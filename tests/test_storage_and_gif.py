"""Tests for storage helpers, session.json/PNG output, and the GIF timeline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.storage import SaveWorker, sanitize_name, session_id
from core.strokes import Drawing

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import render_gif  # noqa: E402


@pytest.mark.parametrize("raw, expected", [
    ("Marek", "marek"),
    ("  Ana  ", "ana"),
    ("José-Luis", "jos_luis"),
    ("a" * 40, "a" * 24),
    ("!!!", ""),
    ("", ""),
    ("Mary Jane", "mary_jane"),
])
def test_sanitize_name(raw, expected):
    assert sanitize_name(raw) == expected


@pytest.mark.parametrize("counter, name, expected", [
    (1, "Marek", "0001_marek"),
    (42, "", "0042"),
    (7, "  ", "0007"),
    (3, "!!!", "0003"),
])
def test_session_id(counter, name, expected):
    assert session_id(counter, name) == expected


@pytest.fixture
def cfg(tmp_path):
    from core import config as configmod
    c = configmod.load(tmp_path / "none.json")
    c["output"]["dir"] = str(tmp_path / "output")
    return c


def _needs_qt():
    try:
        import PyQt5.QtGui  # noqa: F401
        from PyQt5.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        return False
    except Exception:
        return True


@pytest.mark.skipif(_needs_qt(), reason="no Qt / no display")
def test_save_writes_all_files(cfg, tmp_path):
    worker = SaveWorker(cfg)
    d = Drawing(1920, 1080)
    s = d.begin_stroke("#1a1a1a", 10)
    for i in range(5):
        s.add(100 + i * 10, 200 + i * 5, i * 0.033)

    sid = worker._save({
        "name": "Marek",
        "started_at": "2026-09-12T10:00:00.000Z",
        "ended_at": "2026-09-12T10:01:00.000Z",
        "drawing": d,
    })
    assert sid == "0001_marek"

    day = next((Path(cfg["output"]["dir"])).iterdir())
    sess = day / "0001_marek"
    assert (sess / "session.json").exists()
    assert (sess / "drawing.png").exists()
    assert (day / "index.csv").exists()

    payload = json.loads((sess / "session.json").read_text())
    assert payload["session_id"] == "0001_marek"
    assert payload["strokes"][0]["points"][0][2] == 0.0
    assert payload["canvas"]["w"] == 1920

    # second save increments the counter
    sid2 = worker._save({
        "name": "Ana", "started_at": "x", "ended_at": "y", "drawing": Drawing(1920, 1080),
    })
    assert sid2 == "0002_ana"
    lines = (day / "index.csv").read_text().strip().splitlines()
    assert len(lines) == 3  # header + 2 rows

    # empty name -> clean "0003" dir, and the counter still advances past it
    sid3 = worker._save({
        "name": "", "started_at": "x", "ended_at": "y", "drawing": Drawing(1920, 1080),
    })
    assert sid3 == "0003"
    assert (day / "0003").is_dir()
    sid4 = worker._save({
        "name": "Bo", "started_at": "x", "ended_at": "y", "drawing": Drawing(1920, 1080),
    })
    assert sid4 == "0004_bo"


def test_gif_timeline_lays_strokes_end_to_end():
    strokes = [
        {"color": "#000", "width": 10, "points": [[0, 0, 0.0], [10, 0, 0.5]]},
        {"color": "#000", "width": 10, "points": [[0, 10, 0.0], [10, 10, 0.4]]},
    ]
    segs = render_gif._flatten_timeline(strokes)
    times = [s[0] for s in segs]
    assert times == sorted(times)
    assert times[0] == pytest.approx(0.5)
    assert times[-1] > 0.9  # second stroke offset past the first + gap


def test_gif_render_smoke(tmp_path):
    sj = tmp_path / "session.json"
    sj.write_text(json.dumps({
        "canvas": {"w": 1920, "h": 1080, "background": "#ffffff"},
        "strokes": [{"color": "#1a1a1a", "width": 10,
                     "points": [[100, 100, 0.0], [200, 200, 0.3], [300, 150, 0.6]]}],
    }))
    out = tmp_path / "drawing.gif"
    render_gif.render(sj, width=200, fps=10, speed=1.5, hold=0.5, out=out)
    assert out.exists() and out.stat().st_size > 0
