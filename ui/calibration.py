"""Calibration dialog — F9 from any state, returns to the previous state.

Not pretty by design; it has to be fast to use with a queue of children waiting.
Live preview + both binary masks, HSV/exposure/blob sliders, a readout of the
HSV value at the detected blob centre, and a Save-to-config.json button.

Values tuned on a desk are wrong at the venue (ambient colour temperature, hall
exposure, battery sag dimming the LED across the day), so this must be reachable
without a code edit or restart.
"""

from __future__ import annotations

import sys

import cv2
import numpy as np
from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
    QSlider, QVBoxLayout, QWidget,
)

from core import config as configmod
from core.tracker import IDLE_COLOR, DRAW_COLOR
from ui.strings import tr


class _Slider(QWidget):
    def __init__(self, label: str, lo: int, hi: int, value: int, on_change) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel(label)
        self.name.setMinimumWidth(90)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(value)
        self.val = QLabel(str(value))
        self.val.setMinimumWidth(40)
        self.slider.valueChanged.connect(lambda v: (self.val.setText(str(v)), on_change()))
        row.addWidget(self.name)
        row.addWidget(self.slider)
        row.addWidget(self.val)

    def value(self) -> int:
        return self.slider.value()


class CalibrationDialog(QDialog):
    def __init__(self, cfg: dict, config_path: str, session, capture_thread,
                 tracker, on_closed=None, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.config_path = config_path
        self.session = session
        self.capture_thread = capture_thread
        self.tracker = tracker
        self._on_closed = on_closed
        self._last_det = None

        self.setWindowTitle(tr("title_calibration"))
        self.setModal(False)
        self._build_ui()

        session.enter_calibration()
        capture_thread.emit_preview = True
        capture_thread.preview.connect(self._on_preview)

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        images = QHBoxLayout()
        self.view_live = QLabel(tr("cal_live"))
        self.view_idle = QLabel(tr("cal_red_mask"))
        self.view_draw = QLabel(tr("cal_green_mask"))
        for v in (self.view_live, self.view_idle, self.view_draw):
            v.setFixedSize(320, 240)
            v.setAlignment(Qt.AlignCenter)
            v.setStyleSheet("background:#111;color:#888;")
            images.addWidget(v)
        root.addLayout(images)

        self.readout = QLabel(f"{tr('cal_hsv_readout')}: —")
        root.addWidget(self.readout)

        sliders = QHBoxLayout()
        sliders.addWidget(self._colour_box(tr("cal_red"), IDLE_COLOR, two_hue=True))
        sliders.addWidget(self._colour_box(tr("cal_green"), DRAW_COLOR, two_hue=False))
        sliders.addLayout(self._misc_box())
        root.addLayout(sliders)

        btns = QHBoxLayout()
        save_btn = QPushButton(tr("cal_save"))
        save_btn.clicked.connect(self._save)
        close_btn = QPushButton(tr("cal_close"))
        close_btn.clicked.connect(self.close)
        btns.addStretch(1)
        btns.addWidget(save_btn)
        btns.addWidget(close_btn)
        root.addLayout(btns)

    def _colour_box(self, title: str, key: str, two_hue: bool) -> QGroupBox:
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        ranges = self.cfg["colors"][key]["ranges"]
        self.__dict__.setdefault("_sliders", {})[key] = {}
        store = self._sliders[key]

        r0 = ranges[0]
        store["h_lo"] = _Slider(tr("cal_h_lo"), 0, 179, r0["lo"][0], self._on_slider)
        store["h_hi"] = _Slider(tr("cal_h_hi"), 0, 179, r0["hi"][0], self._on_slider)
        store["s_lo"] = _Slider(tr("cal_s_lo"), 0, 255, r0["lo"][1], self._on_slider)
        store["s_hi"] = _Slider(tr("cal_s_hi"), 0, 255, r0["hi"][1], self._on_slider)
        store["v_lo"] = _Slider(tr("cal_v_lo"), 0, 255, r0["lo"][2], self._on_slider)
        store["v_hi"] = _Slider(tr("cal_v_hi"), 0, 255, r0["hi"][2], self._on_slider)
        for name in ("h_lo", "h_hi", "s_lo", "s_hi", "v_lo", "v_hi"):
            lay.addWidget(store[name])

        if two_hue:
            r1 = ranges[1] if len(ranges) > 1 else r0
            store["h2_lo"] = _Slider(tr("cal_h2_lo"), 0, 179, r1["lo"][0], self._on_slider)
            store["h2_hi"] = _Slider(tr("cal_h2_hi"), 0, 179, r1["hi"][0], self._on_slider)
            lay.addWidget(store["h2_lo"])
            lay.addWidget(store["h2_hi"])
        return box

    def _misc_box(self) -> QVBoxLayout:
        col = QVBoxLayout()
        cam = self.cfg["camera"]
        blob = self.cfg["blob"]
        exp_lo, exp_hi = (-13, 0) if sys.platform.startswith("win") else (1, 2000)
        cur = int(cam["exposure"])
        self.sl_exposure = _Slider(tr("cal_exposure"), min(exp_lo, cur), max(exp_hi, cur),
                                   cur, self._on_exposure)
        self.sl_min_area = _Slider(tr("cal_min_area"), 1, 2000,
                                   int(blob["min_area"]), self._on_slider)
        self.sl_circ = _Slider(tr("cal_min_circ"), 0, 100,
                               int(blob["min_circularity"] * 100), self._on_slider)
        col.addWidget(self.sl_exposure)
        col.addWidget(self.sl_min_area)
        col.addWidget(self.sl_circ)
        col.addStretch(1)
        return col

    # -------------------------------------------------------------- update
    def _collect_into_cfg(self) -> None:
        for key in (IDLE_COLOR, DRAW_COLOR):
            s = self._sliders[key]
            ranges = [{
                "lo": [s["h_lo"].value(), s["s_lo"].value(), s["v_lo"].value()],
                "hi": [s["h_hi"].value(), s["s_hi"].value(), s["v_hi"].value()],
            }]
            if "h2_lo" in s:
                ranges.append({
                    "lo": [s["h2_lo"].value(), s["s_lo"].value(), s["v_lo"].value()],
                    "hi": [s["h2_hi"].value(), s["s_hi"].value(), s["v_hi"].value()],
                })
            self.cfg["colors"][key]["ranges"] = ranges

        self.cfg["blob"]["min_area"] = self.sl_min_area.value()
        self.cfg["blob"]["min_circularity"] = self.sl_circ.value() / 100.0
        self.cfg["camera"]["exposure"] = self.sl_exposure.value()

    def _on_slider(self) -> None:
        self._collect_into_cfg()
        self.tracker.configure(self.cfg)

    def _on_exposure(self) -> None:
        self._collect_into_cfg()
        self.capture_thread.request_camera_update()

    # -------------------------------------------------------------- preview
    @pyqtSlot(object)
    def _on_preview(self, payload) -> None:
        frame, det = payload
        self._last_det = det
        masks = self.tracker.debug_masks(frame)
        roi = self.cfg["roi"]
        crop = frame[roi["y"]:roi["y"] + roi["h"], roi["x"]:roi["x"] + roi["w"]]
        self._show(self.view_live, crop, colour=True)
        self._show(self.view_idle, masks[IDLE_COLOR], colour=False)
        self._show(self.view_draw, masks[DRAW_COLOR], colour=False)

        if det is not None and det.raw is not None:
            hsv = self.tracker.hsv_at(frame, det.raw)
            if hsv:
                self.readout.setText(
                    f"{tr('cal_hsv_readout')}: H={hsv[0]} S={hsv[1]} V={hsv[2]}   "
                    f"{tr('status_tracking')}={tr('track_' + det.state)}")

    def _show(self, label: QLabel, arr: np.ndarray, colour: bool) -> None:
        if colour:
            rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        else:
            h, w = arr.shape[:2]
            img = QImage(arr.data, w, h, w, QImage.Format_Grayscale8)
        label.setPixmap(QPixmap.fromImage(img.copy()).scaled(
            label.width(), label.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # ---------------------------------------------------------------- save
    def _save(self) -> None:
        self._collect_into_cfg()
        configmod.save(self.cfg, self.config_path)

    def closeEvent(self, event) -> None:
        try:
            self.capture_thread.preview.disconnect(self._on_preview)
        except TypeError:
            pass
        if self._on_closed is not None:
            self._on_closed()
        self.session.exit_calibration()
        super().closeEvent(event)
