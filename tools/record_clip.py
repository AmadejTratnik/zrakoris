"""Capture raw video at the venue for offline tuning, and verify the camera.

    python tools/record_clip.py --check              # M1: verify manual exposure
    python tools/record_clip.py --seconds 300 --out venue_clip.mp4

``--check`` prints the requested vs actual value for exposure, auto-exposure and
auto-WB, then shows a live preview with the HSV value at the brightest blob
overlaid. Point the wand at the camera: red should read hue near 0 or 179 with
S above ~120; the button should jump it to hue ~50-75. If S reads below ~40 the
exposure is too high — lower it, do not lower the S floor.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.json"


def _load_cam_cfg(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())["camera"]
    return {"index": 0, "width": 640, "height": 480, "fps": 30,
            "exposure": 20, "auto_exposure": 0.25, "auto_wb": 0}


_IS_WIN = sys.platform.startswith("win")


def manual_ae_value() -> float:
    """The 'manual exposure' magic number for this platform's backend.

    Windows/DirectShow uses 0.25. Linux/V4L2 uses 1 (3 = auto). macOS varies.
    """
    return 0.25 if _IS_WIN else 1.0


def auto_ae_value() -> float:
    return 0.75 if _IS_WIN else 3.0


def apply_props(cap: cv2.VideoCapture, cam: dict) -> None:
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam["width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam["height"])
    cap.set(cv2.CAP_PROP_FPS, cam["fps"])
    # AUTO_EXPOSURE must be set to "manual" before EXPOSURE takes effect, and on
    # some drivers it must be set again *after*.
    manual = manual_ae_value() if float(cam.get("auto_exposure", 0)) not in (0.75, 3.0) else auto_ae_value()
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, manual)
    cap.set(cv2.CAP_PROP_EXPOSURE, cam["exposure"])
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, manual)
    cap.set(cv2.CAP_PROP_AUTO_WB, cam["auto_wb"])
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)


def open_camera(cam: dict) -> cv2.VideoCapture:
    backend = cv2.CAP_DSHOW if _IS_WIN else cv2.CAP_ANY
    cap = cv2.VideoCapture(int(cam["index"]), backend)
    apply_props(cap, cam)
    return cap


def _report(cap: cv2.VideoCapture, cam: dict) -> None:
    checks = [
        ("exposure", cv2.CAP_PROP_EXPOSURE, cam["exposure"]),
        ("auto_exposure", cv2.CAP_PROP_AUTO_EXPOSURE, cam["auto_exposure"]),
        ("auto_wb", cv2.CAP_PROP_AUTO_WB, cam["auto_wb"]),
    ]
    print(f"{'property':<16} {'requested':>10} {'actual':>12}")
    for name, prop, requested in checks:
        print(f"{name:<16} {requested:>10} {cap.get(prop):>12.3f}")
    ae = cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)
    print()
    if not _IS_WIN:
        if ae == 1.0:
            print("auto_exposure actual = 1  -> MANUAL exposure is active (good, V4L2).")
        elif ae == 3.0:
            print("auto_exposure actual = 3  -> auto-exposure is STILL ON. The room will")
            print("  not go dark and hue matching will be unreliable. This build now")
            print("  requests the V4L2 'manual' value (1); if it still reads 3 the driver")
            print("  is refusing it -- check `v4l2-ctl -d /dev/video0 --list-ctrls`.")
        print("On V4L2, CAP_PROP_EXPOSURE is exposure_time_absolute (~100us units,")
        print("  typically 1-5000), NOT the -6..-10 log scale from the Windows spec.")
        print("  Use the +/- keys below to find a value where only the LED is visible.")
    print()


def _brightest_blob_hsv(frame: np.ndarray) -> tuple[tuple[int, int, int], tuple[int, int]] | None:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (9, 9), 0)
    _minv, maxv, _minl, maxl = cv2.minMaxLoc(gray)
    if maxv < 60:
        return None
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[maxl[1], maxl[0]]
    return (int(h), int(s), int(v)), maxl


_EXPOSURE_STEPS = [1, 2, 3, 5, 8, 10, 15, 20, 30, 40, 60, 80, 100, 150, 200,
                   300, 400, 600, 800, 1200, 1600, 2500, 5000]


def _nudge_exposure(current: float, up: bool) -> float:
    """Move to the next value on a rough log ladder (works for V4L2 units)."""
    if _IS_WIN:
        return current + (1 if up else -1)
    steps = _EXPOSURE_STEPS
    idx = min(range(len(steps)), key=lambda i: abs(steps[i] - current))
    idx = max(0, min(len(steps) - 1, idx + (1 if up else -1)))
    return float(steps[idx])


def check(cam: dict) -> int:
    cap = open_camera(cam)
    if not cap.isOpened():
        print("ERROR: could not open camera")
        return 1
    _report(cap, cam)
    exposure = float(cam["exposure"])
    auto = False
    print("Live preview keys:  q quit   +/- exposure   a toggle auto-exposure")
    print("Aim for: room nearly black, LED bright and coloured, S >= ~120 on red.")
    print("When it looks right, copy the printed exposure value into config.json.\n")

    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        frame = cv2.flip(frame, 1)
        hit = _brightest_blob_hsv(frame)
        if hit:
            (h, s, v), (x, y) = hit
            colour = (0, 255, 0) if s >= 120 else (0, 0, 255)
            cv2.circle(frame, (x, y), 12, colour, 2)
            cv2.putText(frame, f"H={h} S={s} V={v}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, colour, 2)
            if s < 40:
                cv2.putText(frame, "SATURATING (white) - lower exposure", (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        mode = "AUTO" if auto else f"manual exp={exposure:g}"
        cv2.putText(frame, mode, (10, frame.shape[0] - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.imshow("record_clip --check", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("a"):
            auto = not auto
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, auto_ae_value() if auto else manual_ae_value())
            if not auto:
                cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
            print(f"auto-exposure {'ON' if auto else 'OFF'}  "
                  f"(actual={cap.get(cv2.CAP_PROP_AUTO_EXPOSURE):g})")
        if key in (ord("+"), ord("="), ord("-"), ord("_")) and not auto:
            exposure = _nudge_exposure(exposure, up=key in (ord("+"), ord("=")))
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, manual_ae_value())
            cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
            print(f'  "exposure": {exposure:g}   (actual={cap.get(cv2.CAP_PROP_EXPOSURE):g})')

    cap.release()
    cv2.destroyAllWindows()
    return 0


def record(cam: dict, seconds: int, out: str) -> int:
    cap = open_camera(cam)
    if not cap.isOpened():
        print("ERROR: could not open camera")
        return 1
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cam["fps"]
    writer = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    print(f"Recording {seconds}s to {out} ({w}x{h} @ {fps}fps). Press q to stop early.")
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        ok, frame = cap.read()
        if not ok:
            continue
        writer.write(frame)
        cv2.imshow("recording (raw, not flipped)", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    writer.release()
    cap.release()
    cv2.destroyAllWindows()
    print("done")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    p.add_argument("--seconds", type=int, default=300)
    p.add_argument("--out", default="venue_clip.mp4")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = p.parse_args(argv)

    cam = _load_cam_cfg(Path(args.config))
    if args.check:
        return check(cam)
    return record(cam, args.seconds, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
