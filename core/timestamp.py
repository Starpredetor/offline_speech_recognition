from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.utils import format_timestamp


class TranscriptSegmentLike(Protocol):
    start: float
    end: float
    text: str


@dataclass(slots=True)
class TimestampedLine:

    start: float
    end: float
    text: str

    def render(self) -> str:
        return f"[{format_timestamp(self.start)} - {format_timestamp(self.end)}] {self.text}"


class TimestampGenerator:

    def to_lines(self, segments: list[TranscriptSegmentLike]) -> list[TimestampedLine]:
        lines: list[TimestampedLine] = []
        for segment in segments:
            if not segment.text:
                continue
            lines.append(TimestampedLine(start=segment.start, end=segment.end, text=segment.text))
        return lines
