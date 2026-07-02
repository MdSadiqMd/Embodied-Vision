from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .scoring import FrameClassifier, FrameResult
from .detector import enhance_dark
from .features import bbox_iou, hand_features, image_stats, FrameFeatures, HandFeatures
from .sampler import HybridSampler, SamplerConfig, iter_planned_frames

log = logging.getLogger("classifier.video")


@dataclass
class VideoStats:
    video_path: str
    total_frames_seen: int
    sampled_frames: int
    fps: float
    duration_s: float
    label_counts: dict[str, int] = field(default_factory=dict)
    reason_counts: dict[str, int] = field(default_factory=dict)


def _hands_from_result(result) -> list[HandFeatures]:
    hands: list[HandFeatures] = []
    handedness = result.handedness if result.handedness else []
    landmarks = result.hand_landmarks if result.hand_landmarks else []
    for hd, lms in zip(handedness, landmarks):
        if not hd or not lms:
            continue
        hands.append(hand_features(hd[0].category_name, float(hd[0].score), [(lm.x, lm.y, lm.z) for lm in lms]))
    return hands


@dataclass
class _Record:
    sample: object
    feats: FrameFeatures
    local_strength: float
    disagreement: float
    n_primary: int
    n_secondary: int
    best_box: tuple[float, float, float, float] | None
    jpeg: bytes
    used_clahe: bool


class VideoClassifier:
    """Two-pass per video:
      Pass 1  detect on every planned frame, cache local signals + encoded JPEG.
      Pass 2  for each frame compute bidirectional track support (strongest
              local_strength among neighbours within +-temporal_window_s) and
              classify. Track support is what separates occluded from no_hands.
    """

    def __init__(
        self,
        classifier: FrameClassifier,
        sampler: HybridSampler | None = None,
        write_frames_dir: str | None = None,
        temporal_window_s: float = 2.0,
        dark_lum_for_clahe: float = 0.32,
    ):
        self.classifier = classifier
        self.sampler = sampler or HybridSampler(SamplerConfig())
        self.temporal_window_s = temporal_window_s
        self.dark_lum_for_clahe = dark_lum_for_clahe
        self.write_frames_dir = Path(write_frames_dir) if write_frames_dir else None
        if self.write_frames_dir:
            self.write_frames_dir.mkdir(parents=True, exist_ok=True)
            for lbl in ("no_hands", "low_lighting", "occluded", "dexterous_pose", "easy"):
                (self.write_frames_dir / lbl).mkdir(parents=True, exist_ok=True)

    def _detect_pass(self, video_path: str, plan) -> list[_Record]:
        records: list[_Record] = []
        for sample, frame in iter_planned_frames(video_path, plan):
            ts_ms = int(sample.timestamp_s * 1000)
            stats = image_stats(frame)
            det_input, used_clahe = frame, False
            if stats.mean_luminance < self.dark_lum_for_clahe:
                det_input, used_clahe = enhance_dark(frame), True
            r_primary, r_secondary, disagreement, n_primary, n_secondary = self.classifier.detect_ensemble(det_input, ts_ms)
            primary_hands = _hands_from_result(r_primary)
            secondary_hands = _hands_from_result(r_secondary)
            merged = list(primary_hands)
            for sh in secondary_hands:
                if all(bbox_iou(sh.bbox, ph.bbox) < 0.3 for ph in primary_hands):
                    merged.append(sh)
            feats = FrameFeatures(image=stats, hands=merged)
            local_strength = self.classifier.local_strength(feats, n_primary, n_secondary)
            best_box = None
            if primary_hands:
                best_box = max(primary_hands, key=lambda h: h.handedness_score).bbox
            elif merged:
                best_box = merged[0].bbox
            ok, buf = cv2.imencode(".jpg", frame)
            records.append(_Record(
                sample=sample, feats=feats, local_strength=local_strength,
                disagreement=disagreement, n_primary=n_primary, n_secondary=n_secondary,
                best_box=best_box, jpeg=buf.tobytes() if ok else b"", used_clahe=used_clahe,
            ))
        return records

    def _track_support(self, records: list[_Record], i: int, conf_thr: float) -> tuple[float, float]:
        """Returns (track_support, nearest_conf_dt):
          track_support   = strongest neighbour local_strength within +-window
          nearest_conf_dt = seconds to the nearest neighbour whose local_strength
                            clears conf_thr (a confident anchor). Used for
                            short-gap occlusion bridging."""
        t = records[i].sample.timestamp_s
        support = 0.0
        nearest_dt = 1e9
        for j in range(i - 1, -1, -1):
            dt = t - records[j].sample.timestamp_s
            if dt > self.temporal_window_s:
                break
            support = max(support, records[j].local_strength)
            if records[j].local_strength >= conf_thr:
                nearest_dt = min(nearest_dt, dt)
        for j in range(i + 1, len(records)):
            dt = records[j].sample.timestamp_s - t
            if dt > self.temporal_window_s:
                break
            support = max(support, records[j].local_strength)
            if records[j].local_strength >= conf_thr:
                nearest_dt = min(nearest_dt, dt)
        return support, nearest_dt

    def process(self, video_path: str) -> tuple[list[FrameResult], VideoStats]:
        plan, fps, total = self.sampler.plan(video_path)
        duration = total / fps if fps else 0.0

        records = self._detect_pass(video_path, plan)

        results: list[FrameResult] = []
        counts: dict[str, int] = {}
        reason_counts: dict[str, int] = {}

        conf_thr = self.classifier.cfg["track_strong"]
        for i, rec in enumerate(records):
            feats = rec.feats
            track_support, nearest_conf_dt = self._track_support(records, i, conf_thr)

            jitter = 0.0
            if rec.best_box is not None:
                ious = []
                for j in (i - 1, i + 1):
                    if 0 <= j < len(records) and records[j].best_box is not None:
                        if abs(records[j].sample.timestamp_s - rec.sample.timestamp_s) <= self.temporal_window_s:
                            ious.append(bbox_iou(rec.best_box, records[j].best_box))
                if ious:
                    jitter = float(np.clip(1.0 - np.mean(ious), 0.0, 1.0))

            label, scores, ev = self.classifier.classify(
                feats,
                local_strength=rec.local_strength,
                track_support=track_support,
                nearest_conf_dt=nearest_conf_dt,
                model_disagreement=rec.disagreement,
                jitter=jitter,
                primary_count=rec.n_primary,
                secondary_count=rec.n_secondary,
            )

            feat_dict = {
                "mean_luminance": round(feats.image.mean_luminance, 3),
                "p10_luminance": round(feats.image.p10_luminance, 3),
                "contrast": round(feats.image.contrast, 3),
                "blur": round(feats.image.blur, 1),
                "hands": [
                    {
                        "handedness": h.handedness,
                        "score": round(h.handedness_score, 3),
                        "bbox": [round(v, 3) for v in h.bbox],
                        "articulation": round(h.articulation, 3),
                        "finger_spread": round(h.finger_spread, 3),
                        "out_of_frame": h.out_of_frame_landmarks,
                        "border_clipping": round(h.border_clipping, 3),
                    }
                    for h in feats.hands
                ],
                "local_strength": round(rec.local_strength, 3),
                "track_support": round(track_support, 3),
                "nearest_conf_dt": round(nearest_conf_dt, 3) if nearest_conf_dt < 1e8 else None,
                "jitter": round(jitter, 3),
                "model_disagreement": round(rec.disagreement, 3),
                "primary_hands": rec.n_primary,
                "secondary_hands": rec.n_secondary,
                "clahe_applied": rec.used_clahe,
                "sample_reason": rec.sample.reason,
                "sample_rate_used": rec.sample.sample_rate_used,
                "event_id": rec.sample.event_id,
            }
            results.append(FrameResult(
                frame_index=rec.sample.frame_index,
                timestamp_s=rec.sample.timestamp_s,
                label=label,
                scores=scores,
                hand_evidence=ev,
                num_hands=len(feats.hands),
                features=feat_dict,
            ))
            counts[label] = counts.get(label, 0) + 1
            reason_counts[rec.sample.reason] = reason_counts.get(rec.sample.reason, 0) + 1
            if self.write_frames_dir and rec.jpeg:
                stem = Path(video_path).stem
                out = self.write_frames_dir / label / f"{stem}_{rec.sample.frame_index:07d}_{rec.sample.reason}.jpg"
                out.write_bytes(rec.jpeg)

        log.info("classified %d frames | labels=%s", len(results), counts)
        stats = VideoStats(
            video_path=video_path,
            total_frames_seen=total,
            sampled_frames=len(results),
            fps=fps,
            duration_s=duration,
            label_counts=counts,
            reason_counts=reason_counts,
        )
        return results, stats
