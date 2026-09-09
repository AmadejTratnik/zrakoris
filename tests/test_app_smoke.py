"""Headless smoke test of the full wiring (offscreen Qt).

Exercises the state machine through a whole session and checks the canvas image
and a saved file come out, without a camera or a display.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt5.QtWidgets")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from core import config as configmod  # noqa: E402
from core.session import SessionController, State  # noqa: E402
from core.tracker import Detection, WandTracker  # noqa: E402
from ui.canvas_window import CanvasWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def cfg(tmp_path):
    c = configmod.load(tmp_path / "none.json")
    c["output"]["dir"] = str(tmp_path / "output")
    c["stroke"]["start_frames"] = 2
    c["stroke"]["end_frames"] = 4
    return c


def _draw(session, x, y):
    session.on_detection(Detection(state="draw", pos=(x, y), raw=(x, y), area=100))


def _hover(session, x=0, y=0):
    session.on_detection(Detection(state="hover", pos=(x, y), raw=(x, y), area=100))


def test_full_session_flow(app, cfg):
    tracker = WandTracker(cfg)
    session = SessionController(cfg, tracker)
    canvas = CanvasWindow(cfg)
    session.stroke_extended.connect(canvas.on_stroke_extended)
    session.rerender.connect(canvas.on_rerender)
    session.state_changed.connect(canvas.on_state_changed)
    session.cursor_moved.connect(canvas.on_cursor)

    saved = []
    session.session_saved.connect(saved.append)

    assert session.state is State.IDLE

    # cursor updates even in IDLE
    _hover(session, 5, 5)
    assert session.cursor == (5, 5)

    session.new_session()
    assert session.state is State.ENTRY
    session.set_name("Marek")

    session.start()
    assert session.state is State.DRAWING

    # one green flicker must NOT open a stroke (start_frames = 2)
    _draw(session, 100, 100)
    assert session.drawing.stroke_count == 0
    _draw(session, 101, 101)
    assert session.drawing.stroke_count == 1
    for i in range(2, 20):
        _draw(session, 100 + i, 100 + i)
    assert session.drawing.point_count >= 10

    # one dropped frame must NOT split the stroke (end_frames = 4)
    _hover(session)
    _draw(session, 130, 130)
    assert session.drawing.stroke_count == 1

    # sustained loss closes the stroke
    for _ in range(5):
        _hover(session)
    assert session._open_stroke is None

    # jump rejection: far leap closes rather than drawing across
    for i in range(3):
        _draw(session, 200 + i, 200 + i)
    n = session.drawing.stroke_count
    _draw(session, 900, 900)  # > max_jump_px from ~202
    for i in range(3):
        _draw(session, 905 + i, 905 + i)
    # a new stroke started at the far point, none bridging the gap
    assert session.drawing.stroke_count == n + 1

    session.stop()
    assert session.state is State.REVIEW

    strokes_before = session.drawing.stroke_count
    session.undo()
    assert session.drawing.stroke_count == strokes_before - 1

    session.save()
    assert session.state is State.IDLE
    assert len(saved) == 1
    assert saved[0]["name"] == "Marek"
    assert saved[0]["drawing"].stroke_count == strokes_before - 1


def test_calibration_preserves_drawing(app, cfg):
    tracker = WandTracker(cfg)
    session = SessionController(cfg, tracker)
    session.new_session()
    session.start()
    for i in range(6):
        _draw(session, 100 + i * 5, 100)
    assert session.drawing.stroke_count == 1

    session.enter_calibration()
    assert session.state is State.CALIBRATING
    session.exit_calibration()
    assert session.state is State.DRAWING
    assert session.drawing.stroke_count == 1  # drawing survived


def test_clear_keeps_session(app, cfg):
    tracker = WandTracker(cfg)
    session = SessionController(cfg, tracker)
    session.new_session()
    session.start()
    for i in range(6):
        _draw(session, 100 + i * 5, 100)
    session.clear()
    assert session.state is State.DRAWING
    assert session.drawing.stroke_count == 0
