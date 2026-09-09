"""Render a :class:`~core.strokes.Drawing` to a QImage.

Shared by the live canvas (full re-render on undo/clear/start) and the
SaveWorker (PNG export). Keeping it here means the saved PNG is produced by the
exact same code path as the on-screen image, minus the cursor.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QColor, QImage, QPainter, QPen

from .strokes import Drawing, Stroke


def new_canvas(w: int, h: int, background: str) -> QImage:
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(background))
    return img


def paint_segment(img: QImage, p0, p1, color: str, width: int) -> None:
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    if p0 == p1:
        painter.drawPoint(QPointF(p1[0], p1[1]))
    else:
        painter.drawLine(QPointF(p0[0], p0[1]), QPointF(p1[0], p1[1]))
    painter.end()


def _paint_stroke(painter: QPainter, s: Stroke) -> None:
    if not s.points:
        return
    pen = QPen(QColor(s.color), s.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    if len(s.points) == 1:
        x, y, _ = s.points[0]
        painter.drawPoint(QPointF(x, y))
        return
    for (x0, y0, _), (x1, y1, _) in zip(s.points, s.points[1:]):
        painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))


def render_drawing(drawing: Drawing) -> QImage:
    img = new_canvas(drawing.canvas_w, drawing.canvas_h, drawing.background)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing)
    for s in drawing.strokes:
        _paint_stroke(painter, s)
    painter.end()
    return img
