from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterator

import cv2
import numpy as np

log = logging.getLogger("classifier.sampler")


@dataclass(frozen=True)
class Sample:
    frame_index: int
    timestamp_s: float
    reason: str  # "uniform" | "hand_event" | "confidence_drop" | "transition_window"
    sample_rate_used: float
    event_id: int | None = None


@dataclass
class SamplerConfig:
    base_fps: float = 1.0
    event_fps: float = 5.0
    dense_fps: float = 10.0
    context_before_s: float = 3.0
    context_after_s: float = 3.0
    max_frames: int = 0  # 0 = no cap
    scan_fps: float = 1.0  # pass 1 scan rate
    presence_threshold: float = 0.5  # scan-time hand-presence cutoff


@dataclass
class ScanRow:
    frame_index: int
    timestamp_s: float
    presence: float  # cheap hand-likelihood proxy from pass 1
    brightness: float
    motion: float


class HybridSampler:
    """Two-pass sampler per sampling.md.

    Pass 1: coarse scan (~1 fps) collects presence + brightness + motion signals
    for the whole clip. Presence uses a cheap skin/edge proxy (no ML) so pass 1
    stays fast.

    Pass 2: builds a sorted list of Samples using:
      - uniform base_fps everywhere,
      - event_fps within ±context windows around presence rises/drops,
      - dense_fps around sharp confidence/brightness/motion transitions,
    then dedupes by frame index and applies max_frames cap.
    """

    def __init__(self, cfg: SamplerConfig | None = None):
        self.cfg = cfg or SamplerConfig()

    def scan(self, video_path: str) -> tuple[list[ScanRow], float, int]:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"could not open video: {video_path}")
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            step = max(1, int(round(fps / max(self.cfg.scan_fps, 0.01))))
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            rows: list[ScanRow] = []
            prev_gray: np.ndarray | None = None
            i = 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                if i % step == 0:
                    small = cv2.resize(frame, (160, 90))
                    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                    brightness = float(gray.mean()) / 255.0
                    motion = 0.0
                    if prev_gray is not None:
                        motion = float(np.mean(cv2.absdiff(gray, prev_gray))) / 255.0
                    prev_gray = gray
                    presence = _skin_presence(small)
                    rows.append(ScanRow(i, i / fps, presence, brightness, motion))
                i += 1
            return rows, fps, total
        finally:
            cap.release()

    def plan(self, video_path: str) -> tuple[list[Sample], float, int]:
        rows, fps, total = self.scan(video_path)
        cfg = self.cfg
        base_step = max(1, int(round(fps / max(cfg.base_fps, 0.01))))
        event_step = max(1, int(round(fps / max(cfg.event_fps, 0.01))))
        dense_step = max(1, int(round(fps / max(cfg.dense_fps, 0.01))))

        events = _detect_events(rows, cfg.presence_threshold)

        samples: dict[int, Sample] = {}
        # uniform
        for f in range(0, total, base_step):
            samples[f] = Sample(f, f / fps, "uniform", cfg.base_fps)

        # event windows
        for eid, (etype, ts) in enumerate(events):
            start = max(0, int((ts - cfg.context_before_s) * fps))
            end = min(total, int((ts + cfg.context_after_s) * fps))
            step = dense_step if etype == "confidence_drop" else event_step
            reason = "confidence_drop" if etype == "confidence_drop" else "hand_event"
            for f in range(start, end, step):
                # transition window frames sit on the edges; center frames are "hand_event"
                r = reason
                if abs(f / fps - ts) < 0.4:
                    r = reason
                elif etype != "confidence_drop":
                    r = "transition_window"
                cur = samples.get(f)
                # promote to higher-priority reason if already sampled
                if cur is None or _reason_priority(r) > _reason_priority(cur.reason):
                    used_rate = cfg.dense_fps if step == dense_step else cfg.event_fps
                    samples[f] = Sample(f, f / fps, r, used_rate, eid)

        ordered = sorted(samples.values(), key=lambda s: s.frame_index)

        if cfg.max_frames and len(ordered) > cfg.max_frames:
            # keep every hard-event sample, thin uniform ones
            hard = [s for s in ordered if s.reason != "uniform"]
            uniform = [s for s in ordered if s.reason == "uniform"]
            budget = max(0, cfg.max_frames - len(hard))
            if budget < len(uniform):
                idx = np.linspace(0, len(uniform) - 1, budget).round().astype(int) if budget else []
                uniform = [uniform[i] for i in idx]
            ordered = sorted(hard + uniform, key=lambda s: s.frame_index)
            if len(ordered) > cfg.max_frames:
                ordered = ordered[: cfg.max_frames]

        log.info(
            "sampler: total=%d fps=%.1f events=%d planned=%d uniform=%d event=%d dense=%d",
            total,
            fps,
            len(events),
            len(ordered),
            sum(1 for s in ordered if s.reason == "uniform"),
            sum(1 for s in ordered if s.reason in ("hand_event", "transition_window")),
            sum(1 for s in ordered if s.reason == "confidence_drop"),
        )
        return ordered, fps, total


_REASON_PRIO = {
    "uniform": 0,
    "transition_window": 1,
    "hand_event": 2,
    "confidence_drop": 3,
}


def _reason_priority(r: str) -> int:
    return _REASON_PRIO.get(r, 0)


def _skin_presence(bgr: np.ndarray) -> float:
    """Very cheap skin-tone mask fraction. NOT a hand detector — a proposal
    generator for pass 1 so we know where to look. Egocentric hands span
    a wide skin-tone range; use HSV + YCrCb combined mask."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    m1 = cv2.inRange(hsv, (0, 30, 60), (25, 180, 255))
    m2 = cv2.inRange(hsv, (160, 30, 60), (179, 180, 255))
    m3 = cv2.inRange(ycrcb, (0, 133, 77), (255, 173, 127))
    mask = cv2.bitwise_or(cv2.bitwise_or(m1, m2), m3)
    return float(mask.mean()) / 255.0


def _detect_events(rows: list[ScanRow], presence_thr: float) -> list[tuple[str, float]]:
    """Return (event_type, timestamp) tuples ordered by time."""
    events: list[tuple[str, float]] = []
    if not rows:
        return events
    prev_pres = rows[0].presence >= presence_thr
    prev_bright = rows[0].brightness
    prev_motion = rows[0].motion
    for r in rows[1:]:
        cur_pres = r.presence >= presence_thr
        if cur_pres != prev_pres:
            events.append(("hand_event", r.timestamp_s))
        if abs(r.brightness - prev_bright) > 0.15:
            events.append(("confidence_drop", r.timestamp_s))
        if r.motion - prev_motion > 0.05:
            events.append(("hand_event", r.timestamp_s))
        prev_pres = cur_pres
        prev_bright = r.brightness
        prev_motion = r.motion
    return events


def iter_planned_frames(video_path: str, plan: list[Sample]) -> Iterator[tuple[Sample, np.ndarray]]:
    """Yield (sample, frame_bgr) in order, seeking as needed."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open video: {video_path}")
    try:
        wanted = sorted({s.frame_index for s in plan})
        plan_by_idx = {s.frame_index: s for s in plan}
        wanted_set = set(wanted)
        i = 0
        # sequential read is much faster than repeated seeks for close-together frames
        target_iter = iter(wanted)
        try:
            next_target = next(target_iter)
        except StopIteration:
            return
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if i == next_target:
                yield plan_by_idx[i], frame
                try:
                    next_target = next(target_iter)
                except StopIteration:
                    return
                # skip ahead if next target is far away
                gap = next_target - i - 1
                if gap > 30:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, next_target)
                    i = next_target - 1
            elif i > next_target:
                # missed target — shouldn't happen, but guard against it
                if next_target in wanted_set:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, next_target)
                    i = next_target - 1
            i += 1
    finally:
        cap.release()
