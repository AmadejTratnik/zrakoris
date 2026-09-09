"""Fullscreen canvas shown on the TV (screen index 1).

Shows only the white canvas and the cursor. No text except a short invitation in
IDLE. The child's name is never shown here.

The canvas QImage is painted incrementally — only the newest segment each frame.
A full re-render happens only on undo, clear and session start. The cursor is
drawn in :meth:`paintEvent` on top of the image, never painted into it, so saved
PNGs never carry a stray dot.
"""

from __future__ import annotations

import time

from PyQt5.QtCore import Qt, QPointF, QRectF, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt5.QtWidgets import QWidget

from core.render import new_canvas, paint_segment, render_drawing
from core.session import State
from core.strokes import Drawing
from core.tracker import Detection
from ui.strings import tr


class CanvasWindow(QWidget):
    # M3 dev mode: mouse stands in for the tracker
    mouse_detection = pyqtSignal(object)   # Detection

    def __init__(self, cfg: dict, mouse_input: bool = False, parent=None) -> None:
        super().__init__(parent, Qt.FramelessWindowHint)
        self.mouse_input = mouse_input
        self.cfg = cfg
        c = cfg["canvas"]
        self.canvas_w, self.canvas_h = int(c["w"]), int(c["h"])
        self.background = c.get("background", "#ffffff")
        self.attract_fade_s = float(cfg["session"]["attract_fade_s"])

        self.image: QImage = new_canvas(self.canvas_w, self.canvas_h, self.background)
        self.session_state = State.IDLE
        self.cursor_pos: tuple[float, float] | None = None
        self.tracking_state = "lost"
        self._stroke_color = cfg["stroke"]["color"]
        self._stroke_width = int(cfg["stroke"]["width"])

        # attract-mode fading segments: (p0, p1, created_at)
        self._attract: list[tuple[tuple, tuple, float]] = []
        self._attract_last: tuple[float, float] | None = None

        self._mouse_down = False
        if mouse_input:
            self.setMouseTracking(True)
        else:
            self.setCursor(Qt.BlankCursor)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    # ----------------------------------------------------------- geometry
    def _canvas_rect(self) -> QRectF:
        """Letterboxed target rect for the canvas image inside the widget."""
        ww, wh = self.width(), self.height()
        scale = min(ww / self.canvas_w, wh / self.canvas_h)
        w, h = self.canvas_w * scale, self.canvas_h * scale
        return QRectF((ww - w) / 2, (wh - h) / 2, w, h)

    def _to_widget(self, p: tuple[float, float]) -> QPointF:
        r = self._canvas_rect()
        sx = r.width() / self.canvas_w
        sy = r.height() / self.canvas_h
        return QPointF(r.x() + p[0] * sx, r.y() + p[1] * sy)

    def _to_canvas(self, x: float, y: float) -> tuple[float, float]:
        r = self._canvas_rect()
        sx = self.canvas_w / r.width()
        sy = self.canvas_h / r.height()
        return ((x - r.x()) * sx, (y - r.y()) * sy)

    # ------------------------------------------------ M3 mouse-as-tracker
    def _emit_mouse(self, event, drawing: bool) -> None:
        pos = self._to_canvas(event.x(), event.y())
        state = "draw" if drawing else "hover"
        self.mouse_detection.emit(Detection(state=state, pos=pos, raw=pos, area=100.0))

    def mousePressEvent(self, event) -> None:
        if self.mouse_input and event.button() == Qt.LeftButton:
            self._mouse_down = True
            self._emit_mouse(event, drawing=True)

    def mouseMoveEvent(self, event) -> None:
        if self.mouse_input:
            self._emit_mouse(event, drawing=self._mouse_down)

    def mouseReleaseEvent(self, event) -> None:
        if self.mouse_input and event.button() == Qt.LeftButton:
            self._mouse_down = False
            self._emit_mouse(event, drawing=False)

    # -------------------------------------------------------------- slots
    @pyqtSlot(object, object, str, int)
    def on_stroke_extended(self, p0, p1, color: str, width: int) -> None:
        paint_segment(self.image, p0, p1, color, width)
        self.update()

    @pyqtSlot(str)
    def on_color_changed(self, color: str) -> None:
        """The operator picked a new draw color — only affects the live cursor
        dot and the idle-screen attract lines; painted strokes carry their own
        color and are unaffected (see on_stroke_extended)."""
        self._stroke_color = color

    @pyqtSlot()
    def on_stroke_ended(self) -> None:
        pass

    @pyqtSlot(object)
    def on_rerender(self, drawing: Drawing) -> None:
        self.image = render_drawing(drawing)
        self.update()

    @pyqtSlot(object)
    def on_state_changed(self, state: State) -> None:
        self.session_state = state
        if state is not State.IDLE:
            self._attract.clear()
            self._attract_last = None
        self.update()

    @pyqtSlot(object, object)
    def on_cursor(self, pos, tracking_state: str) -> None:
        self.cursor_pos = pos
        self.tracking_state = tracking_state

        if self.session_state is State.IDLE and pos is not None and tracking_state == "draw":
            now = time.monotonic()
            if self._attract_last is not None:
                self._attract.append((self._attract_last, pos, now))
            self._attract_last = pos
        else:
            self._attract_last = None

        self._prune_attract()
        self.update()

    def _prune_attract(self) -> None:
        cutoff = time.monotonic() - self.attract_fade_s
        self._attract = [seg for seg in self._attract if seg[2] >= cutoff]

    # -------------------------------------------------------------- paint
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self.background))
        target = self._canvas_rect()
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(target, self.image,
                          QRectF(0, 0, self.canvas_w, self.canvas_h))

        if self.session_state is State.IDLE:
            self._paint_attract(painter)
            self._paint_invitation(painter)

        self._paint_cursor(painter)
        painter.end()

    def _paint_attract(self, painter: QPainter) -> None:
        now = time.monotonic()
        for p0, p1, created in self._attract:
            age = now - created
            alpha = max(0.0, 1.0 - age / self.attract_fade_s)
            col = QColor(self._stroke_color)
            col.setAlphaF(alpha * 0.8)
            painter.setPen(QPen(col, self._stroke_width, Qt.SolidLine,
                                Qt.RoundCap, Qt.RoundJoin))
            painter.drawLine(self._to_widget(p0), self._to_widget(p1))

    def _paint_invitation(self, painter: QPainter) -> None:
        painter.setPen(QColor("#9aa0a6"))
        f = QFont()
        f.setPointSize(max(18, self.height() // 24))
        painter.setFont(f)
        painter.drawText(self.rect(), Qt.AlignHCenter | Qt.AlignBottom,
                         tr("canvas_invitation") + "\n")

    def _paint_cursor(self, painter: QPainter) -> None:
        if self.cursor_pos is None or self.tracking_state == "lost":
            return
        p = self._to_widget(self.cursor_pos)
        r = max(8, self._stroke_width)
        if self.tracking_state == "draw":
            painter.setBrush(QColor(self._stroke_color))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(p, r * 0.6, r * 0.6)
        else:  # hover
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#d33"), 3))
            painter.drawEllipse(p, r, r)
