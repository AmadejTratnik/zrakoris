"""AirDraw core: vision pipeline, data model, session logic, storage.

Nothing in this package except ``camera.py``, ``session.py`` and ``storage.py``
imports PyQt. ``tracker.py`` and ``filters.py`` are deliberately Qt-free so the
vision pipeline can be developed and tuned against a recorded video file.
"""
