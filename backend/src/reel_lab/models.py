"""Serializable domain models used across the pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class EnergyPoint:
    """Normalized track energy at a point in time."""

    time: float
    value: float


@dataclass
class AudioSection:
    """A musical section or energy region."""

    start: float
    end: float
    label: str
    energy: float


@dataclass
class AudioMap:
    """Music events used to build edit timelines."""

    source_path: str
    normalized_path: str
    analyzer: str
    duration: float
    bpm: float
    beats: list[float]
    downbeats: list[float]
    onsets: list[float]
    sections: list[AudioSection]
    energy: list[EnergyPoint]
    edit_points: list[float]
    notes: list[str] = field(default_factory=list)
    source_start: float = 0.0
    beat_confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AudioMap:
        """Create a map from its JSON representation."""
        values = dict(data)
        values["sections"] = [AudioSection(**item) for item in values["sections"]]
        values["energy"] = [EnergyPoint(**item) for item in values["energy"]]
        return cls(**values)


@dataclass
class MediaCandidate:
    """A usable photo or source-video interval."""

    id: str
    path: str
    kind: str
    start: float
    end: float
    width: int
    height: int
    quality: float
    intensity: float
    sharpness: float
    brightness: float
    focus_x: float = 0.5
    focus_y: float = 0.5
    peak_time: float | None = None
    stability: float = 1.0
    face_score: float = 0.0
    action_score: float = 0.0

    @property
    def duration(self) -> float:
        """Return candidate duration in seconds."""
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""
        return asdict(self)


@dataclass
class TimelineItem:
    """One visual item placed on the music timeline."""

    timeline_start: float
    timeline_end: float
    candidate_id: str
    path: str
    kind: str
    source_start: float
    source_end: float
    width: int
    height: int
    score: float
    focus_x: float = 0.5
    focus_y: float = 0.5
    motion: str = "none"

    @property
    def duration(self) -> float:
        """Return item duration in seconds."""
        return self.timeline_end - self.timeline_start


@dataclass
class Variant:
    """A complete edit decision list for one creative direction."""

    id: int
    name: str
    description: str
    seed: int
    score: float
    items: list[TimelineItem]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Variant:
        """Create a variant from its JSON representation."""
        values = dict(data)
        values["items"] = [TimelineItem(**item) for item in values["items"]]
        return cls(**values)
