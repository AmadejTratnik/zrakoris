"""SaveWorker — all disk writes and printing, off the UI thread.

Directory layout (see AIRDRAW_SPEC.md section 11)::

    output/2026-09-12/
    ├── index.csv
    └── 0001_marek/
        ├── drawing.png
        └── session.json

Every file is written to a temp path and ``os.replace``-d onto the final name,
so an abruptly closed laptop never leaves a half-written file.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
from datetime import date
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from .paths import resolve_user_path
from .render import render_drawing
from .strokes import Drawing

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"[^a-z0-9_]")
_SESSION_DIR_RE = re.compile(r"^(\d{4})(?:_|$)")
INDEX_FIELDS = [
    "session_id", "name", "started_at", "ended_at",
    "stroke_count", "point_count", "printed",
]


def sanitize_name(name: str) -> str:
    """Filename-safe slug, ``[a-z0-9_]``, ≤24 chars. Empty in → empty out."""
    slug = _NAME_RE.sub("_", name.strip().lower().replace(" ", "_"))
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:24]


def session_id(counter: int, name: str) -> str:
    slug = sanitize_name(name)
    return f"{counter:04d}_{slug}" if slug else f"{counter:04d}"


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


class SaveWorker(QObject):
    """Move an instance to its own QThread and call :meth:`save` via a queued signal."""

    saved = pyqtSignal(str)           # session_id
    save_failed = pyqtSignal(str)     # error message

    def __init__(self, cfg: dict, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.output_dir = resolve_user_path(cfg["output"]["dir"])
        self.print_enabled = bool(cfg["output"].get("print_enabled", False))

    # ------------------------------------------------------------- helpers
    def day_dir(self) -> Path:
        d = self.output_dir / date.today().isoformat()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def next_counter(self, day_dir: Path) -> int:
        highest = 0
        for child in day_dir.iterdir():
            if child.is_dir():
                m = _SESSION_DIR_RE.match(child.name)
                if m:
                    highest = max(highest, int(m.group(1)))
        return highest + 1

    # --------------------------------------------------------------- save
    @pyqtSlot(object)
    def save(self, record: dict) -> None:
        try:
            sid = self._save(record)
            self.saved.emit(sid)
        except Exception as exc:  # noqa: BLE001 — surface everything to the operator
            log.exception("save failed")
            self.save_failed.emit(str(exc))

    def _save(self, record: dict) -> str:
        drawing: Drawing = record["drawing"]
        day = self.day_dir()
        counter = self.next_counter(day)
        sid = session_id(counter, record.get("name", ""))
        session_dir = day / sid
        session_dir.mkdir(parents=True, exist_ok=True)

        # session.json ----------------------------------------------------
        payload = {
            "session_id": sid,
            "name": record.get("name", ""),
            "started_at": record["started_at"],
            "ended_at": record["ended_at"],
            **drawing.to_json(),
        }
        _atomic_write_text(session_dir / "session.json",
                           json.dumps(payload, indent=2) + "\n")

        # drawing.png ---------------------------------------------------
        img = render_drawing(drawing)
        from PyQt5.QtCore import QBuffer, QByteArray
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.WriteOnly)
        img.save(buf, "PNG")
        buf.close()
        _atomic_write_bytes(session_dir / "drawing.png", bytes(ba))

        printed = False
        if self.print_enabled:
            try:
                self._print(img)
                printed = True
            except Exception:  # noqa: BLE001
                log.exception("printing failed (continuing)")

        # index.csv ---------------------------------------------------
        self._append_index(day, {
            "session_id": sid,
            "name": record.get("name", ""),
            "started_at": record["started_at"],
            "ended_at": record["ended_at"],
            "stroke_count": drawing.stroke_count,
            "point_count": drawing.point_count,
            "printed": int(printed),
        })

        log.info("saved %s (%d strokes, %d points)",
                 sid, drawing.stroke_count, drawing.point_count)
        return sid

    def _append_index(self, day_dir: Path, row: dict) -> None:
        index_path = day_dir / "index.csv"
        rows: list[dict] = []
        if index_path.exists():
            with open(index_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        rows.append({k: row.get(k, "") for k in INDEX_FIELDS})

        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=INDEX_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        _atomic_write_text(index_path, out.getvalue())

    def _print(self, img) -> None:
        """Render to a 1-bit bitmap sized for the printer and dispatch.

        Kept deliberately minimal; wire to a concrete printer at the venue.
        """
        from PyQt5.QtGui import QImage
        from PyQt5.QtPrintSupport import QPrinter
        from PyQt5.QtGui import QPainter

        printer = QPrinter(QPrinter.HighResolution)
        painter = QPainter(printer)
        page = printer.pageRect(QPrinter.DevicePixel)
        mono = img.convertToFormat(QImage.Format_Mono)
        scaled = mono.scaled(int(page.width()), int(page.height()),
                             aspectRatioMode=1)  # KeepAspectRatio
        painter.drawImage(0, 0, scaled)
        painter.end()
