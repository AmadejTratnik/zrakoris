"""One Euro filter (Casiez, Roussel & Vogel, 2012).

Pure Python + math. Imports no Qt, no numpy. One instance per scalar axis.

Why not an EMA: an exponential moving average forces a single trade-off between
jitter when the hand is still and lag when it moves fast. The One Euro filter
adapts its cutoff frequency to the measured velocity, so it can be smooth at
rest and responsive in motion at the same time.
"""

from __future__ import annotations

import math


class _LowPass:
    """First-order low-pass. ``alpha`` is recomputed by the caller each step."""

    def __init__(self) -> None:
        self.y: float | None = None

    def __call__(self, x: float, alpha: float) -> float:
        self.y = x if self.y is None else alpha * x + (1.0 - alpha) * self.y
        return self.y

    def reset(self) -> None:
        self.y = None


class OneEuroFilter:
    """Adaptive low-pass filter operating on a single scalar."""

    def __init__(
        self,
        freq: float = 30.0,
        min_cutoff: float = 1.0,
        beta: float = 0.01,
        d_cutoff: float = 1.0,
    ) -> None:
        self.freq = freq
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x = _LowPass()
        self._dx = _LowPass()
        self._last_time: float | None = None
        self._last_x: float | None = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self) -> None:
        """Clear all history.

        Call this whenever tracking is lost or the session state changes,
        otherwise the filter interpolates smoothly across the gap and paints a
        line where there should not be one.
        """
        self._x.reset()
        self._dx.reset()
        self._last_time = None
        self._last_x = None

    def __call__(self, x: float, timestamp: float) -> float:
        if self._last_time is not None and timestamp > self._last_time:
            self.freq = 1.0 / (timestamp - self._last_time)
        self._last_time = timestamp
        dt = 1.0 / max(self.freq, 1e-6)

        dx = 0.0 if self._last_x is None else (x - self._last_x) * self.freq
        self._last_x = x
        edx = self._dx(dx, self._alpha(self.d_cutoff, dt))

        cutoff = self.min_cutoff + self.beta * abs(edx)
        return self._x(x, self._alpha(cutoff, dt))


class PointFilter:
    """Convenience wrapper: one :class:`OneEuroFilter` per axis."""

    def __init__(self, freq: float = 30.0, min_cutoff: float = 1.0,
                 beta: float = 0.01, d_cutoff: float = 1.0) -> None:
        self._fx = OneEuroFilter(freq, min_cutoff, beta, d_cutoff)
        self._fy = OneEuroFilter(freq, min_cutoff, beta, d_cutoff)

    def reset(self) -> None:
        self._fx.reset()
        self._fy.reset()

    def __call__(self, p: tuple[float, float], timestamp: float) -> tuple[float, float]:
        return self._fx(p[0], timestamp), self._fy(p[1], timestamp)
