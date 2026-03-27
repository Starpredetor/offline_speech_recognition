from __future__ import annotations

import sys
import json
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt, QTimer, QRect, QPoint
from PySide6.QtGui import QFont, QColor, QPainter, QTextOption, QCursor


@dataclass(slots=True)
class OverlayConfig:

    font_size: int = 16
    font_family: str = "Arial"
    text_color: QColor = None
    background_color: QColor = None
    opacity: float = 0.9
    padding: int = 10

    def __post_init__(self) -> None:
        """Set default colors if not provided."""
        if self.text_color is None:
            self.text_color = QColor(255, 255, 255)  # White
        if self.background_color is None:
            self.background_color = QColor(0, 0, 0)  # Black


class SubtitleOverlay(QWidget):

    def __init__(self, config: Optional[OverlayConfig] = None) -> None:
        """Initialize the subtitle overlay.

        Args:
            config: OverlayConfig object or None for defaults
        """
        super().__init__()
        self.config = config or OverlayConfig()
        self.current_text = ""
        self.target_window_rect: Optional[QRect] = None
        self.drag_position: Optional[QPoint] = None
        self.resize_edge = None
        self.resize_start_pos = None
        self.resize_start_geom = None
        self.resize_margin = 10

        self._setup_ui()
        self._load_preferences()

    def _setup_ui(self) -> None:
        """Set up the overlay window properties."""
        # Make window frameless and transparent
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        # Set transparency
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowOpacity(self.config.opacity)

        # Default size and position (will be overridden by preferences)
        self.setGeometry(100, 100, 800, 150)
        
        # Enable mouse tracking for resize cursor
        self.setMouseTracking(True)
    
    def _get_prefs_file(self) -> Path:
        """Get the preferences file path."""
        prefs_dir = Path.home() / ".copilot" / "session-state" / "a2bae4eb-3fa2-4d33-996f-b346dbf30316" / "files"
        prefs_dir.mkdir(parents=True, exist_ok=True)
        return prefs_dir / "overlay_prefs.json"
    
    def _load_preferences(self) -> None:
        """Load overlay position and size from preferences."""
        prefs_file = self._get_prefs_file()
        if prefs_file.exists():
            try:
                with open(prefs_file, 'r') as f:
                    prefs = json.load(f)
                    self.setGeometry(
                        prefs.get('x', 100),
                        prefs.get('y', 100),
                        prefs.get('width', 800),
                        prefs.get('height', 150)
                    )
            except Exception:
                pass
    
    def _save_preferences(self) -> None:
        """Save overlay position and size to preferences."""
        try:
            prefs_file = self._get_prefs_file()
            geom = self.geometry()
            prefs = {
                'x': geom.x(),
                'y': geom.y(),
                'width': geom.width(),
                'height': geom.height()
            }
            with open(prefs_file, 'w') as f:
                json.dump(prefs, f)
        except Exception:
            pass

    def set_text(self, text: str) -> None:
        """Update the subtitle text.

        Args:
            text: Text to display
        """
        self.current_text = text
        self.update()

    def set_target_window_rect(self, rect: QRect) -> None:
        """Set the target window rectangle for reference (not used for positioning).

        Args:
            rect: QRect of the target window
        """
        self.target_window_rect = rect
        # Don't auto-position anymore - let user position it manually

    def _get_resize_edge(self, pos: QPoint) -> Optional[str]:
        """Determine which edge is near the cursor for resizing.
        
        Args:
            pos: Mouse position
            
        Returns:
            String indicating edge(s): 'left', 'right', 'top', 'bottom', or combinations
        """
        rect = self.rect()
        margin = self.resize_margin
        
        edges = []
        if pos.x() < margin:
            edges.append('left')
        elif pos.x() > rect.width() - margin:
            edges.append('right')
            
        if pos.y() < margin:
            edges.append('top')
        elif pos.y() > rect.height() - margin:
            edges.append('bottom')
        
        return ''.join(edges) if edges else None

    def _update_cursor(self, edge: Optional[str]) -> None:
        """Update cursor based on resize edge.
        
        Args:
            edge: Resize edge string
        """
        if edge is None:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif edge in ('top', 'bottom'):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif edge in ('left', 'right'):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edge in ('topleft', 'bottomright'):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif edge in ('topright', 'bottomleft'):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)

    def paintEvent(self, event) -> None:
        """Paint the overlay with text and background.

        Args:
            event: Paint event
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Draw semi-transparent background
        painter.fillRect(self.rect(), self.config.background_color)

        # Draw text
        font = QFont(self.config.font_family, self.config.font_size)
        painter.setFont(font)
        painter.setPen(self.config.text_color)

        text_rect = self.rect().adjusted(
            self.config.padding,
            self.config.padding,
            -self.config.padding,
            -self.config.padding,
        )

        text_option = QTextOption()
        text_option.setWrapMode(QTextOption.WrapMode.WordWrap)
        text_option.setAlignment(Qt.AlignmentFlag.AlignCenter)

        painter.drawText(text_rect, self.current_text, text_option)
        painter.end()

    def mousePressEvent(self, event) -> None:
        """Allow dragging and resizing the overlay.

        Args:
            event: Mouse event
        """
        edge = self._get_resize_edge(event.pos())
        if edge:
            self.resize_edge = edge
            self.resize_start_pos = event.globalPosition().toPoint()
            self.resize_start_geom = self.geometry()
        else:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        """Handle dragging and resizing the overlay.

        Args:
            event: Mouse event
        """
        if self.resize_edge:
            # Handle resizing
            delta = event.globalPosition().toPoint() - self.resize_start_pos
            geom = QRect(self.resize_start_geom)
            
            if 'left' in self.resize_edge:
                new_width = geom.width() - delta.x()
                if new_width >= 200:  # Minimum width
                    geom.setLeft(self.resize_start_geom.left() + delta.x())
            if 'right' in self.resize_edge:
                new_width = geom.width() + delta.x()
                if new_width >= 200:
                    geom.setWidth(new_width)
            if 'top' in self.resize_edge:
                new_height = geom.height() - delta.y()
                if new_height >= 50:  # Minimum height
                    geom.setTop(self.resize_start_geom.top() + delta.y())
            if 'bottom' in self.resize_edge:
                new_height = geom.height() + delta.y()
                if new_height >= 50:
                    geom.setHeight(new_height)
            
            self.setGeometry(geom)
        elif hasattr(self, 'drag_position') and self.drag_position:
            # Handle dragging
            self.move(event.globalPosition().toPoint() - self.drag_position)
        else:
            # Update cursor when hovering
            edge = self._get_resize_edge(event.pos())
            self._update_cursor(edge)
        
        event.accept()
    
    def mouseReleaseEvent(self, event) -> None:
        """Handle mouse release - save preferences after resize/move.
        
        Args:
            event: Mouse event
        """
        if self.resize_edge or self.drag_position:
            self._save_preferences()
        
        self.resize_edge = None
        self.resize_start_pos = None
        self.resize_start_geom = None
        self.drag_position = None
        event.accept()

    def show_and_focus(self) -> None:
        """Show the overlay and set it as always-on-top."""
        self.show()
        self.raise_()
        self.activateWindow()


class OverlayManager:
    """Manages the subtitle overlay window lifecycle."""

    def __init__(self, config: Optional[OverlayConfig] = None) -> None:
        """Initialize the overlay manager.

        Args:
            config: OverlayConfig object or None for defaults
        """
        self.app: Optional[QApplication] = None
        self.overlay: Optional[SubtitleOverlay] = None
        self.config = config or OverlayConfig()
        self.update_timer: Optional[QTimer] = None

    def initialize(self) -> None:
        """Initialize the QApplication and overlay widget."""
        if self.app is None:
            # Create or get existing QApplication
            app = QApplication.instance()
            if app is None:
                self.app = QApplication(sys.argv)
            else:
                self.app = app

        if self.overlay is None:
            self.overlay = SubtitleOverlay(self.config)
            self.overlay.show_and_focus()

    def set_subtitle(self, text: str) -> None:
        """Update the subtitle text.

        Args:
            text: Text to display
        """
        if self.overlay is None:
            self.initialize()

        if self.overlay:
            self.overlay.set_text(text)

    def set_target_window(self, rect: QRect) -> None:
        """Set the target window position for overlay alignment.

        Args:
            rect: QRect of the target window
        """
        if self.overlay is None:
            self.initialize()

        if self.overlay:
            self.overlay.set_target_window_rect(rect)

    def show(self) -> None:
        """Show the overlay window."""
        if self.overlay is None:
            self.initialize()
        if self.overlay:
            self.overlay.show_and_focus()

    def hide(self) -> None:
        """Hide the overlay window."""
        if self.overlay:
            self.overlay.hide()

    def close(self) -> None:
        """Close the overlay window."""
        if self.overlay:
            self.overlay.close()
        if self.app:
            self.app.quit()

    def process_events(self) -> None:
        """Process pending events (call periodically in main loop)."""
        if self.app:
            self.app.processEvents()
