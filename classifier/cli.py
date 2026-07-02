from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from .scoring import FrameClassifier
from .detector import DisagreementEnsemble, DEFAULT_MODEL
from .sampler import HybridSampler, SamplerConfig
from .video import VideoClassifier


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="classify-video")
    p.add_argument("video", help="path to input .mp4")
    p.add_argument("--out", help="output JSON path (default: <video>.classify.json)")
    p.add_argument(
        "--frames-dir",
        "--write-frames",
        dest="frames_dir",
        default=None,
        help="root directory; frames are written under <dir>/<label>/*.jpg",
    )
    p.add_argument("--model", default=str(DEFAULT_MODEL), help="path to hand_landmarker.task")
    p.add_argument("--summary-only", action="store_true", help="omit per-frame features from JSON")
    p.add_argument("--verbose", "-v", action="store_true")

    p.add_argument("--base-fps", type=float, default=1.0, help="uniform sampling rate (fps)")
    p.add_argument("--event-fps", type=float, default=5.0, help="fps within event context windows")
    p.add_argument("--dense-fps", type=float, default=10.0, help="fps at sharp transitions")
    p.add_argument("--context-s", type=float, default=3.0, help="+- seconds around each event")
    p.add_argument("--scan-fps", type=float, default=1.0, help="pass-1 coarse scan rate")
    p.add_argument("--max-frames", type=int, default=0, help="cap on sampled frames per clip")
    p.add_argument("--presence-threshold", type=float, default=0.06, help="pass-1 skin-mask threshold")

    p.add_argument("--primary-conf", type=float, default=0.5, help="strict detector min confidence")
    p.add_argument("--secondary-conf", type=float, default=0.15, help="permissive detector min confidence")
    p.add_argument("--no-video-mode", action="store_true", help="disable temporal VIDEO mode")

    # legacy alias — mapped onto --base-fps
    p.add_argument("--sample-fps", type=float, default=None, help=argparse.SUPPRESS)
    p.add_argument("--min-conf", type=float, default=None, help=argparse.SUPPRESS)

    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    log = logging.getLogger("classifier.cli")

    video = Path(args.video)
    if not video.exists():
        log.error("video not found: %s", video)
        return 2

    out_path = Path(args.out) if args.out else video.with_suffix(video.suffix + ".classify.json")

    base_fps = args.sample_fps if args.sample_fps is not None else args.base_fps
    secondary_conf = args.min_conf if args.min_conf is not None else args.secondary_conf

    log.info("loading model %s", args.model)
    ensemble = DisagreementEnsemble(
        model_path=args.model,
        primary_conf=args.primary_conf,
        secondary_conf=secondary_conf,
        video_mode=not args.no_video_mode,
    )
    fc = FrameClassifier(ensemble=ensemble)

    sampler = HybridSampler(
        SamplerConfig(
            base_fps=base_fps,
            event_fps=args.event_fps,
            dense_fps=args.dense_fps,
            context_before_s=args.context_s,
            context_after_s=args.context_s,
            max_frames=args.max_frames,
            scan_fps=args.scan_fps,
            presence_threshold=args.presence_threshold,
        )
    )

    vc = VideoClassifier(
        classifier=fc,
        sampler=sampler,
        write_frames_dir=args.frames_dir,
    )

    log.info("processing %s (base_fps=%.2f event_fps=%.2f dense_fps=%.2f)", video, base_fps, args.event_fps, args.dense_fps)
    results, stats = vc.process(str(video))
    ensemble.close()

    payload = {
        "video": str(video),
        "stats": asdict(stats),
        "frames": [
            {k: v for k, v in r.to_dict().items() if k != "features" or not args.summary_only}
            for r in results
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2))
    log.info(
        "wrote %s | sampled=%d labels=%s reasons=%s",
        out_path,
        stats.sampled_frames,
        stats.label_counts,
        stats.reason_counts,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
