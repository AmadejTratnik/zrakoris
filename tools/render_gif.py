"""session.json -> animated GIF. Standalone, run at the end of the day.

Must not import PyQt or open a camera.

    python tools/render_gif.py output/2026-09-12/0001_marek/session.json \
        --width 600 --fps 20 --speed 1.5 --hold 1.5 --out drawing.gif

    python tools/render_gif.py --all output/2026-09-12     # whole day

The third element of each stroke point is seconds since that stroke started,
which is what lets the replay run at true speed rather than a uniform rate.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _flatten_timeline(strokes: list[dict]) -> list[tuple[float, tuple, tuple, str, int]]:
    """Return (absolute_t, p0, p1, color, width) segments in play order.

    Each stroke's own point times restart at 0, so strokes are laid end to end
    with a small gap; this keeps the replay readable without needing wall-clock
    timestamps in the file.
    """
    segs: list[tuple[float, tuple, tuple, str, int]] = []
    clock = 0.0
    for s in strokes:
        pts = s["points"]
        color, width = s["color"], int(s["width"])
        if not pts:
            continue
        base = clock
        if len(pts) == 1:
            segs.append((base, (pts[0][0], pts[0][1]), (pts[0][0], pts[0][1]), color, width))
        for a, b in zip(pts, pts[1:]):
            segs.append((base + b[2], (a[0], a[1]), (b[0], b[1]), color, width))
        clock = base + pts[-1][2] + 0.15
    return segs


def render(session_json: Path, width: int, fps: int, speed: float,
           hold: float, out: Path) -> None:
    data = json.loads(session_json.read_text())
    cv = data["canvas"]
    cw, ch = int(cv["w"]), int(cv["h"])
    scale = width / cw
    ow, oh = width, max(1, round(ch * scale))
    bg = _hex(cv.get("background", "#ffffff"))

    segs = _flatten_timeline(data["strokes"])
    total = segs[-1][0] if segs else 0.0
    duration = total / max(speed, 1e-6)
    n_frames = max(1, math.ceil(duration * fps))

    canvas = Image.new("RGB", (ow, oh), bg)
    draw = ImageDraw.Draw(canvas)
    frames: list[np.ndarray] = []
    si = 0
    for f in range(n_frames + 1):
        play_t = (f / fps) * speed
        while si < len(segs) and segs[si][0] <= play_t:
            _t, p0, p1, color, w = segs[si]
            xy = [(p0[0] * scale, p0[1] * scale), (p1[0] * scale, p1[1] * scale)]
            lw = max(1, round(w * scale))
            draw.line(xy, fill=_hex(color), width=lw, joint="curve")
            r = lw / 2
            for (px, py) in xy:
                draw.ellipse([px - r, py - r, px + r, py + r], fill=_hex(color))
            si += 1
        frames.append(np.asarray(canvas).copy())

    for _ in range(max(1, round(hold * fps))):
        frames.append(np.asarray(canvas).copy())

    imageio.mimsave(out, frames, format="GIF",
                    duration=1000.0 / fps, palettesize=64, loop=0)
    print(f"wrote {out} ({len(frames)} frames, {ow}x{oh})")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("target", help="a session.json, or a day directory with --all")
    p.add_argument("--all", action="store_true", help="walk a day directory")
    p.add_argument("--width", type=int, default=600)
    p.add_argument("--fps", type=int, default=20)
    p.add_argument("--speed", type=float, default=1.5)
    p.add_argument("--hold", type=float, default=1.5)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    target = Path(args.target)
    if args.all:
        sessions = sorted(target.glob("*/session.json"))
        if not sessions:
            print(f"no session.json files under {target}")
            return 1
        for sj in sessions:
            render(sj, args.width, args.fps, args.speed, args.hold,
                   sj.with_name("drawing.gif"))
        return 0

    out = Path(args.out) if args.out else target.with_name("drawing.gif")
    render(target, args.width, args.fps, args.speed, args.hold, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
