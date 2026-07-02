from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal

import numpy as np

from .detector import DisagreementEnsemble
from .features import FrameFeatures, bbox_iou


Label = Literal["no_hands", "low_lighting", "occluded", "dexterous_pose", "easy"]


@dataclass
class Scores:
    no_hands: float
    low_lighting: float
    occluded: float
    dexterous_pose: float
    easy: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FrameResult:
    frame_index: int
    timestamp_s: float
    label: Label
    scores: Scores
    hand_evidence: float
    num_hands: int
    features: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "frame_index": self.frame_index,
            "timestamp_s": round(self.timestamp_s, 3),
            "label": self.label,
            "scores": {k: round(v, 3) for k, v in self.scores.as_dict().items()},
            "hand_evidence": round(self.hand_evidence, 3),
            "num_hands": self.num_hands,
            "features": self.features,
        }


# --- Design (Approach 4) -----------------------------------------------------
# The no_hands vs occluded decision is the crux. Research + our own data show a
# single frame cannot separate them: BlazePalm fails ENTIRELY under occlusion,
# so "detector saw nothing" is identical for an empty frame and an occluded
# hand. The only reliable separator is TEMPORAL bracketing — a hand that is
# confidently detected within +-window seconds is physically present even in a
# frame where the detector momentarily lost it. So presence is decided by fusing
#   (a) local detection strength (primary-anchored), and
#   (b) neighbour track support (bidirectional, from the two-pass driver),
# and difficulty is attributed only after presence is established.
# ----------------------------------------------------------------------------
DEFAULTS = dict(
    # presence
    present_local=0.50,          # local detection strength that means "hand clearly here"
    track_strong=0.55,           # neighbour strength that counts as a confident anchor
    bridge_max_s=0.7,            # only interpolate "occluded" across gaps this short
    secondary_local_factor=0.5,  # local strength multiplier when only permissive detector fired

    # low_lighting (dark != absent; dark wins over occluded per classifier.md).
    # Tuned to reproduce output-2's well-liked low_lighting bucket (~19% of
    # frames) — brightness-dominated with a p10 (shadow-depth) term.
    low_light_mean=0.38,
    low_light_p10=0.08,
    low_light_contrast=0.14,
    hard_dark_lum=0.20,          # mean-lum below this is unambiguously underexposed
    dark_thr=0.35,               # low_light score above this => low_lighting
    weights_low=(0.5, 0.25, 0.25),   # brightness_term, p10_term, contrast_term

    # occlusion (structured partial loss / border clip / blur / disagreement)
    weights_occ=(0.34, 0.22, 0.22, 0.22),  # partial-loss, border-clip, blur, low-conf
    disagreement_weight=0.35,
    occ_min=0.30,                # occlusion score needed to label a CLEAR frame occluded

    # dexterity
    weights_dex=(0.4, 0.32, 0.28),   # articulation, spread, self-overlap
    dex_min=0.30,

    # easy — strict residual: every gate must pass
    easy_min_handedness=0.70,
    easy_max_lowlight=0.18,
    easy_max_blur=0.25,          # blur SCORE (0 sharp .. 1 blurry)
    easy_max_occ=0.25,
    easy_max_dex=0.25,

    # blur normalisation (Laplacian variance)
    blur_sharp_cutoff=320.0,     # >= this => fully sharp (blur score 0)
    blur_blurry_cutoff=70.0,     # <= this => fully blurry (blur score 1)
)


def _blur_score(blur: float, cfg: dict) -> float:
    sharp = cfg["blur_sharp_cutoff"]
    blurry = cfg["blur_blurry_cutoff"]
    if blur >= sharp:
        return 0.0
    if blur <= blurry:
        return 1.0
    return float((sharp - blur) / (sharp - blurry))


class FrameClassifier:
    """Two-stage classifier. Stage 1 (presence) fuses local detection with the
    caller-supplied bidirectional temporal track support. Stage 2 attributes the
    dominant difficulty. `easy` is a strict residual."""

    def __init__(self, ensemble: DisagreementEnsemble | None = None, **thresholds):
        self.ensemble = ensemble or DisagreementEnsemble()
        self.cfg = {**DEFAULTS, **thresholds}

    def detect_ensemble(self, bgr, timestamp_ms: int):
        return self.ensemble.detect(bgr, timestamp_ms)

    # --- local signals -------------------------------------------------------
    def local_strength(self, f: FrameFeatures, primary_count: int, secondary_count: int) -> float:
        """Primary-anchored local detection strength in [0,1]. A detection seen
        only by the permissive secondary detector is halved: on its own it is
        weak local evidence and must be corroborated by the temporal track."""
        if not f.hands:
            return 0.0
        best = 0.0
        for h in f.hands:
            box_area = max(0.0, h.bbox[2] - h.bbox[0]) * max(0.0, h.bbox[3] - h.bbox[1])
            size_signal = float(np.clip(box_area / 0.05, 0.0, 1.0))
            completeness = 1.0 - min(1.0, h.out_of_frame_landmarks / 21.0)
            best = max(best, 0.6 * h.handedness_score + 0.25 * completeness + 0.15 * size_signal)
        if primary_count == 0 and secondary_count > 0:
            best *= self.cfg["secondary_local_factor"]
        return float(np.clip(best, 0.0, 1.0))

    def _score_low_light(self, f: FrameFeatures) -> float:
        s = f.image
        w_b, w_p, w_c = self.cfg["weights_low"]
        brightness_term = float(np.clip(1.0 - s.mean_luminance / self.cfg["low_light_mean"], 0.0, 1.0))
        p10_term = 0.0
        if self.cfg["low_light_p10"] > 0:
            p10_term = float(np.clip(1.0 - s.p10_luminance / self.cfg["low_light_p10"], 0.0, 1.0))
        contrast_term = float(np.clip(1.0 - s.contrast / self.cfg["low_light_contrast"], 0.0, 1.0))
        score = w_b * brightness_term + w_p * p10_term + w_c * contrast_term
        # unambiguously underexposed frames get a floor regardless of contrast
        hard_dark = float(np.clip((self.cfg["hard_dark_lum"] - s.mean_luminance) / self.cfg["hard_dark_lum"], 0.0, 1.0))
        return float(np.clip(max(score, hard_dark), 0.0, 1.0))

    def _score_occluded(self, f: FrameFeatures, *, model_disagreement: float) -> float:
        w_partial, w_clip, w_blur, w_lowconf = self.cfg["weights_occ"]
        blur = _blur_score(f.image.blur, self.cfg)
        disag = self.cfg["disagreement_weight"] * model_disagreement
        if not f.hands:
            return float(np.clip(disag, 0.0, 1.0))
        best = 0.0
        for h in f.hands:
            partial_loss = min(1.0, h.out_of_frame_landmarks / 8.0)
            low_conf = float(np.clip(1.0 - h.handedness_score, 0.0, 1.0))
            score = w_partial * partial_loss + w_clip * h.border_clipping + w_blur * blur + w_lowconf * low_conf
            best = max(best, score)
        return float(np.clip(best + disag, 0.0, 1.0))

    def _score_dexterous(self, f: FrameFeatures, jitter: float) -> float:
        if not f.hands:
            return 0.0
        w_art, w_spread, w_ovl = self.cfg["weights_dex"]
        best = 0.0
        for i, h in enumerate(f.hands):
            self_overlap = 0.0
            for j, g in enumerate(f.hands):
                if i != j:
                    self_overlap = max(self_overlap, bbox_iou(h.bbox, g.bbox))
            best = max(best, w_art * h.articulation + w_spread * h.finger_spread + w_ovl * self_overlap + 0.1 * jitter)
        return float(np.clip(best, 0.0, 1.0))

    def _is_easy(self, f: FrameFeatures, primary_count: int, s_low: float, s_occ: float, s_dex: float) -> bool:
        if primary_count < 1 or not f.hands:
            return False
        if s_low > self.cfg["easy_max_lowlight"]:
            return False
        if _blur_score(f.image.blur, self.cfg) > self.cfg["easy_max_blur"]:
            return False
        if s_occ > self.cfg["easy_max_occ"] or s_dex > self.cfg["easy_max_dex"]:
            return False
        best_hd = max(h.handedness_score for h in f.hands)
        complete = min(h.out_of_frame_landmarks for h in f.hands) == 0
        return best_hd >= self.cfg["easy_min_handedness"] and complete

    def classify(
        self,
        features: FrameFeatures,
        *,
        local_strength: float,
        track_support: float,
        nearest_conf_dt: float = 1e9,
        model_disagreement: float = 0.0,
        jitter: float = 0.0,
        primary_count: int = 0,
        secondary_count: int = 0,
    ) -> tuple[Label, Scores, float]:
        """Presence fuses local detection with a SHORT-GAP temporal bridge.
        `nearest_conf_dt` = seconds to the nearest frame with a confident
        (primary-anchored) detection. Track interpolation only bridges gaps up
        to `bridge_max_s`: a single dropped frame between confident detections
        is `occluded`; a long run of nothing is `no_hands`. This replaces the
        noisy per-frame evidence threshold that made the two classes a toss-up.
        `track_support` is kept for reporting/scoring only."""
        s_low = self._score_low_light(features)
        s_occ = self._score_occluded(features, model_disagreement=model_disagreement)
        s_dex = self._score_dexterous(features, jitter)

        local_present = local_strength >= self.cfg["present_local"]
        bridge = self.cfg["bridge_max_s"]
        # short-gap track interpolation: a confident detection within `bridge`
        # seconds means the hand is almost certainly still here even though this
        # frame's detector failed (BlazePalm drops out entirely under occlusion).
        # A per-frame detection hint (permissive hit / disagreement) extends the
        # allowed gap to 2x, since the hand is directly corroborated here.
        has_hint = bool(features.hands) or model_disagreement > 0.0
        short_gap = nearest_conf_dt <= bridge
        hinted_gap = has_hint and nearest_conf_dt <= 2.0 * bridge

        if local_present:
            present, lost = True, False
        elif short_gap or hinted_gap:
            present, lost = True, True   # dropout inside a live track => occluded
        else:
            present, lost = False, False

        s_easy = 0.0 if not present else float(np.clip(local_strength * (1.0 - max(s_low, s_occ, s_dex)), 0.0, 1.0))
        s_nohands = 0.0 if present else float(np.clip(1.0 - max(local_strength, track_support), 0.0, 1.0))
        scores = Scores(s_nohands, s_low, s_occ, s_dex, s_easy)

        if not present:
            return "no_hands", scores, local_strength

        # dark always wins (classifier.md ordering: low_lighting first)
        if s_low >= self.cfg["dark_thr"]:
            return "low_lighting", scores, local_strength

        # hand present but this frame's detection failed and it isn't dark:
        # by construction that is an occlusion / severe degradation
        if lost:
            return "occluded", scores, local_strength

        # clear local detection: dexterous vs occluded vs easy
        if s_dex >= self.cfg["dex_min"] and s_dex >= s_occ:
            return "dexterous_pose", scores, local_strength
        if s_occ >= self.cfg["occ_min"]:
            return "occluded", scores, local_strength
        if self._is_easy(features, primary_count, s_low, s_occ, s_dex):
            return "easy", scores, local_strength
        # clear detection, not dark, not strongly hard, but fails strict easy
        # gate (e.g. slightly blurry / partial): route to nearest hard class
        return ("occluded" if s_occ >= s_dex else "dexterous_pose"), scores, local_strength
