from __future__ import annotations

from datetime import timedelta
from pathlib import Path


def ensure_directory(path: Path) -> None:
    """Create directory if it doesn't exist.

    Args:
        path: Directory path to create
    """
    path.mkdir(parents=True, exist_ok=True)


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS or MM:SS.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted timestamp string
    """
    total = int(max(seconds, 0))
    td = timedelta(seconds=total)
    minutes, sec = divmod(td.seconds, 60)
    hours = td.seconds // 3600
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def print_section(title: str) -> None:
    """Print a formatted section header.

    Args:
        title: Section title to display
    """
    print(f"\n=== {title} ===")
