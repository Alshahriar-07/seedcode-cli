"""Application controller: trusted Windows app discovery, launch, verify.

The package turns "open Spotify" into a structured, verifiable workflow:

    find_app ("Spotify")   → AppInfo (or ApplicationNotFoundError)
    is_running             → reuse/focus instead of spawning duplicates
    launch                 → Start Menu shortcut / shell-resolved target,
                             then state-based wait for a real window
    verify                 → process + window evidence, not assumptions
    install (missing apps) → gated behind the INSTALL permission category
                             and always confirmed by the user

Discovery walks trusted Windows mechanisms only — Start Menu shortcuts,
App Paths, uninstall registrations, PATH — never blind ``subprocess`` calls
with guessed exe names. All OS access is injectable/lazy so the whole
package unit-tests without Windows.
"""

from __future__ import annotations

from .discovery import AppInfo, find_app, installed_apps
from .launcher import launch_app
from .verifier import app_running, wait_for_app_window

__all__ = [
    "AppInfo",
    "find_app",
    "installed_apps",
    "launch_app",
    "app_running",
    "wait_for_app_window",
]
