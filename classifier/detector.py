from __future__ import annotations

import os
from pathlib import Path

import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision


DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "models" / "hand_landmarker.task"


def enhance_dark(bgr: np.ndarray) -> np.ndarray:
    """CLAHE on the L channel of LAB — brightens shadows without blowing out
    highlights. Cheap and preserves color. Used when a frame is dark enough
    that the detector is likely to miss hands (model_improvement.md: normalize
    input carefully)."""
    import cv2
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)


class HandDetector:
    """MediaPipe hand landmarker wrapper.

    Supports VIDEO running mode (temporal continuity for tracker) and separate
    confidence gates for detection/presence/tracking (model_improvement.md).
    """

    def __init__(
        self,
        model_path: str | os.PathLike | None = None,
        num_hands: int = 2,
        min_detection: float = 0.3,
        min_presence: float = 0.3,
        min_tracking: float = 0.3,
        video_mode: bool = True,
    ):
        path = Path(model_path) if model_path else DEFAULT_MODEL
        if not path.exists():
            raise FileNotFoundError(f"hand_landmarker model not found: {path}")
        self.video_mode = video_mode
        mode = mp_vision.RunningMode.VIDEO if video_mode else mp_vision.RunningMode.IMAGE
        opts = mp_vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(path)),
            running_mode=mode,
            num_hands=num_hands,
            min_hand_detection_confidence=min_detection,
            min_hand_presence_confidence=min_presence,
            min_tracking_confidence=min_tracking,
        )
        self._landmarker = mp_vision.HandLandmarker.create_from_options(opts)

    def detect(self, bgr: np.ndarray, timestamp_ms: int | None = None):
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        if self.video_mode:
            if timestamp_ms is None:
                raise ValueError("timestamp_ms is required in VIDEO mode")
            return self._landmarker.detect_for_video(mp_image, int(timestamp_ms))
        return self._landmarker.detect(mp_image)

    def close(self):
        self._landmarker.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class DisagreementEnsemble:
    """Primary strict detector + secondary permissive detector.

    Frames where the two disagree are strong hard-frame candidates (occluded,
    dexterous, borderline). model_improvement.md recommends comparing outputs
    across models rather than trusting a single one.
    """

    def __init__(
        self,
        model_path: str | os.PathLike | None = None,
        primary_conf: float = 0.5,
        secondary_conf: float = 0.15,
        video_mode: bool = True,
    ):
        self.primary = HandDetector(
            model_path=model_path,
            min_detection=primary_conf,
            min_presence=primary_conf,
            min_tracking=primary_conf,
            video_mode=video_mode,
        )
        self.secondary = HandDetector(
            model_path=model_path,
            min_detection=secondary_conf,
            min_presence=secondary_conf,
            min_tracking=secondary_conf,
            video_mode=video_mode,
        )

    def detect(self, bgr: np.ndarray, timestamp_ms: int | None = None):
        r1 = self.primary.detect(bgr, timestamp_ms)
        r2 = self.secondary.detect(bgr, timestamp_ms)
        n1 = len(r1.hand_landmarks) if r1.hand_landmarks else 0
        n2 = len(r2.hand_landmarks) if r2.hand_landmarks else 0
        # secondary detects more than primary => borderline hard frame
        disagreement = float(max(0, n2 - n1)) / 2.0
        return r1, r2, disagreement, n1, n2

    def close(self):
        self.primary.close()
        self.secondary.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
