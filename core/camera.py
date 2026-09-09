"""CaptureThread — owns the VideoCapture and runs the tracker off the UI thread.

Emits a small :class:`~core.tracker.Detection` every frame. Full BGR frames are
emitted *only* while calibrating — copying multi-megabyte buffers through the Qt
event loop at 30 fps stalls the UI.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import cv2
from PyQt5.QtCore import QThread, pyqtSignal

from .tracker import WandTracker

log = logging.getLogger(__name__)

_IS_WIN = sys.platform.startswith("win")


def _manual_ae() -> float:
    """'Manual exposure' magic value for this platform's capture backend.

    DirectShow (Windows) uses 0.25; V4L2 (Linux) uses 1 (3 = auto). Until this
    is set to manual, CAP_PROP_EXPOSURE is ignored by the driver.
    """
    return 0.25 if _IS_WIN else 1.0


def _auto_ae() -> float:
    return 0.75 if _IS_WIN else 3.0


def _wants_manual(cam: dict) -> bool:
    return float(cam.get("auto_exposure", 0)) not in (0.75, 3.0)


_EXPOSURE_LADDER = [1, 2, 3, 5, 8, 10, 15, 20, 30, 40, 60, 80, 100, 150, 200,
                    300, 400, 600, 800, 1200, 1600, 2500, 5000]


def nudge_exposure(current: float, up: bool) -> float:
    """Next exposure value on a rough log ladder (one operator button press).

    On Windows the property is a small negative log scale, so step by 1.
    """
    if _IS_WIN:
        return max(-13.0, min(0.0, current + (1 if up else -1)))
    steps = _EXPOSURE_LADDER
    i = min(range(len(steps)), key=lambda k: abs(steps[k] - current))
    i = max(0, min(len(steps) - 1, i + (1 if up else -1)))
    return float(steps[i])


def _camera_name(index: int) -> str:
    """Best-effort friendly label for a camera index, for the operator dropdown."""
    if not _IS_WIN:
        try:
            name = Path(f"/sys/class/video4linux/video{index}/name").read_text(
                encoding="utf-8").strip()
            if name:
                return f"{name} ({index})"
        except OSError:
            pass
    return f"Camera {index}"


def _camera_produces_frames(cap: cv2.VideoCapture, attempts: int = 20) -> bool:
    """True once ``cap`` actually delivers a frame, not just ``isOpened()``.

    Many UVC webcams expose a second, non-capture ``/dev/videoN`` node since
    Linux 4.16 (a metadata stream carrying per-frame timestamps/exposure, not
    video) alongside their real capture node. V4L2 happily opens that node —
    ``isOpened()`` reports true — but it can never produce a frame, and the
    capture loop would spin forever reading nothing. Laptop integrated cameras,
    on the other hand, can take up to a second to hand over the first frame, so
    give a genuinely slow starter time to wake before writing it off.
    """
    for _ in range(attempts):
        ok, frame = cap.read()
        if ok and frame is not None:
            return True
        time.sleep(0.05)
    return False


def _candidate_indices(max_probe: int) -> list[int]:
    """Indices worth probing.

    On Linux, only ``/dev/videoN`` nodes that actually exist — probing past
    the last one makes OpenCV fall through to its FFMPEG backend, which logs
    a scary (harmless) "index out of range" error to the console. Windows has
    no such device-node listing, so fall back to a plain 0..max_probe-1 range.
    """
    if _IS_WIN:
        return list(range(max_probe))
    indices = sorted(
        int(p.name[len("video"):]) for p in Path("/dev").glob("video*")
        if p.name[len("video"):].isdigit()
    )
    return indices[:max_probe]


def list_camera_devices(cfg: dict, max_probe: int = 8) -> list[tuple[int, str]]:
    """Enumerate camera indices for the operator's device dropdown.

    Call this before the capture thread starts (it does, in ``main.py``) — each
    candidate is actually opened, including the configured index, which would
    otherwise contend with the capture thread for the device.

    Every index that OpenCV can open is listed, even one that doesn't deliver a
    frame during the quick probe: some webcams (notably laptop integrated
    cameras) are slow to wake and only start streaming once selected for real,
    and hiding them left the operator with no way to pick the right device. An
    index that opened but stayed silent is still shown, tagged so the operator
    knows it's the less likely pick (a UVC metadata node reads the same way).
    """
    current = int(cfg["camera"]["index"])
    backend = cv2.CAP_DSHOW if _IS_WIN else cv2.CAP_ANY
    candidates = sorted(set(_candidate_indices(max_probe)) | {current})
    found: dict[int, str] = {}
    for i in candidates:
        cap = cv2.VideoCapture(i, backend)
        opened = cap.isOpened()
        has_frames = opened and _camera_produces_frames(cap)
        cap.release()
        if opened:
            name = _camera_name(i)
            found[i] = name if has_frames else f"{name} — no signal?"
    if not found:
        # nothing opened at all — surface the configured index anyway so the
        # dropdown isn't empty; opening it will fail visibly (the existing
        # "Could not open camera" dialog) rather than silently.
        found[current] = _camera_name(current)
    return sorted(found.items())


def apply_camera_props(cap: cv2.VideoCapture, cam: dict, *, geometry: bool = True) -> None:
    """Push exposure / white-balance / buffer settings, platform-aware."""
    if geometry:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam["width"])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam["height"])
        cap.set(cv2.CAP_PROP_FPS, cam["fps"])
    ae = _manual_ae() if _wants_manual(cam) else _auto_ae()
    # Some drivers need AUTO_EXPOSURE set both before and after EXPOSURE.
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, ae)
    cap.set(cv2.CAP_PROP_EXPOSURE, cam["exposure"])
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, ae)
    cap.set(cv2.CAP_PROP_AUTO_WB, cam["auto_wb"])
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)


class CaptureThread(QThread):
    detected = pyqtSignal(object)   # Detection — every frame
    preview = pyqtSignal(object)    # (bgr_frame, Detection) — only while calibrating
    failed = pyqtSignal(str)
    fps_updated = pyqtSignal(float)
    camera_switch_failed = pyqtSignal(int)   # index that failed to switch to

    def __init__(self, cfg: dict, tracker: WandTracker,
                 video_path: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.tracker = tracker
        self.video_path = video_path
        self._running = False
        self.emit_preview = False
        self._camera_update_pending = False
        self._camera_switch_target: int | None = None
        self._fps_ema = float(cfg["camera"].get("fps", 30))

    def request_camera_update(self) -> None:
        """Ask the capture loop to re-push exposure/WB (thread-safe flag)."""
        self._camera_update_pending = True

    def request_camera_switch(self, index: int) -> None:
        """Ask the capture loop to close the current device and open a different index."""
        self._camera_switch_target = int(index)

    # ------------------------------------------------------------------ open
    def _open_camera(self) -> cv2.VideoCapture | None:
        if self.video_path:
            cap = cv2.VideoCapture(self.video_path)
            if not cap.isOpened():
                log.error("could not open video %s", self.video_path)
                return None
            log.info("replaying video %s", self.video_path)
            return cap

        cam = self.cfg["camera"]
        backend = cv2.CAP_DSHOW if _IS_WIN else cv2.CAP_ANY
        cap = cv2.VideoCapture(int(cam["index"]), backend)
        if not cap.isOpened():
            log.error("could not open camera index %s", cam["index"])
            return None

        apply_camera_props(cap, cam, geometry=True)

        ae_got = cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)
        log.info(
            "camera opened: exposure req=%s got=%s | auto_exposure got=%s | auto_wb got=%s",
            cam["exposure"], cap.get(cv2.CAP_PROP_EXPOSURE), ae_got,
            cap.get(cv2.CAP_PROP_AUTO_WB),
        )
        if not _IS_WIN and _wants_manual(cam) and ae_got == 3.0:
            log.warning(
                "auto-exposure is still ON (V4L2 reads 3, wanted 1) — the driver "
                "refused manual mode. Check `v4l2-ctl -d /dev/video%s --list-ctrls`.",
                cam["index"],
            )
        if not _camera_produces_frames(cap):
            log.error(
                "camera index %s opened but delivered no frames — likely a "
                "non-capture device node (e.g. a UVC metadata node)", cam["index"])
            cap.release()
            return None
        return cap

    def apply_camera_settings(self, cap: cv2.VideoCapture) -> None:
        """Re-push exposure/WB after calibration changed them."""
        if self.video_path:
            return
        apply_camera_props(cap, self.cfg["camera"], geometry=False)

    # ------------------------------------------------------------------ loop
    def run(self) -> None:
        self._running = True
        # A bad *default* camera doesn't end the thread — it stays alive with
        # no device open so the operator can still pick a working one from
        # the panel's dropdown instead of hand-editing config.json + restart.
        self._cap = self._open_camera()
        if self._cap is None:
            self.failed.emit("Could not open camera")

        target_dt = 1.0 / max(float(self.cfg["camera"].get("fps", 30)), 1.0)
        last_frame_wall = time.monotonic()

        while self._running:
            if self._camera_switch_target is not None:
                target = self._camera_switch_target
                self._camera_switch_target = None
                self._switch_camera(target)

            if self._cap is None:
                time.sleep(0.05)   # no camera yet — waiting on a dropdown pick
                continue

            if self._camera_update_pending:
                self._camera_update_pending = False
                self.apply_camera_settings(self._cap)

            ok, frame = self._cap.read()
            if not ok:
                if self.video_path:
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # loop the clip
                    self.tracker.reset()
                    continue
                time.sleep(0.01)
                continue

            frame = cv2.flip(frame, 1)                    # stage 1: flip
            now = time.monotonic()
            det = self.tracker.process(frame, now)
            self.detected.emit(det)

            if self.emit_preview:
                self.preview.emit((frame, det))

            # fps meter (EMA)
            dt = now - last_frame_wall
            last_frame_wall = now
            if dt > 0:
                inst = 1.0 / dt
                self._fps_ema = 0.9 * self._fps_ema + 0.1 * inst
                self.fps_updated.emit(self._fps_ema)

            if self.video_path:
                # pace replay to the clip's fps so smoothing behaves as live
                sleep = target_dt - (time.monotonic() - now)
                if sleep > 0:
                    time.sleep(sleep)

        if self._cap is not None:
            self._cap.release()

    def _switch_camera(self, index: int) -> None:
        """Open a different camera index without a thread restart.

        ``cfg["camera"]["index"]`` is only committed to the new value once the
        new device has proven it can actually deliver frames (``_open_camera``
        checks this) — a bad pick (e.g. a UVC metadata node) leaves the working
        camera running instead of going dark.
        """
        if self.video_path:
            return
        old_index = self.cfg["camera"]["index"]
        self.cfg["camera"]["index"] = index
        new_cap = self._open_camera()
        if new_cap is None:
            self.cfg["camera"]["index"] = old_index
            log.error("camera switch to index %s failed — keeping index %s", index, old_index)
            self.camera_switch_failed.emit(index)
            return
        old_cap = self._cap
        self._cap = new_cap
        if old_cap is not None:
            old_cap.release()

    def stop(self) -> None:
        self._running = False
        self.wait(2000)
