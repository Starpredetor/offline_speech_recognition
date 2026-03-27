from __future__ import annotations

from dataclasses import dataclass

from core.stt.whisper_engine import TranscriptSegment
from core.utils import format_timestamp


@dataclass(slots=True)
class TimestampedLine:

    start: float
    end: float
    text: str

    def render(self) -> str:
        """Render the line with formatted timestamps.

        Returns:
            Formatted string like "[00:00 - 00:05] text here"
        """
        return f"[{format_timestamp(self.start)} - {format_timestamp(self.end)}] {self.text}"


class TimestampGenerator:

    def to_lines(self, segments: list[TranscriptSegment]) -> list[TimestampedLine]:
        """Convert segments to timestamped lines.

        Args:
            segments: List of TranscriptSegment objects

        Returns:
            List of TimestampedLine objects
        """
        lines: list[TimestampedLine] = []
        for segment in segments:
            if not segment.text:
                continue
            lines.append(TimestampedLine(start=segment.start, end=segment.end, text=segment.text))
        return lines
