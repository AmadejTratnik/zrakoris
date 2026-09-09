"""M2 tuning harness: run WandTracker against a recorded clip (or camera) and
overlay the detection. No Qt.

    python tools/replay_overlay.py venue_clip.mp4
    python tools/replay_overlay.py venue_clip.mp4 --config venue.json
    python tools/replay_overlay.py --camera            # live, using config.json

Keys:  space = pause/step   [ / ] = step back/forward while paused   q = quit
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config as configmod          # noqa: E402
from core.tracker import WandTracker           # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("video", nargs="?", help="path to a recorded clip")
    p.add_argument("--camera", action="store_true", help="use the camera instead")
    p.add_argument("--config", default="config.json")
    args = p.parse_args(argv)

    cfg = configmod.load(args.config)
    tracker = WandTracker(cfg)
    roi = cfg["roi"]

    if args.camera:
        cap = cv2.VideoCapture(int(cfg["camera"]["index"]))
    elif args.video:
        cap = cv2.VideoCapture(args.video)
    else:
        p.error("give a video path or --camera")

    if not cap.isOpened():
        print("ERROR: could not open source")
        return 1

    paused = False
    frames: list = []
    idx = 0
    while True:
        if not paused:
            ok, frame = cap.read()
            if not ok:
                if not args.camera:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    tracker.reset()
                    continue
                break
            frame = cv2.flip(frame, 1)
            frames.append(frame)
            idx = len(frames) - 1
        frame = frames[idx]

        det = tracker.process(frame.copy(), time.monotonic())
        vis = frame.copy()
        cv2.rectangle(vis, (roi["x"], roi["y"]),
                      (roi["x"] + roi["w"], roi["y"] + roi["h"]), (255, 255, 0), 1)
        if det.raw is not None:
            cx, cy = int(det.raw[0] + roi["x"]), int(det.raw[1] + roi["y"])
            colour = {"draw": (0, 255, 0), "hover": (0, 0, 255)}.get(det.state, (128, 128, 128))
            cv2.circle(vis, (cx, cy), 10, colour, 2)
        txt = f"state={det.state} pos={_fmt(det.pos)} area={det.area:.0f} frame={idx}"
        cv2.putText(vis, txt, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.imshow("replay_overlay", vis)

        key = cv2.waitKey(0 if paused else 20) & 0xFF
        if key == ord("q"):
            break
        if key == ord(" "):
            paused = not paused
        if paused and key == ord("]"):
            idx = min(idx + 1, len(frames) - 1)
        if paused and key == ord("["):
            idx = max(idx - 1, 0)

    cap.release()
    cv2.destroyAllWindows()
    return 0


def _fmt(p) -> str:
    return "-" if p is None else f"({p[0]:.0f},{p[1]:.0f})"


if __name__ == "__main__":
    raise SystemExit(main())
