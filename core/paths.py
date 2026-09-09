"""Path resolution that works both from source and from a PyInstaller build.

* ``app_dir()``    — where user-facing files live (``config.json``, ``output/``).
                     Next to the ``.exe`` when frozen; the repo root from source.
* ``bundle_dir()`` — where read-only bundled resources live (``sys._MEIPASS``
                     when frozen).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _REPO_ROOT


def bundle_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return _REPO_ROOT


def resource_path(rel: str) -> Path:
    """Absolute path to a bundled read-only resource."""
    return bundle_dir() / rel


def resolve_user_path(value: str | Path) -> Path:
    """Resolve a possibly-relative user path against :func:`app_dir`."""
    p = Path(value).expanduser()
    return p if p.is_absolute() else (app_dir() / p)
