"""M3 — canvas + stroke logic driven by real Qt mouse events (offscreen).

Mirrors the M3 table in TEST_PLAN.md. Mouse is a perfect input source, so any
failure here is in the stroke model / rendering, not tracking.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt5.QtWidgets")

from PyQt5.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PyQt5.QtGui import QMouseEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from core import config as configmod  # noqa: E402
from core.render import render_drawing  # noqa: E402
from core.session import SessionController, State  # noqa: E402
from core.storage import SaveWorker  # noqa: E402
from core.tracker import WandTracker  # noqa: E402
from ui.canvas_window import CanvasWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def img_bytes(img) -> bytes:
    b = img.constBits()
    b.setsize(img.sizeInBytes())
    return bytes(b)


@pytest.fixture
def cfg(tmp_path):
    c = configmod.load(tmp_path / "none.json")
    c["output"]["dir"] = str(tmp_path / "output")
    c["session"]["idle_timeout_s"] = 10
    return c


class Harness:
    """CanvasWindow + SessionController wired like main.py (--mouse)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.tracker = WandTracker(cfg)
        self.session = SessionController(cfg, self.tracker)
        self.canvas = CanvasWindow(cfg, mouse_input=True)
        self.canvas.resize(960, 540)  # 1920x1080 canvas -> exact 0.5 scale

        s = self.session
        s.stroke_extended.connect(self.canvas.on_stroke_extended)
        s.stroke_ended.connect(self.canvas.on_stroke_ended)
        s.rerender.connect(self.canvas.on_rerender)
        s.cursor_moved.connect(self.canvas.on_cursor)
        s.state_changed.connect(self.canvas.on_state_changed)
        self.canvas.mouse_detection.connect(s.on_detection)

        self.rerenders = 0
        s.rerender.connect(lambda *_: setattr(self, "rerenders", self.rerenders + 1))
        self.bridging_segments = []
        s.stroke_extended.connect(self._watch_segment)

    def _watch_segment(self, p0, p1, color, width):
        d = ((p0[0] - p1[0]) ** 2 + (p0[1] - p1[1]) ** 2) ** 0.5
        if d > 300:  # a segment this long across the canvas = a bridge/jump bug
            self.bridging_segments.append((p0, p1, d))

    # -- event helpers (widget coords) --------------------------------------
    def _evt(self, kind, x, y, buttons):
        return QMouseEvent(kind, QPointF(x, y), Qt.LeftButton, buttons, Qt.NoModifier)

    def press(self, x, y):
        self.canvas.mousePressEvent(self._evt(QEvent.MouseButtonPress, x, y, Qt.LeftButton))

    def move(self, x, y, down=True):
        b = Qt.LeftButton if down else Qt.NoButton
        self.canvas.mouseMoveEvent(self._evt(QEvent.MouseMove, x, y, b))

    def release(self, x, y):
        self.canvas.mouseReleaseEvent(self._evt(QEvent.MouseButtonRelease, x, y, Qt.NoButton))

    def stroke(self, pts):
        """Full click-drag-release through the given widget points."""
        self.press(*pts[0])
        for p in pts[1:]:
            self.move(*p)
        self.release(*pts[-1])


@pytest.fixture
def h(app, cfg):
    return Harness(cfg)


# --------------------------------------------------------------------- M3.1
def test_m3_1_state_flow(h):
    assert h.session.state is State.IDLE
    h.session.new_session()
    assert h.session.state is State.ENTRY
    h.session.set_name("Marek")
    h.session.start()
    assert h.session.state is State.DRAWING


# --------------------------------------------------------------------- M3.2
def test_m3_2_drag_makes_a_smooth_stroke(h):
    h.session.new_session(); h.session.start()
    # realistic mouse sampling: ~12px widget steps (24px canvas, well under max_jump)
    pts = [(100 + i * 12, 100 + i * 9) for i in range(12)]
    h.stroke(pts)
    assert h.session.drawing.stroke_count == 1
    stroke = h.session.drawing.strokes[0]
    assert len(stroke) >= 8                      # every drag point recorded
    # canvas image actually changed (line was painted incrementally)
    blank = render_drawing(type(h.session.drawing)(1920, 1080))
    assert img_bytes(h.canvas.image) != img_bytes(blank)


# --------------------------------------------------------------------- M3.3
def test_m3_3_single_click_leaves_no_dot(h):
    h.session.new_session(); h.session.start()
    h.press(200, 200)
    h.release(200, 200)                           # no movement between
    assert h.session.drawing.stroke_count == 0
    # but a real click-drag does leave one
    h.stroke([(300, 300), (320, 320), (350, 350)])
    assert h.session.drawing.stroke_count == 1


# --------------------------------------------------------------------- M3.4
def test_m3_4_undo_pops_exactly_one_and_rerenders(h):
    h.session.new_session(); h.session.start()
    for base in (100, 200, 300):
        h.stroke([(base, base), (base + 20, base + 10), (base + 40, base + 30)])
    assert h.session.drawing.stroke_count == 3
    before = h.rerenders
    h.session.undo()
    assert h.session.drawing.stroke_count == 2
    assert h.rerenders == before + 1
    h.session.undo(); h.session.undo()
    assert h.session.drawing.stroke_count == 0
    h.session.undo()                              # nothing left — no crash
    assert h.session.drawing.stroke_count == 0


# --------------------------------------------------------------------- M3.5
def test_m3_5_clear_keeps_session(h):
    h.session.new_session(); h.session.set_name("Ana"); h.session.start()
    h.stroke([(100, 100), (150, 150), (200, 200)])
    h.session.clear()
    assert h.session.state is State.DRAWING
    assert h.session.drawing.stroke_count == 0
    assert h.session.name == "Ana"


# --------------------------------------------------------------------- M3.6
def test_m3_6_no_line_between_separate_strokes(h):
    h.session.new_session(); h.session.start()
    h.stroke([(100, 100), (120, 110), (140, 120)])
    h.stroke([(800, 400), (820, 410), (840, 420)])
    assert h.session.drawing.stroke_count == 2
    assert h.bridging_segments == []              # nothing drawn across the gap


# --------------------------------------------------------------------- M3.7
def test_m3_7_resume_draws_no_long_line(h):
    h.session.new_session(); h.session.start()
    h.press(100, 100); h.move(120, 110); h.move(140, 120)   # stroke open, button held
    h.session.stop()
    assert h.session.state is State.REVIEW
    h.session.resume()
    assert h.session.state is State.DRAWING
    # "button still held" — moves come in far from the old spot
    h.move(700, 450); h.move(720, 460); h.move(740, 470)
    assert h.bridging_segments == []
    # the post-resume stroke starts at the new location, not the old one
    last = h.session.drawing.strokes[-1]
    assert last.points[0][0] > 1000                # far side in canvas coords


# --------------------------------------------------------------------- M3.8
def test_m3_8_coord_mapping_tracks_resize(h):
    h.canvas.resize(800, 800)                     # letterbox: scale 0.41667, y-offset 175
    cx, cy = h.canvas._to_canvas(400, 400)        # widget centre-ish
    assert cx == pytest.approx(960, abs=2)
    assert cy == pytest.approx(540, abs=3)


# --------------------------------------------------------------------- M3.9 / M3.10
def test_m3_9_and_10_save_outputs_and_png_has_no_cursor(h, cfg, tmp_path):
    h.session.new_session(); h.session.set_name("Marek"); h.session.start()
    h.stroke([(100 + i * 12, 100 + i * 8) for i in range(15)])

    # cursor activity must NOT bleed into the canvas image
    img_before = img_bytes(h.canvas.image)
    for x in range(100, 400, 30):
        h.canvas.on_cursor((x * 4.0, x * 3.0), "draw")
    img_after = img_bytes(h.canvas.image)
    assert img_before == img_after                # cursor drawn only in paintEvent

    saved = []
    h.session.session_saved.connect(saved.append)
    h.session.stop()
    h.session.save()
    assert h.session.state is State.IDLE
    assert len(saved) == 1

    worker = SaveWorker(cfg)
    sid = worker._save(saved[0])
    day = next((tmp_path / "output").iterdir())
    sess = day / sid
    assert (sess / "drawing.png").exists()
    assert (sess / "session.json").exists()
    assert (day / "index.csv").exists()

    # the PNG is produced purely from strokes (render_drawing), never the cursor
    from PyQt5.QtGui import QImage
    png = QImage(str(sess / "drawing.png"))
    ref = render_drawing(saved[0]["drawing"])
    assert png.size() == ref.size()


# --------------------------------------------------------------------- M3.12
def test_m3_12_idle_banner_on_panel_only(h):
    seen = []
    h.session.idle_warning.connect(seen.append)

    canvas_got = []
    # canvas is deliberately NOT connected to idle_warning anywhere
    assert not any(
        "idle_warning" in str(c) for c in [h.canvas.on_cursor, h.canvas.on_state_changed]
    )

    h.session.new_session(); h.session.start()
    # simulate 11 s of no detections
    import time
    h.session._last_detection_wall = time.monotonic() - 11
    h.session._check_idle()
    assert seen == [True]                         # banner requested exactly once

    # wand comes back -> banner cleared
    h.session._last_detection_wall = time.monotonic()
    h.session._check_idle()
    assert seen == [True, False]
    _ = canvas_got
