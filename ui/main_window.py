"""Operator panel — laptop screen only. Never shows on the TV.

Name field, large transport buttons, live status, a toggleable mask preview, and
the keyboard shortcuts the operator will actually use.
"""

from __future__ import annotations

import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QImage, QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QShortcut, QVBoxLayout, QWidget,
)

from core.camera import list_camera_devices, nudge_exposure
from core.session import State
from core.tracker import IDLE_COLOR, DRAW_COLOR
from ui.strings import tr


def _readable_text_color(hex_color: str) -> str:
    """Black or white label text, whichever reads better on ``hex_color``."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#ffffff" if luminance < 140 else "#111827"


_ENABLED_BY_STATE: dict[State, set[str]] = {
    State.IDLE:    {"new"},
    State.ENTRY:   {"start", "cancel", "name"},
    State.DRAWING: {"stop", "undo", "clear"},
    State.REVIEW:  {"save", "discard", "resume", "undo"},
    State.CALIBRATING: set(),
}


class MainWindow(QMainWindow):
    # Closing the operator panel shuts the whole app down (canvas included).
    app_closing = pyqtSignal()

    def __init__(self, cfg: dict, session, capture_thread, tracker,
                 open_calibration, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.session = session
        self.capture_thread = capture_thread
        self.tracker = tracker
        self.open_calibration = open_calibration
        self.session_count = 0

        self.setWindowTitle(tr("title_operator"))
        self._build_ui()
        self._wire_signals()
        self._wire_shortcuts()
        self._apply_state(State.IDLE)

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # name
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(tr("name_label")))
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(24)
        self.name_edit.setPlaceholderText(tr("name_placeholder"))
        name_row.addWidget(self.name_edit)
        root.addLayout(name_row)

        # camera device
        cam_row = QHBoxLayout()
        cam_row.addWidget(QLabel(tr("camera_label")))
        self.camera_combo = QComboBox()
        for index, label in list_camera_devices(self.cfg):
            self.camera_combo.addItem(label, index)
        current = self.camera_combo.findData(int(self.cfg["camera"]["index"]))
        if current >= 0:
            self.camera_combo.setCurrentIndex(current)
        cam_row.addWidget(self.camera_combo, 1)
        root.addLayout(cam_row)

        # transport buttons
        self.buttons: dict[str, QPushButton] = {}
        grid = QGridLayout()
        specs = [
            ("new", f"{tr('btn_new')} (Ctrl+N)", 0, 0),
            ("start", f"{tr('btn_start')} (Space)", 0, 1),
            ("stop", f"{tr('btn_stop')} (Space)", 0, 2),
            ("resume", tr("btn_resume"), 1, 0),
            ("save", f"{tr('btn_save')} (Ctrl+S)", 1, 1),
            ("discard", f"{tr('btn_discard')} (Ctrl+D)", 1, 2),
            ("undo", f"{tr('btn_undo')} (Ctrl+Z)", 2, 0),
            ("clear", tr("btn_clear"), 2, 1),
            ("cancel", tr("btn_cancel"), 2, 2),
        ]
        for key, label, r, col in specs:
            b = QPushButton(label)
            b.setMinimumHeight(56)
            grid.addWidget(b, r, col)
            self.buttons[key] = b
        root.addLayout(grid)

        self.buttons["new"].clicked.connect(self.session.new_session)
        self.buttons["start"].clicked.connect(self.session.start)
        self.buttons["stop"].clicked.connect(self.session.stop)
        self.buttons["resume"].clicked.connect(self.session.resume)
        self.buttons["save"].clicked.connect(self.session.save)
        self.buttons["discard"].clicked.connect(self.session.discard)
        self.buttons["undo"].clicked.connect(self.session.undo)
        self.buttons["clear"].clicked.connect(self.session.clear)
        self.buttons["cancel"].clicked.connect(self.session.cancel)

        # live exposure nudge — retuning without opening F9 (battery sag, hall light)
        exp_row = QHBoxLayout()
        self.btn_exp_down = QPushButton("−")   # minus sign
        self.btn_exp_up = QPushButton("+")
        for b in (self.btn_exp_down, self.btn_exp_up):
            b.setFixedWidth(44)
        self.btn_exp_down.setToolTip(tr("btn_exp_down"))
        self.btn_exp_up.setToolTip(tr("btn_exp_up"))
        self.lbl_exposure = QLabel(str(self.cfg["camera"]["exposure"]))
        self.lbl_exposure.setMinimumWidth(40)
        self.btn_exp_down.clicked.connect(lambda: self._nudge_exposure(False))
        self.btn_exp_up.clicked.connect(lambda: self._nudge_exposure(True))
        exp_row.addWidget(QLabel(tr("status_exposure") + ":"))
        exp_row.addWidget(self.btn_exp_down)
        exp_row.addWidget(self.lbl_exposure)
        exp_row.addWidget(self.btn_exp_up)
        exp_row.addStretch(1)
        root.addLayout(exp_row)

        # drawing color palette — keys 1-9
        color_box = QGroupBox(tr("color_box"))
        color_row = QHBoxLayout(color_box)
        self.color_buttons: list[QPushButton] = []
        self._palette = list(self.cfg["stroke"].get("palette", []))[:9]
        for i, color in enumerate(self._palette, start=1):
            b = QPushButton(str(i))
            b.setFixedSize(40, 40)
            b.clicked.connect(lambda _checked=False, c=color: self.session.set_stroke_color(c))
            color_row.addWidget(b)
            self.color_buttons.append(b)
        color_row.addStretch(1)
        root.addWidget(color_box)
        self._on_color_changed(self.session.stroke_color)

        # status
        status_box = QGroupBox(tr("status_box"))
        s = QGridLayout(status_box)
        self.lbl_state = QLabel(tr("state_IDLE"))
        self.lbl_tracking = QLabel(tr("track_lost"))
        self.lbl_fps = QLabel("0.0")
        self.lbl_strokes = QLabel("0")
        self.lbl_counter = QLabel("0")
        for i, (name, w) in enumerate([
            (tr("status_state"), self.lbl_state), (tr("status_tracking"), self.lbl_tracking),
            (tr("status_fps"), self.lbl_fps), (tr("status_strokes"), self.lbl_strokes),
            (tr("status_saved"), self.lbl_counter),
        ]):
            s.addWidget(QLabel(name + ":"), i, 0)
            s.addWidget(w, i, 1)
        root.addWidget(status_box)

        # idle banner
        self.idle_banner = QLabel(tr("idle_banner"))
        self.idle_banner.setStyleSheet(
            "background:#fde68a; color:#7c2d12; padding:8px; font-weight:bold;")
        self.idle_banner.setVisible(False)
        root.addWidget(self.idle_banner)

        # mask preview
        self.mask_toggle = QCheckBox(tr("mask_toggle"))
        self.mask_toggle.toggled.connect(self._on_mask_toggle)
        root.addWidget(self.mask_toggle)
        self.mask_label = QLabel()
        self.mask_label.setFixedHeight(200)
        self.mask_label.setAlignment(Qt.AlignCenter)
        self.mask_label.setVisible(False)
        root.addWidget(self.mask_label)

        root.addStretch(1)

    # -------------------------------------------------------------- wiring
    def _wire_signals(self) -> None:
        self.session.state_changed.connect(self._apply_state)
        self.session.stats_changed.connect(self._on_stats)
        self.session.idle_warning.connect(self.idle_banner.setVisible)
        self.session.session_saved.connect(lambda *_: None)
        self.name_edit.textChanged.connect(self.session.set_name)
        self.capture_thread.fps_updated.connect(
            lambda f: self.lbl_fps.setText(f"{f:.1f}"))
        self.capture_thread.detected.connect(self._on_detection)
        self.capture_thread.preview.connect(self._on_preview)
        self.camera_combo.currentIndexChanged.connect(self._on_camera_changed)
        self.capture_thread.camera_switch_failed.connect(self._on_camera_switch_failed)
        self.session.color_changed.connect(self._on_color_changed)
        if getattr(self.capture_thread, "video_path", None):
            for b in (self.btn_exp_down, self.btn_exp_up):
                b.setEnabled(False)
                b.setToolTip("n/a in --video mode")
            self.camera_combo.setEnabled(False)
            self.camera_combo.setToolTip("n/a in --video mode")

    def _wire_shortcuts(self) -> None:
        def sc(seq, fn):
            QShortcut(QKeySequence(seq), self, activated=fn)

        sc("Ctrl+N", self.session.new_session)
        sc("Space", self.session.toggle_start_stop)
        sc("Ctrl+Z", self.session.undo)
        sc("Ctrl+S", self.session.save)
        sc("Ctrl+D", self.session.discard)
        sc("F9", self.open_calibration)
        for i, color in enumerate(self._palette, start=1):
            sc(str(i), lambda c=color: self.session.set_stroke_color(c))

    # --------------------------------------------------------------- slots
    @pyqtSlot(object)
    def _apply_state(self, state: State) -> None:
        self.lbl_state.setText(tr(f"state_{state.name}"))
        allowed = _ENABLED_BY_STATE.get(state, set())
        for key, btn in self.buttons.items():
            btn.setEnabled(key in allowed)
        self.name_edit.setEnabled("name" in allowed)
        if state is State.ENTRY:
            self.name_edit.setFocus()
            self.name_edit.selectAll()

    @pyqtSlot(dict)
    def _on_stats(self, stats: dict) -> None:
        self.lbl_strokes.setText(str(stats["stroke_count"]))

    @pyqtSlot(object)
    def _on_detection(self, det) -> None:
        self.lbl_tracking.setText(tr(f"track_{det.state}"))

    # --------------------------------------------------------- exposure
    def _nudge_exposure(self, up: bool) -> None:
        cam = self.cfg["camera"]
        cam["exposure"] = nudge_exposure(float(cam["exposure"]), up)
        self.lbl_exposure.setText(f"{cam['exposure']:g}")
        self.capture_thread.request_camera_update()

    # ------------------------------------------------------------- camera
    def _on_camera_changed(self, idx: int) -> None:
        device_index = self.camera_combo.itemData(idx)
        if device_index is None:
            return
        # cfg["camera"]["index"] is updated by the capture thread itself, only
        # once the new device has proven it can deliver frames.
        self.capture_thread.request_camera_switch(int(device_index))

    def _on_camera_switch_failed(self, index: int) -> None:
        # revert the dropdown to whatever camera is actually still running
        current = self.camera_combo.findData(int(self.cfg["camera"]["index"]))
        if current >= 0:
            self.camera_combo.blockSignals(True)
            self.camera_combo.setCurrentIndex(current)
            self.camera_combo.blockSignals(False)
        QMessageBox.warning(self, tr("msg_camera_title"), tr("msg_camera_switch_fail"))

    # -------------------------------------------------------------- color
    def _on_color_changed(self, color: str) -> None:
        for btn, c in zip(self.color_buttons, self._palette):
            selected = c == color
            border = "3px solid #111827" if selected else "1px solid #6b7280"
            btn.setStyleSheet(
                f"background:{c}; color:{_readable_text_color(c)}; "
                f"border:{border}; font-weight:bold;")

    def set_session_count(self, n: int) -> None:
        self.session_count = n
        self.lbl_counter.setText(str(n))

    def note_saved(self) -> None:
        self.session_count += 1
        self.lbl_counter.setText(str(self.session_count))

    # ---------------------------------------------------------- mask preview
    def _on_mask_toggle(self, on: bool) -> None:
        self.mask_label.setVisible(on)
        self.capture_thread.emit_preview = on

    def sync_preview(self) -> None:
        """Called when the calibration dialog closes, to restore preview state."""
        self.capture_thread.emit_preview = self.mask_toggle.isChecked()

    @pyqtSlot(object)
    def _on_preview(self, payload) -> None:
        if not self.mask_toggle.isChecked():
            return
        frame, _det = payload
        masks = self.tracker.debug_masks(frame)
        combo = np.zeros((*masks[IDLE_COLOR].shape, 3), dtype=np.uint8)
        combo[..., 2] = masks[IDLE_COLOR]   # red channel
        combo[..., 1] = masks[DRAW_COLOR]   # green channel
        h, w = combo.shape[:2]
        img = QImage(combo.data, w, h, 3 * w, QImage.Format_BGR888).copy()
        self.mask_label.setPixmap(QPixmap.fromImage(img).scaled(
            self.mask_label.width(), self.mask_label.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # ------------------------------------------------------------- kiosk
    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and not self.cfg.get("_kiosk", False):
            self.window().showNormal()
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self.app_closing.emit()
        super().closeEvent(event)
