"""Vision pipelines. Pure OpenCV + numpy, imports no Qt.

Two trackers share one blob-selection core (:class:`BlobTracker`):

* :class:`WandTracker` — the LED wand. Two colour masks (red = hover, green =
  draw). This is what ``main.py`` uses unless ``input.mode`` says otherwise.
* :class:`TorchTracker` — a phone camera light. One "is there a bright spot?"
  mask; the torch being on *is* the draw signal, off means lost. No hover.

Input:  a BGR frame (already flipped horizontally by the capture thread) and a
        monotonic timestamp in seconds.
Output: a :class:`Detection` with the cursor already mapped to canvas coords and
        already smoothed, so consumers never deal with the ROI.
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


class BlobTracker:
    """Shared ROI cropping, blob selection and smoothing.

    Subclasses implement :meth:`process` (turn a frame into a Detection) and
    :meth:`debug_masks` (binary masks for the calibration overlay). Not
    thread-safe; own it from one thread.
    """

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

    # -------------------------------------------------------------- internals
    def _roi_slice(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        x0 = max(0, min(self.roi_x, w - 1))
        y0 = max(0, min(self.roi_y, h - 1))
        x1 = max(x0 + 1, min(self.roi_x + self.roi_w, w))
        y1 = max(y0 + 1, min(self.roi_y + self.roi_h, h))
        return frame[y0:y1, x0:x1]

    def _prep_hsv(self, frame: np.ndarray) -> np.ndarray:
        roi = self._roi_slice(frame)                       # crop
        roi = cv2.GaussianBlur(roi, (5, 5), 0)             # blur
        return cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)        # BGR -> HSV

    def _color_mask(self, hsv: np.ndarray, spec: _ColorSpec) -> np.ndarray:
        mask = None
        for lo, hi in spec.ranges:                         # threshold
            part = cv2.inRange(hsv, lo, hi)
            mask = part if mask is None else cv2.bitwise_or(mask, part)
        if mask is None:
            return np.zeros(hsv.shape[:2], dtype=np.uint8)
        return cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)

    def _best_blob(
        self, mask: np.ndarray, last_pos: tuple[float, float] | None
    ) -> tuple[tuple[float, float], float] | None:
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cands: list[tuple[tuple[float, float], float]] = []
        for c in cnts:                                     # candidates
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

        if not cands:                                      # select
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

    def _accept(self, blob, timestamp: float, state: str) -> Detection:
        """Common tail: record last_pos, smooth, map to canvas."""
        raw, area = blob
        self.last_pos = raw
        smoothed_roi = self._filter(raw, timestamp)
        return Detection(state=state, pos=self._to_canvas(smoothed_roi),
                         raw=raw, area=area)

    # ------------------------------------------------------ calibration helper
    def hsv_at(self, frame: np.ndarray, roi_pt: tuple[float, float]) -> tuple[int, int, int] | None:
        """HSV value at a point in ROI coords — for the calibration readout."""
        roi = self._roi_slice(frame)
        x, y = int(round(roi_pt[0])), int(round(roi_pt[1]))
        if 0 <= y < roi.shape[0] and 0 <= x < roi.shape[1]:
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            h, s, v = hsv[y, x]
            return int(h), int(s), int(v)
        return None


class WandTracker(BlobTracker):
    """Stateful single-wand tracker: red = hover, green = draw."""

    def configure(self, cfg: dict) -> None:
        super().configure(cfg)
        self.idle_spec = _ColorSpec.from_config(cfg["colors"]["idle"])
        self.draw_spec = _ColorSpec.from_config(cfg["colors"]["draw"])

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
            self.reset()
            return Detection(state=STATE_LOST)

        return self._accept(blob, timestamp, state)

    def _masks(self, frame: np.ndarray) -> dict[str, np.ndarray]:
        hsv = self._prep_hsv(frame)
        return {
            IDLE_COLOR: self._color_mask(hsv, self.idle_spec),
            DRAW_COLOR: self._color_mask(hsv, self.draw_spec),
        }

    def debug_masks(self, frame: np.ndarray) -> dict[str, np.ndarray]:
        """Return the two binary masks for the calibration overlay."""
        return self._masks(frame)


TORCH_MASK = "torch"


class TorchTracker(BlobTracker):
    """Single bright-spot tracker for a phone camera light.

    The torch is a near-white highlight: very high V, low S. There is no hover
    state — a spot present means "draw", absent means "lost". The operator's
    ``start``/``stop`` and the user's torch button are the only gates.
    """

    def configure(self, cfg: dict) -> None:
        super().configure(cfg)
        t = cfg.get("torch", {})
        self.v_min = int(t.get("v_min", 235))
        self.s_max = int(t.get("s_max", 90))
        self.dilate = int(t.get("dilate", 2))

    # ------------------------------------------------------------- main entry
    def process(self, frame: np.ndarray, timestamp: float) -> Detection:
        blob = self._best_blob(self._mask(frame), self.last_pos)
        if blob is None:
            self.reset()
            return Detection(state=STATE_LOST)
        return self._accept(blob, timestamp, STATE_DRAW)

    def _mask(self, frame: np.ndarray) -> np.ndarray:
        hsv = self._prep_hsv(frame)
        lo = np.array([0, 0, self.v_min], dtype=np.uint8)
        hi = np.array([179, self.s_max, 255], dtype=np.uint8)
        mask = cv2.inRange(hsv, lo, hi)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        if self.dilate > 0:
            mask = cv2.dilate(mask, self._kernel, iterations=self.dilate)
        return mask

    def debug_masks(self, frame: np.ndarray) -> dict[str, np.ndarray]:
        return {TORCH_MASK: self._mask(frame)}
