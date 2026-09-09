"""Stroke and Drawing data model. Pure Python, imports no Qt.

Strokes are the source of truth. The canvas QImage is a cache that can always be
rebuilt from these; the session.json holds exactly this data.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Stroke:
    color: str
    width: int
    # Each point is (x, y, t) with x/y in canvas coords and t in seconds since
    # this stroke started.
    points: list[tuple[float, float, float]] = field(default_factory=list)

    def add(self, x: float, y: float, t: float) -> None:
        self.points.append((float(x), float(y), float(t)))

    def __len__(self) -> int:
        return len(self.points)

    @property
    def is_drawable(self) -> bool:
        """A single point renders as a dot; zero points renders as nothing."""
        return len(self.points) >= 1

    def to_json(self) -> dict:
        return {
            "color": self.color,
            "width": self.width,
            "points": [[round(x, 2), round(y, 2), round(t, 3)] for x, y, t in self.points],
        }

    @classmethod
    def from_json(cls, d: dict) -> "Stroke":
        return cls(
            color=d["color"],
            width=int(d["width"]),
            points=[(float(p[0]), float(p[1]), float(p[2])) for p in d["points"]],
        )


@dataclass
class Drawing:
    """An ordered list of strokes plus the canvas geometry they live in."""

    canvas_w: int
    canvas_h: int
    background: str = "#ffffff"
    strokes: list[Stroke] = field(default_factory=list)

    # -- mutation -----------------------------------------------------------
    def begin_stroke(self, color: str, width: int) -> Stroke:
        s = Stroke(color=color, width=width)
        self.strokes.append(s)
        return s

    def undo(self) -> Stroke | None:
        """Pop the last stroke. Returns it, or None if there was nothing."""
        return self.strokes.pop() if self.strokes else None

    def clear(self) -> None:
        self.strokes.clear()

    # -- queries ----------------------------------------------------------
    @property
    def stroke_count(self) -> int:
        return len(self.strokes)

    @property
    def point_count(self) -> int:
        return sum(len(s) for s in self.strokes)

    # -- serialisation --------------------------------------------------
    def to_json(self) -> dict:
        return {
            "canvas": {"w": self.canvas_w, "h": self.canvas_h, "background": self.background},
            "strokes": [s.to_json() for s in self.strokes],
        }

    @classmethod
    def from_json(cls, d: dict) -> "Drawing":
        c = d["canvas"]
        return cls(
            canvas_w=int(c["w"]),
            canvas_h=int(c["h"]),
            background=c.get("background", "#ffffff"),
            strokes=[Stroke.from_json(s) for s in d["strokes"]],
        )
