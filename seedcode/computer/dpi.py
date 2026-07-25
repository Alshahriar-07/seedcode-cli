"""DPI / display-scaling resilience for the Computer Engine.

The engine drives three subsystems that each report coordinates in their own
space unless told otherwise:

* **UI Automation** (``uiautomation``) — ``BoundingRectangle`` is in *physical*
  pixels once the process is per-monitor DPI aware, otherwise virtualized.
* **mss** (screenshots) — always captures *physical* pixels.
* **pyautogui** (mouse/keyboard) — operates in *physical* pixels only when the
  process is DPI aware; otherwise Windows silently rescales its clicks, so a
  point read from UIA/mss lands in the wrong place on any display scaled above
  100 %.

The fix is to declare the process **Per-Monitor-DPI-Aware v2** exactly once, as
early as possible. Then all three agree on one physical-pixel space and a
coordinate resolved from the accessibility tree or a screenshot can be clicked
verbatim — on 4K laptops at 150 %, mixed-DPI multi-monitor rigs, and 100 %
desktops alike.

Everything here is Windows-only and defensive: on any other platform, or if the
Win32 calls are unavailable, the functions degrade to safe no-ops so the rest of
Seed Code keeps working. Nothing in this module touches an AI provider.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache

# Windows DPI-awareness context handles (see SetProcessDpiAwarenessContext).
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = -3
# PROCESS_DPI_AWARENESS values (SetProcessDpiAwareness, Win 8.1+).
_PROCESS_PER_MONITOR_DPI_AWARE = 2
# The standard baseline DPI Windows scales everything relative to.
BASELINE_DPI = 96

_applied: str | None = None


@dataclass(slots=True)
class DpiInfo:
    """The DPI-awareness state the engine achieved, for diagnostics."""

    mode: str          # e.g. "per_monitor_v2", "per_monitor", "system", "none"
    aware: bool        # whether physical-pixel alignment can be trusted
    platform: str

    def describe(self) -> str:
        if not self.aware:
            return f"DPI awareness: {self.mode} (coordinates may be virtualized)"
        return f"DPI awareness: {self.mode} (physical-pixel aligned)"


def ensure_dpi_awareness() -> DpiInfo:
    """Declare this process per-monitor-DPI-aware v2 — idempotent, best-effort.

    Called once at engine start (and safe to call again). Tries the strongest
    awareness Windows offers, falling back through older APIs on downlevel
    systems. If awareness was already set by the host process (an embedding
    app, a manifest), Windows refuses the change and we simply report the
    current state rather than fighting it.
    """
    global _applied
    if _applied is not None:
        return _current_info(_applied)

    if sys.platform != "win32":
        _applied = "none"
        return _current_info("none")

    mode = _apply_windows_awareness()
    _applied = mode
    return _current_info(mode)


def _apply_windows_awareness() -> str:
    """Walk the DPI-awareness APIs newest-first; return the mode that stuck."""
    import ctypes

    # 1) Windows 10 1703+ : richest, allows Per-Monitor v2 (dialogs, non-client
    #    areas, and child windows all scale correctly).
    try:
        user32 = ctypes.windll.user32
        if user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        ):
            return "per_monitor_v2"
        if user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE)
        ):
            return "per_monitor"
    except (AttributeError, OSError):
        pass

    # 2) Windows 8.1+ : per-monitor awareness via shcore.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE)
        return "per_monitor"
    except (AttributeError, OSError):
        # E_ACCESSDENIED here means awareness was already set for the process;
        # treat that as success and read the real state below.
        pass

    # 3) Vista+ : system-DPI aware (single global scale factor).
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass

    return _detect_existing_mode()


def _detect_existing_mode() -> str:
    """Read the process's current awareness when we could not set it."""
    if sys.platform != "win32":
        return "none"
    import ctypes

    try:
        awareness = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(awareness))
        return {0: "none", 1: "system", 2: "per_monitor"}.get(
            awareness.value, "unknown"
        )
    except (AttributeError, OSError):
        return "unknown"


def _current_info(mode: str) -> DpiInfo:
    aware = mode in ("per_monitor_v2", "per_monitor", "system")
    return DpiInfo(mode=mode, aware=aware, platform=sys.platform)


@lru_cache(maxsize=16)
def scale_for_point(x: int, y: int) -> float:
    """The DPI scale factor (1.0 == 100 %) of the monitor containing a point.

    Used to translate physical-pixel geometry to/from a display's logical
    layout when a driver reports logical coordinates. Returns 1.0 whenever the
    real factor cannot be determined, so callers never divide by an unknown.
    """
    dpi = _dpi_for_point(x, y)
    return (dpi / BASELINE_DPI) if dpi else 1.0


def _dpi_for_point(x: int, y: int) -> int | None:
    if sys.platform != "win32":
        return None
    import ctypes

    try:
        # MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST=2)
        pt = _Point(int(x), int(y))
        monitor = ctypes.windll.user32.MonitorFromPoint(pt, 2)
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        # GetDpiForMonitor(hmon, MDT_EFFECTIVE_DPI=0, &x, &y)
        if ctypes.windll.shcore.GetDpiForMonitor(
            monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
        ) == 0:
            return int(dpi_x.value)
    except (AttributeError, OSError):
        return None
    return None


if sys.platform == "win32":
    import ctypes

    class _Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
else:  # pragma: no cover - non-Windows stub so imports never fail
    class _Point:  # type: ignore[no-redef]
        def __init__(self, x: int, y: int) -> None:
            self.x, self.y = x, y


def reset_for_tests() -> None:
    """Clear the cached applied-state (test hook only)."""
    global _applied
    _applied = None
    scale_for_point.cache_clear()
