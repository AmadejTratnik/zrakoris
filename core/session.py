"""SessionController — the operator-facing state machine.

See AIRDRAW_SPEC.md section 7. Three things that are easy to miss:

* The cursor updates in *every* state. Only recording is gated to DRAWING.
* Any open stroke is closed on every state exit, so ``resume`` never draws a
  long line back to where the child was standing seconds ago.
* CALIBRATING stores and restores the previous state, so recalibrating
  mid-session does not destroy the child's drawing.
"""

from __future__ import annotations

import logging
import math
import time
from enum import Enum, auto

from PyQt5.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from .strokes import Drawing, Stroke
from .tracker import STATE_DRAW, Detection

log = logging.getLogger(__name__)


class State(Enum):
    IDLE = auto()
    ENTRY = auto()
    DRAWING = auto()
    REVIEW = auto()
    CALIBRATING = auto()


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class SessionController(QObject):
    # state
    state_changed = pyqtSignal(object)            # State
    # rendering
    stroke_extended = pyqtSignal(object, object, str, int)  # p0, p1, color, width
    stroke_ended = pyqtSignal()
    rerender = pyqtSignal(object)                 # Drawing — full repaint
    cursor_moved = pyqtSignal(object, object)     # pos|None, tracking_state str
    # operator status
    stats_changed = pyqtSignal(dict)              # {stroke_count, point_count, name}
    idle_warning = pyqtSignal(bool)               # show/hide "wrap up?" banner
    session_saved = pyqtSignal(object)            # Drawing (for the SaveWorker)
    color_changed = pyqtSignal(str)               # new stroke color for future strokes

    def __init__(self, cfg: dict, tracker, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.tracker = tracker

        self.state = State.IDLE
        self._prev_state: State | None = None

        self.drawing = self._new_drawing()
        self.name = ""
        self.cursor: tuple[float, float] | None = None
        self.tracking_state = "lost"
        self.started_at: str | None = None

        # stroke debounce state
        st = cfg["stroke"]
        self._start_frames = int(st["start_frames"])
        self._end_frames = int(st["end_frames"])
        self._max_jump = float(st["max_jump_px"])
        self._stroke_color = st["color"]
        self._stroke_width = int(st["width"])

        self._open_stroke: Stroke | None = None
        self._stroke_t0 = 0.0
        self._last_point: tuple[float, float] | None = None
        self._draw_run = 0
        self._grace_run = 0

        # idle timeout
        self._last_detection_wall = time.monotonic()
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(1000)
        self._idle_timer.timeout.connect(self._check_idle)
        self._idle_banner_shown = False

    # ------------------------------------------------------------ lifecycle
    def _new_drawing(self) -> Drawing:
        c = self.cfg["canvas"]
        return Drawing(canvas_w=int(c["w"]), canvas_h=int(c["h"]),
                       background=c.get("background", "#ffffff"))

    def _set_state(self, new: State) -> None:
        if new is self.state:
            return
        log.info("state %s -> %s", self.state.name, new.name)
        # close any open stroke and reset smoothing on every state exit
        self._close_stroke()
        self.tracker.reset()
        self.state = new
        self._hide_idle_banner()
        if new is State.DRAWING:
            self._last_detection_wall = time.monotonic()
            self._idle_timer.start()
        else:
            self._idle_timer.stop()
        self.state_changed.emit(new)

    # --------------------------------------------------- operator commands
    def new_session(self) -> None:
        if self.state in (State.IDLE, State.ENTRY):
            self.drawing = self._new_drawing()
            self.name = ""
            self.rerender.emit(self.drawing)
            self._emit_stats()
            self._set_state(State.ENTRY)

    def set_name(self, name: str) -> None:
        self.name = name.strip()
        self._emit_stats()

    @property
    def stroke_color(self) -> str:
        return self._stroke_color

    def set_stroke_color(self, color: str) -> None:
        """Pick the color future strokes are drawn in (e.g. the 1-9 palette keys).

        The currently open stroke (if any) keeps its own color — only strokes
        started after this call use the new one.
        """
        if color == self._stroke_color:
            return
        self._stroke_color = color
        self.color_changed.emit(color)

    def start(self) -> None:
        if self.state is State.ENTRY:
            self.started_at = _utc_now()
            self._set_state(State.DRAWING)
        elif self.state is State.REVIEW:
            self.resume()

    def stop(self) -> None:
        if self.state is State.DRAWING:
            self._set_state(State.REVIEW)

    def cancel(self) -> None:
        if self.state is State.ENTRY:
            self._set_state(State.IDLE)

    def resume(self) -> None:
        if self.state is State.REVIEW:
            self._set_state(State.DRAWING)

    def toggle_start_stop(self) -> None:
        if self.state is State.ENTRY:
            self.start()
        elif self.state is State.DRAWING:
            self.stop()
        elif self.state is State.REVIEW:
            self.resume()

    def undo(self) -> None:
        if self.state in (State.DRAWING, State.REVIEW):
            popped = self.drawing.undo()
            if popped is not None:
                self._close_stroke()
                self.rerender.emit(self.drawing)
                self._emit_stats()

    def clear(self) -> None:
        if self.state is State.DRAWING:
            self.drawing.clear()
            self._close_stroke()
            self.rerender.emit(self.drawing)
            self._emit_stats()

    def save(self) -> None:
        if self.state is State.REVIEW:
            self.session_saved.emit(self._build_record())
            self._reset_to_idle()

    def discard(self) -> None:
        if self.state is State.REVIEW:
            self._reset_to_idle()

    def _reset_to_idle(self) -> None:
        self.drawing = self._new_drawing()
        self.name = ""
        self.started_at = None
        self.rerender.emit(self.drawing)
        self._emit_stats()
        self._set_state(State.IDLE)

    # --------------------------------------------------------- calibration
    def enter_calibration(self) -> None:
        if self.state is State.CALIBRATING:
            return
        self._prev_state = self.state
        self._set_state(State.CALIBRATING)

    def exit_calibration(self) -> None:
        if self.state is not State.CALIBRATING:
            return
        target = self._prev_state or State.IDLE
        self._prev_state = None
        self._set_state(target)

    # ----------------------------------------------------- detection input
    @pyqtSlot(object)
    def on_detection(self, det: Detection) -> None:
        self.cursor = det.pos
        self.tracking_state = det.state
        self.cursor_moved.emit(det.pos, det.state)

        if det.pos is not None:
            self._last_detection_wall = time.monotonic()

        if self.state is not State.DRAWING:
            self._close_stroke()
            return

        self._feed_stroke(det)

    def _feed_stroke(self, det: Detection) -> None:
        if det.state == STATE_DRAW and det.pos is not None:
            self._grace_run = 0
            self._draw_run += 1

            if self._open_stroke is None:
                if self._draw_run >= self._start_frames:
                    self._begin_stroke(det.pos)
                return

            # jump rejection: a big leap is tracking loss, not a fast hand
            if self._last_point is not None and _dist(det.pos, self._last_point) > self._max_jump:
                log.debug("jump %.0fpx > %.0f — closing stroke",
                          _dist(det.pos, self._last_point), self._max_jump)
                self._close_stroke()
                return

            self._append_point(det.pos)
        else:
            self._draw_run = 0
            if self._open_stroke is not None:
                self._grace_run += 1
                if self._grace_run >= self._end_frames:
                    self._close_stroke()

    def _begin_stroke(self, pos: tuple[float, float]) -> None:
        self._open_stroke = self.drawing.begin_stroke(self._stroke_color, self._stroke_width)
        self._stroke_t0 = time.monotonic()
        self._open_stroke.add(pos[0], pos[1], 0.0)
        self._last_point = pos
        self._grace_run = 0
        self._emit_stats()

    def _append_point(self, pos: tuple[float, float]) -> None:
        assert self._open_stroke is not None
        t = time.monotonic() - self._stroke_t0
        self._open_stroke.add(pos[0], pos[1], t)
        p0 = self._last_point
        self._last_point = pos
        if p0 is not None:
            # the open stroke's own color/width, not the (possibly since-changed)
            # current picker selection — a color switch mid-stroke must not
            # repaint what's already on the canvas in the new color.
            self.stroke_extended.emit(p0, pos, self._open_stroke.color, self._open_stroke.width)
        self._emit_stats()

    def _close_stroke(self) -> None:
        if self._open_stroke is None:
            self._draw_run = 0
            self._grace_run = 0
            self._last_point = None
            return
        # drop a stroke that never accumulated a drawable point
        if not self._open_stroke.is_drawable:
            try:
                self.drawing.strokes.remove(self._open_stroke)
            except ValueError:
                pass
        self._open_stroke = None
        self._last_point = None
        self._draw_run = 0
        self._grace_run = 0
        self.stroke_ended.emit()
        self._emit_stats()

    # -------------------------------------------------------- idle timeout
    def _check_idle(self) -> None:
        if self.state is not State.DRAWING:
            return
        elapsed = time.monotonic() - self._last_detection_wall
        if elapsed >= float(self.cfg["session"]["idle_timeout_s"]) and not self._idle_banner_shown:
            self._idle_banner_shown = True
            self.idle_warning.emit(True)
        elif elapsed < 2.0 and self._idle_banner_shown:
            self._hide_idle_banner()

    def _hide_idle_banner(self) -> None:
        if self._idle_banner_shown:
            self._idle_banner_shown = False
            self.idle_warning.emit(False)

    # --------------------------------------------------------------- utils
    def _emit_stats(self) -> None:
        self.stats_changed.emit({
            "stroke_count": self.drawing.stroke_count,
            "point_count": self.drawing.point_count,
            "name": self.name,
        })

    def _build_record(self) -> dict:
        return {
            "name": self.name,
            "started_at": self.started_at or _utc_now(),
            "ended_at": _utc_now(),
            "drawing": self.drawing,
        }


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
