from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class WindowInfo:

    title: str
    x: int
    y: int
    width: int
    height: int

    def __str__(self) -> str:
        return f"{self.title} (x={self.x}, y={self.y}, w={self.width}, h={self.height})"


class WindowTracker:

    def __init__(self) -> None:
        """Initialize window tracker with platform-specific implementation."""
        self._backend = self._init_backend()

    @staticmethod
    def _init_backend() -> WindowTrackerBackend:
        """Initialize platform-specific window tracker."""
        if sys.platform == "win32":
            return WindowsWindowTracker()
        elif sys.platform == "darwin":
            return MacWindowTracker()
        else:
            return LinuxWindowTracker()

    def get_active_window(self) -> Optional[WindowInfo]:
        """Get information about the currently active window.

        Returns:
            WindowInfo object or None if unable to get active window
        """
        return self._backend.get_active_window()

    def get_available_windows(self) -> list[WindowInfo]:
        """Get list of all available windows.

        Returns:
            List of WindowInfo objects
        """
        return self._backend.get_available_windows()

    def focus_window(self, window_title: str) -> bool:
        """Bring a window to focus by title.

        Args:
            window_title: Title of window to focus

        Returns:
            True if successful, False otherwise
        """
        return self._backend.focus_window(window_title)


class WindowTrackerBackend:

    def get_active_window(self) -> Optional[WindowInfo]:
        """Get the active window."""
        raise NotImplementedError

    def get_available_windows(self) -> list[WindowInfo]:
        """Get all available windows."""
        raise NotImplementedError

    def focus_window(self, window_title: str) -> bool:
        """Focus a window by title."""
        raise NotImplementedError


class WindowsWindowTracker(WindowTrackerBackend):
    """Windows-specific window tracking using win32gui."""

    def __init__(self) -> None:
        """Initialize Windows window tracker."""
        try:
            import win32gui
            import win32con
            self.win32gui = win32gui
            self.win32con = win32con
        except ImportError:
            raise RuntimeError(
                "pywin32 is required for Windows window tracking. "
                "Install with: pip install pywin32"
            )

    def get_active_window(self) -> Optional[WindowInfo]:
        """Get the currently active window on Windows."""
        try:
            hwnd = self.win32gui.GetForegroundWindow()
            if hwnd == 0:
                return None

            title = self.win32gui.GetWindowText(hwnd)
            rect = self.win32gui.GetWindowRect(hwnd)
            left, top, right, bottom = rect

            return WindowInfo(
                title=title,
                x=left,
                y=top,
                width=right - left,
                height=bottom - top,
            )
        except Exception as e:
            print(f"Error getting active window: {e}")
            return None

    def get_available_windows(self) -> list[WindowInfo]:
        """Get all visible windows on Windows."""
        windows: list[WindowInfo] = []

        def _enum_windows(hwnd, _lParam) -> bool:
            if not self.win32gui.IsWindowVisible(hwnd):
                return True

            title = self.win32gui.GetWindowText(hwnd)
            if not title:
                return True

            try:
                rect = self.win32gui.GetWindowRect(hwnd)
                left, top, right, bottom = rect

                windows.append(
                    WindowInfo(
                        title=title,
                        x=left,
                        y=top,
                        width=right - left,
                        height=bottom - top,
                    )
                )
            except Exception:
                pass

            return True

        self.win32gui.EnumWindows(_enum_windows, None)
        return windows

    def focus_window(self, window_title: str) -> bool:
        """Focus a window by title on Windows."""
        try:
            hwnd = self.win32gui.FindWindow(None, window_title)
            if hwnd == 0:
                return False
            self.win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False


class MacWindowTracker(WindowTrackerBackend):
    """macOS-specific window tracking using Quartz."""

    def __init__(self) -> None:
        """Initialize macOS window tracker."""
        try:
            from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly
            from Quartz import kCGWindowName, kCGWindowBounds
            self.CGWindowListCopyWindowInfo = CGWindowListCopyWindowInfo
            self.kCGWindowListOptionOnScreenOnly = kCGWindowListOptionOnScreenOnly
            self.kCGWindowName = kCGWindowName
            self.kCGWindowBounds = kCGWindowBounds
        except ImportError:
            raise RuntimeError(
                "pyobjc-framework-Quartz is required for macOS window tracking. "
                "Install with: pip install pyobjc-framework-Quartz"
            )

    def get_active_window(self) -> Optional[WindowInfo]:
        """Get the currently active window on macOS."""
        try:
            windows = self.CGWindowListCopyWindowInfo(self.kCGWindowListOptionOnScreenOnly, 0)
            if not windows:
                return None

            # The first window in the list is typically the active one
            window = windows[0]
            title = window.get(self.kCGWindowName, "Unknown")
            bounds = window.get(self.kCGWindowBounds, {})

            x = bounds.get("X", 0)
            y = bounds.get("Y", 0)
            width = bounds.get("Width", 0)
            height = bounds.get("Height", 0)

            return WindowInfo(title=title, x=int(x), y=int(y), width=int(width), height=int(height))
        except Exception as e:
            print(f"Error getting active window: {e}")
            return None

    def get_available_windows(self) -> list[WindowInfo]:
        """Get all visible windows on macOS."""
        windows_list: list[WindowInfo] = []

        try:
            windows = self.CGWindowListCopyWindowInfo(self.kCGWindowListOptionOnScreenOnly, 0)
            for window in windows:
                title = window.get(self.kCGWindowName, "Unknown")
                bounds = window.get(self.kCGWindowBounds, {})

                x = bounds.get("X", 0)
                y = bounds.get("Y", 0)
                width = bounds.get("Width", 0)
                height = bounds.get("Height", 0)

                windows_list.append(
                    WindowInfo(title=title, x=int(x), y=int(y), width=int(width), height=int(height))
                )
        except Exception as e:
            print(f"Error getting available windows: {e}")

        return windows_list

    def focus_window(self, window_title: str) -> bool:
        """Focus a window by title on macOS."""
        try:
            import subprocess
            script = f'tell app "System Events" to set frontmost of (processes whose name contains "{window_title}") to true'
            subprocess.run(["osascript", "-e", script], check=True)
            return True
        except Exception:
            return False


class LinuxWindowTracker(WindowTrackerBackend):
    """Linux-specific window tracking using X11/Xlib."""

    def __init__(self) -> None:
        """Initialize Linux window tracker."""
        try:
            from Xlib import display
            self.display = display.Display()
        except ImportError:
            raise RuntimeError(
                "python-xlib is required for Linux window tracking. "
                "Install with: pip install python-xlib"
            )

    def get_active_window(self) -> Optional[WindowInfo]:
        """Get the currently active window on Linux."""
        try:
            root = self.display.screen().root
            # Get active window from _NET_ACTIVE_WINDOW property
            net_active_window = root.get_full_property(
                self.display.intern_atom("_NET_ACTIVE_WINDOW"), 0
            )
            if not net_active_window:
                return None

            window_id = net_active_window.value[0]
            window = self.display.create_resource_object("window", window_id)

            title = window.get_full_property(self.display.intern_atom("_NET_WM_NAME"), 0)
            title_str = title.value.decode("utf-8") if title else "Unknown"

            geom = window.get_geometry()
            return WindowInfo(
                title=title_str,
                x=geom.x,
                y=geom.y,
                width=geom.width,
                height=geom.height,
            )
        except Exception as e:
            print(f"Error getting active window: {e}")
            return None

    def get_available_windows(self) -> list[WindowInfo]:
        """Get all visible windows on Linux."""
        windows_list: list[WindowInfo] = []

        try:
            root = self.display.screen().root
            net_client_list = root.get_full_property(
                self.display.intern_atom("_NET_CLIENT_LIST"), 0
            )
            if not net_client_list:
                return windows_list

            for window_id in net_client_list.value:
                try:
                    window = self.display.create_resource_object("window", window_id)
                    title_prop = window.get_full_property(self.display.intern_atom("_NET_WM_NAME"), 0)
                    title = title_prop.value.decode("utf-8") if title_prop else "Unknown"

                    geom = window.get_geometry()
                    windows_list.append(
                        WindowInfo(title=title, x=geom.x, y=geom.y, width=geom.width, height=geom.height)
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"Error getting available windows: {e}")

        return windows_list

    def focus_window(self, window_title: str) -> bool:
        """Focus a window by title on Linux."""
        try:
            import subprocess
            subprocess.run(["wmctrl", "-a", window_title], check=True)
            return True
        except Exception:
            return False
