from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import cv2
import numpy as np


@dataclass
class HandFeatures:
    handedness: str
    handedness_score: float
    bbox: tuple[float, float, float, float]  # x1,y1,x2,y2 normalised [0,1]
    landmarks: list[tuple[float, float, float]]
    out_of_frame_landmarks: int
    border_clipping: float
    articulation: float
    finger_spread: float


@dataclass
class ImageStats:
    mean_luminance: float
    p10_luminance: float
    contrast: float
    blur: float
    histogram_spread: float


@dataclass
class FrameFeatures:
    image: ImageStats
    hands: list[HandFeatures] = field(default_factory=list)


LM_FINGERTIPS = (4, 8, 12, 16, 20)
LM_MCP = (5, 9, 13, 17)
LM_WRIST = 0


def image_stats(bgr: np.ndarray) -> ImageStats:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    lum = gray.astype(np.float32) / 255.0
    mean_l = float(lum.mean())
    p10 = float(np.percentile(lum, 10))
    contrast = float(lum.std())
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    hist = cv2.calcHist([gray], [0], None, [32], [0, 256]).flatten()
    hist = hist / (hist.sum() + 1e-9)
    hist_spread = float(-(hist * np.log(hist + 1e-9)).sum() / np.log(32))
    return ImageStats(mean_l, p10, contrast, blur, hist_spread)


def _bbox_from_landmarks(lms: Sequence[tuple[float, float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in lms]
    ys = [p[1] for p in lms]
    return (min(xs), min(ys), max(xs), max(ys))


def _border_clipping(bbox: tuple[float, float, float, float], margin: float = 0.02) -> float:
    x1, y1, x2, y2 = bbox
    sides = 0
    if x1 < margin:
        sides += 1
    if y1 < margin:
        sides += 1
    if x2 > 1 - margin:
        sides += 1
    if y2 > 1 - margin:
        sides += 1
    return sides / 4.0


def _out_of_frame(lms: Sequence[tuple[float, float, float]]) -> int:
    count = 0
    for x, y, _ in lms:
        if x < 0 or x > 1 or y < 0 or y > 1:
            count += 1
    return count


def _articulation(lms: Sequence[tuple[float, float, float]]) -> float:
    """Sum of joint bend angles across fingers. Higher = more articulated pose."""
    finger_chains = [
        (0, 1, 2, 3, 4),
        (0, 5, 6, 7, 8),
        (0, 9, 10, 11, 12),
        (0, 13, 14, 15, 16),
        (0, 17, 18, 19, 20),
    ]
    total = 0.0
    for chain in finger_chains:
        pts = [np.array(lms[i][:2]) for i in chain]
        for i in range(1, len(pts) - 1):
            v1 = pts[i - 1] - pts[i]
            v2 = pts[i + 1] - pts[i]
            n1 = np.linalg.norm(v1)
            n2 = np.linalg.norm(v2)
            if n1 < 1e-6 or n2 < 1e-6:
                continue
            cos = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
            angle = np.arccos(cos)
            bend = np.pi - angle
            total += bend
    max_bend = np.pi * 3 * len(finger_chains)
    return float(total / max_bend)


def _finger_spread(lms: Sequence[tuple[float, float, float]]) -> float:
    tips = [np.array(lms[i][:2]) for i in LM_FINGERTIPS]
    dists = []
    for i in range(len(tips) - 1):
        dists.append(float(np.linalg.norm(tips[i] - tips[i + 1])))
    if not dists:
        return 0.0
    return float(np.std(dists) / (np.mean(dists) + 1e-6))


def hand_features(
    handedness_label: str,
    handedness_score: float,
    landmarks: Sequence[tuple[float, float, float]],
) -> HandFeatures:
    bbox = _bbox_from_landmarks(landmarks)
    return HandFeatures(
        handedness=handedness_label,
        handedness_score=handedness_score,
        bbox=bbox,
        landmarks=list(landmarks),
        out_of_frame_landmarks=_out_of_frame(landmarks),
        border_clipping=_border_clipping(bbox),
        articulation=_articulation(landmarks),
        finger_spread=_finger_spread(landmarks),
    )


def bbox_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0
