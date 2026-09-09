"""WandTracker — the vision pipeline. Pure OpenCV + numpy, imports no Qt.

Input:  a BGR frame (already flipped horizontally by the capture thread) and a
        monotonic timestamp in seconds.
Output: a :class:`Detection` with the cursor already mapped to canvas coords and
        already smoothed, so consumers never deal with the ROI.

The nine stages are documented inline. See AIRDRAW_SPEC.md section 5.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .filters import PointFilter

IDLE_COLOR = "idle"   # red LED — resting state
DRAW_COLOR = "draw"   # green LED — button held

STATE_DRAW = "draw"
STATE_HOVER = "hover"
STATE_LOST = "lost"


@dataclass
class Detection:
    state: str                       # "draw" | "hover" | "lost"
    pos: tuple | None = None         # (x, y) in CANVAS coords, smoothed
    raw: tuple | None = None         # (x, y) in ROI coords, pre-smoothing
    area: float = 0.0


@dataclass
class _ColorSpec:
    name: str
    ranges: list[tuple[np.ndarray, np.ndarray]] = field(default_factory=list)

    @classmethod
    def from_config(cls, d: dict) -> "_ColorSpec":
        ranges = [
            (np.array(r["lo"], dtype=np.uint8), np.array(r["hi"], dtype=np.uint8))
            for r in d["ranges"]
        ]
        return cls(name=d.get("name", "?"), ranges=ranges)


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class WandTracker:
    """Stateful single-wand tracker. Not thread-safe; own it from one thread."""

    def __init__(self, cfg: dict) -> None:
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self.configure(cfg)
        self.reset()

    # ------------------------------------------------------------------ config
    def configure(self, cfg: dict) -> None:
        """Apply a (possibly newly calibrated) config without dropping state."""
        roi = cfg["roi"]
        self.roi_x, self.roi_y = int(roi["x"]), int(roi["y"])
        self.roi_w, self.roi_h = int(roi["w"]), int(roi["h"])

        blob = cfg["blob"]
        self.min_area = float(blob["min_area"])
        self.max_area = float(blob["max_area"])
        self.min_circularity = float(blob["min_circularity"])
        self.gate_px = float(blob["gate_px"])

        canvas = cfg["canvas"]
        self.canvas_w, self.canvas_h = int(canvas["w"]), int(canvas["h"])

        self.idle_spec = _ColorSpec.from_config(cfg["colors"]["idle"])
        self.draw_spec = _ColorSpec.from_config(cfg["colors"]["draw"])

        s = cfg["smoothing"]
        self._filter = PointFilter(
            freq=float(cfg["camera"].get("fps", 30)),
            min_cutoff=float(s["min_cutoff"]),
            beta=float(s["beta"]),
            d_cutoff=float(s["d_cutoff"]),
        )

    def reset(self) -> None:
        """Forget the last position and clear the smoothing filters."""
        self.last_pos: tuple[float, float] | None = None
        self._filter.reset()

    # ------------------------------------------------------------- main entry
    def process(self, frame: np.ndarray, timestamp: float) -> Detection:
        masks = self._masks(frame)
        green = self._best_blob(masks[DRAW_COLOR], self.last_pos)
        red = self._best_blob(masks[IDLE_COLOR], self.last_pos)

        # Green wins over red: committing to "drawing" is the right failure mode.
        if green is not None:
            blob, state = green, STATE_DRAW
        elif red is not None:
            blob, state = red, STATE_HOVER
        else:
            blob, state = None, STATE_LOST

        if blob is None:
            self.reset()
            return Detection(state=STATE_LOST)

        raw, area = blob
        self.last_pos = raw
        smoothed_roi = self._filter(raw, timestamp)
        canvas_pos = self._to_canvas(smoothed_roi)
        return Detection(state=state, pos=canvas_pos, raw=raw, area=area)

    # -------------------------------------------------------------- internals
    def _roi_slice(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        x0 = max(0, min(self.roi_x, w - 1))
        y0 = max(0, min(self.roi_y, h - 1))
        x1 = max(x0 + 1, min(self.roi_x + self.roi_w, w))
        y1 = max(y0 + 1, min(self.roi_y + self.roi_h, h))
        return frame[y0:y1, x0:x1]

    def _masks(self, frame: np.ndarray) -> dict[str, np.ndarray]:
        roi = self._roi_slice(frame)                       # stage 2: crop
        roi = cv2.GaussianBlur(roi, (5, 5), 0)             # stage 3: blur
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)         # stage 4: BGR->HSV
        return {
            IDLE_COLOR: self._color_mask(hsv, self.idle_spec),
            DRAW_COLOR: self._color_mask(hsv, self.draw_spec),
        }

    def _color_mask(self, hsv: np.ndarray, spec: _ColorSpec) -> np.ndarray:
        mask = None
        for lo, hi in spec.ranges:                         # stage 5: threshold
            part = cv2.inRange(hsv, lo, hi)
            mask = part if mask is None else cv2.bitwise_or(mask, part)
        if mask is None:
            return np.zeros(hsv.shape[:2], dtype=np.uint8)
        return cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)  # stage 6

    def _best_blob(
        self, mask: np.ndarray, last_pos: tuple[float, float] | None
    ) -> tuple[tuple[float, float], float] | None:
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cands: list[tuple[tuple[float, float], float]] = []
        for c in cnts:                                     # stage 7: candidates
            area = cv2.contourArea(c)
            if not (self.min_area < area < self.max_area):
                continue
            peri = cv2.arcLength(c, True)
            if peri <= 0:
                continue
            circularity = 4.0 * math.pi * area / (peri * peri)
            if circularity < self.min_circularity:
                continue
            # minEnclosingCircle, not moments: steadier when the blob clips the
            # ROI edge or a finger occludes part of it.
            (x, y), _r = cv2.minEnclosingCircle(c)
            cands.append(((float(x), float(y)), float(area)))

        if not cands:                                      # stage 8: select
            return None
        if last_pos is None:
            return max(cands, key=lambda b: b[1])          # cold start: largest
        near = [b for b in cands if _dist(b[0], last_pos) < self.gate_px]
        if near:
            return min(near, key=lambda b: _dist(b[0], last_pos))
        return max(cands, key=lambda b: b[1])              # reacquire

    def _to_canvas(self, p: tuple[float, float]) -> tuple[float, float]:
        sx = self.canvas_w / self.roi_w
        sy = self.canvas_h / self.roi_h
        return (p[0] * sx, p[1] * sy)

    # ------------------------------------------------------ calibration helper
    def debug_masks(self, frame: np.ndarray) -> dict[str, np.ndarray]:
        """Return the two binary masks for the calibration overlay."""
        return self._masks(frame)

    def hsv_at(self, frame: np.ndarray, roi_pt: tuple[float, float]) -> tuple[int, int, int] | None:
        """HSV value at a point in ROI coords — for the calibration readout."""
        roi = self._roi_slice(frame)
        x, y = int(round(roi_pt[0])), int(round(roi_pt[1]))
        if 0 <= y < roi.shape[0] and 0 <= x < roi.shape[1]:
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            h, s, v = hsv[y, x]
            return int(h), int(s), int(v)
        return None
