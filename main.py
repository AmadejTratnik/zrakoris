"""AirDraw entry point — wires the three threads and two windows together.

    python main.py                       # uses config.json
    python main.py --config venue.json   # alternate config
    python main.py --no-tv               # single-screen dev mode
    python main.py --video clip.mp4      # replay a recording instead of a camera
    python main.py --kiosk               # disable Esc / dev conveniences
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from PyQt5.QtCore import Qt, QThread


def _fix_qt_plugin_path() -> None:
    """Point Qt at PyQt5's own plugins.

    ``opencv-python`` bundles its own (often broken) copy of the Qt platform
    plugins and, on import, sets ``QT_QPA_PLATFORM_PLUGIN_PATH`` to it — which
    then shadows PyQt5's working ``libqxcb.so`` and the GUI fails to start with
    "Could not load the Qt platform plugin xcb". Force the path back to PyQt5.
    Must run after cv2 is imported and before QApplication is constructed.
    """
    if sys.platform.startswith("win") or "QT_QPA_PLATFORM" in os.environ:
        return
    if getattr(sys, "frozen", False):
        return  # PyInstaller bundles a single consistent set of Qt plugins
    try:
        from PyQt5.QtCore import QLibraryInfo
        plugins = QLibraryInfo.location(QLibraryInfo.PluginsPath)
    except Exception:  # noqa: BLE001
        return
    if plugins and os.path.isdir(plugins):
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = plugins
        os.environ["QT_PLUGIN_PATH"] = plugins


from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core import config as configmod
from core import paths
from core.camera import CaptureThread
from core.session import SessionController
from core.storage import SaveWorker
from core.tracker import WandTracker
from ui.calibration import CalibrationDialog
from ui.canvas_window import CanvasWindow
from ui.main_window import MainWindow
from ui.strings import set_language, tr

log = logging.getLogger("airdraw")


def resolve_config_path(value: str) -> str:
    """Locate config.json next to the exe/repo; seed it from the bundled default.

    Keeps the operator's editable config beside the executable so calibration
    can write to it and a technician can hand-edit it without touching a bundle.
    """
    p = paths.resolve_user_path(value)
    if not p.exists():
        default = paths.resource_path("config.json")
        if default.exists() and default != p:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(default.read_bytes())
            log.info("seeded %s from the bundled default", p)
    return str(p)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="airdraw")
    p.add_argument("--config", default="config.json")
    p.add_argument("--no-tv", action="store_true",
                   help="single-screen dev mode: canvas as a normal window")
    p.add_argument("--video", default=None,
                   help="replay a recorded clip instead of opening a camera")
    p.add_argument("--mouse", action="store_true",
                   help="M3 dev mode: mouse on the canvas stands in for the tracker")
    p.add_argument("--kiosk", action="store_true",
                   help="disable Esc-to-windowed and other dev conveniences")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def place_windows(app: QApplication, main_win: MainWindow,
                  canvas_win: CanvasWindow, no_tv: bool) -> None:
    screens = app.screens()
    primary = app.primaryScreen()

    main_win.setGeometry(primary.availableGeometry().adjusted(40, 40, -40, -40))
    main_win.show()

    if no_tv or len(screens) < 2:
        if not no_tv:
            log.warning("only one screen detected — canvas will overlap the panel; "
                        "use --no-tv for dev")
        canvas_win.resize(960, 540)
        canvas_win.move(primary.geometry().center())
        canvas_win.show()
    else:
        tv = screens[1]
        canvas_win.setGeometry(tv.geometry())
        canvas_win.showFullScreen()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    config_path = resolve_config_path(args.config)
    cfg = configmod.load(config_path)
    cfg["_kiosk"] = args.kiosk
    set_language(cfg.get("ui", {}).get("language", "sl"))

    _fix_qt_plugin_path()
    app = QApplication(sys.argv)
    app.setApplicationName("AirDraw")

    tracker = WandTracker(cfg)
    capture_thread = CaptureThread(cfg, tracker, video_path=args.video)
    session = SessionController(cfg, tracker)
    canvas_win = CanvasWindow(cfg, mouse_input=args.mouse)

    # save worker on its own thread
    save_thread = QThread()
    save_worker = SaveWorker(cfg)
    save_worker.moveToThread(save_thread)
    save_thread.start()

    # --- signal wiring -----------------------------------------------------
    if args.mouse:
        canvas_win.mouse_detection.connect(session.on_detection)
    else:
        capture_thread.detected.connect(session.on_detection, Qt.QueuedConnection)
    session.stroke_extended.connect(canvas_win.on_stroke_extended)
    session.stroke_ended.connect(canvas_win.on_stroke_ended)
    session.rerender.connect(canvas_win.on_rerender)
    session.cursor_moved.connect(canvas_win.on_cursor)
    session.state_changed.connect(canvas_win.on_state_changed)
    session.color_changed.connect(canvas_win.on_color_changed)

    session.session_saved.connect(save_worker.save, Qt.QueuedConnection)

    def _on_capture_failed(msg: str) -> None:
        QMessageBox.critical(None, tr("msg_camera_title"), tr("msg_camera_fail"))

    capture_thread.failed.connect(_on_capture_failed)

    # calibration dialog factory (F9)
    state = {"dialog": None}

    def open_calibration() -> None:
        if state["dialog"] is not None:
            state["dialog"].raise_()
            return
        dlg = CalibrationDialog(
            cfg, config_path, session, capture_thread, tracker,
            on_closed=lambda: main_win.sync_preview(),
        )

        def _cleared() -> None:
            state["dialog"] = None

        dlg.finished.connect(lambda *_: _cleared())
        state["dialog"] = dlg
        dlg.show()

    main_win = MainWindow(cfg, session, capture_thread, tracker, open_calibration)
    try:
        day = save_worker.day_dir()
        main_win.set_session_count(save_worker.next_counter(day) - 1)
    except OSError:
        log.warning("could not read the day's output dir for the session counter")
    if args.mouse:
        canvas_win.mouse_detection.connect(main_win._on_detection)
    save_worker.saved.connect(lambda *_: main_win.note_saved())
    save_worker.save_failed.connect(
        lambda m: QMessageBox.warning(main_win, tr("msg_save_fail_title"), m))

    # Closing the operator panel (or the canvas) tears the whole app down.
    main_win.app_closing.connect(canvas_win.close)
    main_win.app_closing.connect(app.quit)

    def _shutdown() -> None:
        if not args.mouse:
            capture_thread.stop()
        save_thread.quit()
        save_thread.wait(3000)

    app.aboutToQuit.connect(_shutdown)

    place_windows(app, main_win, canvas_win, args.no_tv or args.mouse)

    if not args.mouse:
        capture_thread.start()

    try:
        return app.exec_()
    finally:
        _shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
