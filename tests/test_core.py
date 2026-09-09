"""Tests for the Qt-free core: filters, strokes, tracker, config, storage helpers.

Run: python3 -m pytest -q
"""

from __future__ import annotations

import copy
import json

import cv2
import numpy as np
import pytest

from core import config as configmod
from core.filters import OneEuroFilter, PointFilter
from core.strokes import Drawing, Stroke
from core.tracker import STATE_DRAW, STATE_HOVER, STATE_LOST, WandTracker


# --------------------------------------------------------------------- filters
def test_one_euro_converges_to_constant():
    f = OneEuroFilter(freq=30)
    out = None
    for i in range(60):
        out = f(5.0, i / 30.0)
    assert out == pytest.approx(5.0, abs=1e-3)


def test_one_euro_reset_clears_history():
    f = OneEuroFilter(freq=30)
    for i in range(30):
        f(10.0, i / 30.0)
    f.reset()
    assert f(0.0, 1.0) == 0.0  # first sample after reset passes straight through


def test_point_filter_independent_axes():
    pf = PointFilter(freq=30)
    x, y = pf((1.0, 100.0), 0.0)
    assert x == 1.0 and y == 100.0


# --------------------------------------------------------------------- strokes
def test_drawing_undo_and_clear():
    d = Drawing(1920, 1080)
    s = d.begin_stroke("#000", 10)
    s.add(1, 2, 0.0)
    d.begin_stroke("#000", 10).add(3, 4, 0.0)
    assert d.stroke_count == 2
    assert d.point_count == 2
    popped = d.undo()
    assert popped.points == [(3.0, 4.0, 0.0)]
    assert d.stroke_count == 1
    d.clear()
    assert d.stroke_count == 0
    assert d.undo() is None


def test_stroke_json_roundtrip():
    s = Stroke("#1a1a1a", 10, [(1.111, 2.222, 0.033), (3.0, 4.0, 0.066)])
    s2 = Stroke.from_json(s.to_json())
    assert s2.color == s.color and s2.width == s.width
    assert s2.points[0] == pytest.approx((1.11, 2.22, 0.033))


def test_drawing_json_roundtrip():
    d = Drawing(800, 600, "#ffffff")
    d.begin_stroke("#000", 5).add(10, 20, 0.0)
    d2 = Drawing.from_json(d.to_json())
    assert d2.canvas_w == 800 and d2.canvas_h == 600
    assert d2.point_count == 1


# --------------------------------------------------------------------- config
def test_config_defaults_when_missing(tmp_path):
    cfg = configmod.load(tmp_path / "nope.json")
    assert cfg["camera"]["exposure"] == configmod.DEFAULTS["camera"]["exposure"]


def test_config_deep_merge(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"camera": {"exposure": -5}}))
    cfg = configmod.load(p)
    assert cfg["camera"]["exposure"] == -5
    assert cfg["camera"]["auto_wb"] == 0  # untouched default preserved


def test_config_roi_aspect_warning():
    cfg = copy.deepcopy(configmod.DEFAULTS)
    cfg["roi"] = {"x": 0, "y": 0, "w": 400, "h": 400}  # 1:1 vs 16:9 canvas
    warnings = configmod.validate(cfg)
    assert any("aspect" in w for w in warnings)


def test_config_save_atomic(tmp_path):
    cfg = copy.deepcopy(configmod.DEFAULTS)
    dest = tmp_path / "out.json"
    configmod.save(cfg, dest)
    assert json.loads(dest.read_text())["blob"]["min_area"] == 60
    assert not (tmp_path / "out.json.tmp").exists()


def test_config_save_drops_runtime_keys(tmp_path):
    cfg = copy.deepcopy(configmod.DEFAULTS)
    cfg["_kiosk"] = True
    cfg["_anything"] = 123
    dest = tmp_path / "out.json"
    configmod.save(cfg, dest)
    written = json.loads(dest.read_text())
    assert "_kiosk" not in written and "_anything" not in written
    assert written["ui"]["language"] == "sl"


# --------------------------------------------------------------------- tracker
def _frame_with_blob(color_bgr, center, radius=14, size=(480, 640)):
    frame = np.zeros((size[0], size[1], 3), dtype=np.uint8)
    cv2.circle(frame, center, radius, color_bgr, -1)
    return frame


@pytest.fixture
def cfg():
    c = copy.deepcopy(configmod.DEFAULTS)
    c["roi"] = {"x": 0, "y": 0, "w": 640, "h": 360}
    c["canvas"] = {"w": 1920, "h": 1080, "background": "#ffffff"}
    return c


def test_tracker_green_is_draw(cfg):
    t = WandTracker(cfg)
    frame = _frame_with_blob((0, 255, 0), (200, 150))
    det = t.process(frame, 0.0)
    assert det.state == STATE_DRAW
    # (200,150) ROI -> canvas scale x3
    assert det.pos == pytest.approx((600, 450), abs=8)


def test_tracker_red_is_hover(cfg):
    t = WandTracker(cfg)
    det = t.process(_frame_with_blob((0, 0, 255), (100, 100)), 0.0)
    assert det.state == STATE_HOVER


def test_tracker_green_wins_over_red(cfg):
    t = WandTracker(cfg)
    frame = _frame_with_blob((0, 0, 255), (100, 100))
    cv2.circle(frame, (400, 200), 14, (0, 255, 0), -1)
    det = t.process(frame, 0.0)
    assert det.state == STATE_DRAW


def test_tracker_lost_on_empty(cfg):
    t = WandTracker(cfg)
    det = t.process(np.zeros((480, 640, 3), np.uint8), 0.0)
    assert det.state == STATE_LOST
    assert det.pos is None


def test_tracker_gate_rejects_far_jump(cfg):
    cfg["blob"]["gate_px"] = 80
    t = WandTracker(cfg)
    # establish last_pos near (100,100)
    t.process(_frame_with_blob((0, 255, 0), (100, 100)), 0.0)
    # now a large green blob far away plus the real one close: near one wins
    frame = _frame_with_blob((0, 255, 0), (110, 105))
    cv2.circle(frame, (500, 300), 20, (0, 255, 0), -1)
    det = t.process(frame, 0.033)
    assert det.raw[0] < 200 and det.raw[1] < 200


def test_tracker_circularity_rejects_line(cfg):
    cfg["blob"]["min_circularity"] = 0.7
    t = WandTracker(cfg)
    frame = np.zeros((480, 640, 3), np.uint8)
    cv2.rectangle(frame, (50, 150), (400, 158), (0, 255, 0), -1)  # thin bar
    det = t.process(frame, 0.0)
    assert det.state == STATE_LOST
