"""Load, validate and save ``config.json``.

The config is a plain nested dict everywhere else in the app. This module only
adds: a loader with sensible errors, a deep-merge against the shipped defaults
(so a partial venue config still works), the ROI aspect-ratio check from the
spec, and an atomic writer used by calibration mode.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULTS: dict = {
    "camera": {
        "index": 0, "width": 640, "height": 480, "fps": 30,
        "exposure": 20, "auto_exposure": 0.25, "auto_wb": 0,
    },
    "roi": {"x": 40, "y": 20, "w": 560, "h": 315},
    "colors": {
        "idle": {
            "name": "red",
            "ranges": [
                {"lo": [0, 120, 90], "hi": [8, 255, 255]},
                {"lo": [172, 120, 90], "hi": [179, 255, 255]},
            ],
        },
        "draw": {
            "name": "green",
            "ranges": [{"lo": [45, 100, 90], "hi": [80, 255, 255]}],
        },
    },
    "blob": {"min_area": 60, "max_area": 8000, "min_circularity": 0.55, "gate_px": 150},
    "smoothing": {"min_cutoff": 1.0, "beta": 0.01, "d_cutoff": 1.0},
    "stroke": {
        "start_frames": 2, "end_frames": 4, "max_jump_px": 150,
        "width": 10, "color": "#1a1a1a",
        "palette": [
            "#1a1a1a", "#e11d48", "#f97316", "#eab308", "#22c55e",
            "#06b6d4", "#3b82f6", "#8b5cf6", "#ec4899",
        ],
    },
    "canvas": {"w": 1920, "h": 1080, "background": "#ffffff"},
    "session": {"idle_timeout_s": 90, "attract_fade_s": 20},
    "output": {"dir": "./output", "print_enabled": False},
    "ui": {"language": "sl"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def validate(cfg: dict) -> list[str]:
    """Return a list of human-readable warnings. Does not raise."""
    warnings: list[str] = []

    roi = cfg["roi"]
    canvas = cfg["canvas"]
    if roi["h"] == 0 or canvas["h"] == 0:
        warnings.append("roi/canvas height is zero")
    else:
        roi_ar = roi["w"] / roi["h"]
        canvas_ar = canvas["w"] / canvas["h"]
        if abs(roi_ar - canvas_ar) / canvas_ar > 0.02:
            warnings.append(
                f"ROI aspect ratio {roi_ar:.3f} differs from canvas {canvas_ar:.3f} "
                f"by more than 2% — circles will render as ovals"
            )

    st = cfg["stroke"]
    if st["start_frames"] < 1:
        warnings.append("stroke.start_frames < 1 will drop dots on any green flicker")
    if st["end_frames"] < 1:
        warnings.append("stroke.end_frames < 1 will fragment curves on one dropped frame")

    return warnings


def load(path: str | os.PathLike) -> dict:
    """Load a config file, merged onto :data:`DEFAULTS`. Logs validation warnings."""
    p = Path(path)
    if p.exists():
        try:
            user = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"config: {p} is not valid JSON: {exc}") from exc
        cfg = _deep_merge(DEFAULTS, user)
    else:
        log.warning("config file %s not found — using built-in defaults", p)
        cfg = copy.deepcopy(DEFAULTS)

    for w in validate(cfg):
        log.warning("config: %s", w)
    return cfg


def save(cfg: dict, path: str | os.PathLike) -> None:
    """Atomically write ``cfg`` to ``path`` (temp file + ``os.replace``).

    Runtime-only keys (``_``-prefixed, e.g. ``_kiosk``) are never persisted.
    """
    clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    log.info("config written to %s", p)
