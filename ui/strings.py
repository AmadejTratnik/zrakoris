"""Tiny UI translation layer.

This is a single-locale booth app, so there is no gettext / Qt Linguist
machinery — just two flat dicts and a lookup. Default language is Slovenian
(``sl``); set ``ui.language`` in ``config.json`` to ``"en"`` for English.

Usage::

    from ui.strings import tr, set_language
    set_language(cfg["ui"]["language"])
    button.setText(tr("btn_save"))

An unknown key returns the key itself, so a missing translation is visible
rather than crashing.
"""

from __future__ import annotations

_LANG = "sl"

_TR: dict[str, dict[str, str]] = {
    "en": {
        # window titles
        "title_operator": "AirDraw — operator",
        "title_calibration": "AirDraw — calibration (F9)",
        # operator panel — name
        "name_label": "First name:",
        "name_placeholder": "first name only",
        "camera_label": "Camera:",
        "color_box": "Color (keys 1-9)",
        # operator panel — buttons
        "btn_new": "New",
        "btn_start": "Start",
        "btn_stop": "Stop",
        "btn_resume": "Resume",
        "btn_save": "Save",
        "btn_discard": "Discard",
        "btn_undo": "Undo",
        "btn_clear": "Clear",
        "btn_cancel": "Cancel",
        "btn_exp_down": "Exposure −",
        "btn_exp_up": "Exposure +",
        # operator panel — status
        "status_box": "Status",
        "status_state": "Session state",
        "status_tracking": "Tracking",
        "status_fps": "FPS",
        "status_strokes": "Strokes",
        "status_saved": "Saved today",
        "status_exposure": "Exposure",
        # operator panel — misc
        "idle_banner": "No wand seen for a while — wrap up this session?",
        "mask_toggle": "Show mask preview (diagnostic)",
        # canvas
        "canvas_invitation": "Pick up the wand and press the button to draw",
        # calibration
        "cal_hsv_readout": "HSV at blob",
        "cal_save": "Save to config.json",
        "cal_close": "Close",
        "cal_red": "Red (idle)",
        "cal_green": "Green (draw)",
        "cal_live": "live",
        "cal_red_mask": "red mask",
        "cal_green_mask": "green mask",
        "cal_h_lo": "H low", "cal_h_hi": "H high",
        "cal_s_lo": "S low", "cal_s_hi": "S high",
        "cal_v_lo": "V low", "cal_v_hi": "V high",
        "cal_h2_lo": "H2 low", "cal_h2_hi": "H2 high",
        "cal_exposure": "Exposure",
        "cal_min_area": "Min area",
        "cal_min_circ": "Min circ x100",
        # dialogs
        "msg_camera_title": "AirDraw — camera",
        "msg_camera_fail": "Could not open camera",
        "msg_camera_switch_fail": "Could not switch to that camera — it opened but sent "
                                   "no video (often a non-video helper device paired with "
                                   "the real camera). Keeping the previous camera.",
        "msg_save_fail_title": "AirDraw — save failed",
        # state names
        "state_IDLE": "IDLE",
        "state_ENTRY": "ENTRY",
        "state_DRAWING": "DRAWING",
        "state_REVIEW": "REVIEW",
        "state_CALIBRATING": "CALIBRATING",
        # tracking states
        "track_draw": "draw",
        "track_hover": "hover",
        "track_lost": "lost",
    },
    "sl": {
        "title_operator": "AirDraw — upravljanje",
        "title_calibration": "AirDraw — umerjanje (F9)",
        "name_label": "Ime:",
        "name_placeholder": "samo ime",
        "camera_label": "Kamera:",
        "color_box": "Barva (tipke 1-9)",
        "btn_new": "Nova risba",
        "btn_start": "Začni",
        "btn_stop": "Ustavi",
        "btn_resume": "Nadaljuj",
        "btn_save": "Shrani",
        "btn_discard": "Zavrzi",
        "btn_undo": "Razveljavi",
        "btn_clear": "Počisti",
        "btn_cancel": "Prekliči",
        "btn_exp_down": "Osvetlitev −",
        "btn_exp_up": "Osvetlitev +",
        "status_box": "Stanje",
        "status_state": "Stanje seje",
        "status_tracking": "Sledenje",
        "status_fps": "FPS",
        "status_strokes": "Poteze",
        "status_saved": "Shranjeno danes",
        "status_exposure": "Osvetlitev",
        "idle_banner": "Paličice že nekaj časa ni videti — naj zaključim sejo?",
        "mask_toggle": "Prikaži masko (diagnostika)",
        "canvas_invitation": "Primi paličico in pritisni gumb za risanje",
        "cal_hsv_readout": "HSV na piki",
        "cal_save": "Shrani v config.json",
        "cal_close": "Zapri",
        "cal_red": "Rdeča (mirovanje)",
        "cal_green": "Zelena (risanje)",
        "cal_live": "v živo",
        "cal_red_mask": "rdeča maska",
        "cal_green_mask": "zelena maska",
        "cal_h_lo": "H nizko", "cal_h_hi": "H visoko",
        "cal_s_lo": "S nizko", "cal_s_hi": "S visoko",
        "cal_v_lo": "V nizko", "cal_v_hi": "V visoko",
        "cal_h2_lo": "H2 nizko", "cal_h2_hi": "H2 visoko",
        "cal_exposure": "Osvetlitev",
        "cal_min_area": "Najm. površina",
        "cal_min_circ": "Najm. okroglost x100",
        "msg_camera_title": "AirDraw — kamera",
        "msg_camera_fail": "Kamere ni mogoče odpreti",
        "msg_camera_switch_fail": "Preklop na to kamero ni uspel — naprava se je odprla, "
                                   "a ni poslala slike (pogosto gre za pomožno napravo ob "
                                   "pravi kameri). Ohranjam prejšnjo kamero.",
        "msg_save_fail_title": "AirDraw — shranjevanje ni uspelo",
        "state_IDLE": "Mirovanje",
        "state_ENTRY": "Vnos imena",
        "state_DRAWING": "Risanje",
        "state_REVIEW": "Pregled",
        "state_CALIBRATING": "Umerjanje",
        "track_draw": "riše",
        "track_hover": "lebdi",
        "track_lost": "izgubljen",
    },
}


def set_language(lang: str | None) -> None:
    global _LANG
    if lang in _TR:
        _LANG = lang


def language() -> str:
    return _LANG


def tr(key: str) -> str:
    return _TR.get(_LANG, {}).get(key) or _TR["en"].get(key) or key
