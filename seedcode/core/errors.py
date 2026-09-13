"""Structured error taxonomy for the SeedCode operator stack.

Machine-readable errors are the contract between the deterministic layers
(screen, apps, web, memory) and the agent loop: every failure carries a
stable ``code``, a human-readable message, and whether a retry/recovery is
plausible. The planner reads ``code`` + ``recoverable``; the user reads
``message``. Nothing raises a bare string anywhere below the tool surface.

All operator errors derive from :class:`OperatorError` so callers can catch
the whole family; the historical leaf exceptions (``ComputerError``,
``BrowserError``, ``SkillError``...) keep their names via aliases where
existing code depends on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# NOTE: deliberately NOT ``slots=True``. A slotted dataclass replaces the
# class object, which breaks zero-arg ``super()`` inside ``__post_init__`` for
# Exception subclasses (``super(type, obj): obj must be an instance or subtype
# of type``) - every structured error would be unraisable.
@dataclass
class OperatorError(Exception):
    """Base class for every structured operator failure."""

    code: str = "OPERATOR_ERROR"
    message: str = ""
    recoverable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message:
            object.__setattr__(self, "message", self.code)
        # Explicit base call: cooperative super() is fragile across the
        # dataclass/Exception metaclass boundary; Exception.__init__ sets args.
        Exception.__init__(self, self.message)

    def __str__(self) -> str:  # the model-facing text
        return self.message

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form (planner / error handler / logs)."""
        return {
            "error_type": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
            **({"details": self.details} if self.details else {}),
        }


# --- element / screen -----------------------------------------------------------
class ElementNotFoundError(OperatorError):
    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(
            code="ELEMENT_NOT_FOUND", message=message or "Element not found on screen.",
            recoverable=True, **kw,
        )


class StaleElementError(OperatorError):
    """A cached element id no longer resolves — the UI changed under us."""

    def __init__(self, element_id: str = "", **kw: Any) -> None:
        super().__init__(
            code="STALE_ELEMENT",
            message=(
                f"Element '{element_id or 'reference'}' is stale (the UI changed). "
                "Re-run the screen query to get a fresh element id."
            ),
            recoverable=True,
            details={"element_id": element_id},
            **kw,
        )


class ScreenUnavailableError(OperatorError):
    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(
            code="SCREEN_UNAVAILABLE",
            message=message or "Screen state is unavailable on this machine.",
            recoverable=False, **kw,
        )


# --- windows / applications -------------------------------------------------------
class WindowNotFoundError(OperatorError):
    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(
            code="WINDOW_NOT_FOUND", message=message or "No matching window.",
            recoverable=True, **kw,
        )


class ApplicationNotFoundError(OperatorError):
    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(
            code="APPLICATION_NOT_FOUND",
            message=message or "Application not found on this machine.",
            recoverable=True, **kw,
        )


class ApplicationLaunchError(OperatorError):
    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(
            code="APPLICATION_LAUNCH_FAILED",
            message=message or "Application did not launch.",
            recoverable=True, **kw,
        )


# --- web ---------------------------------------------------------------------------
class NavigationError(OperatorError):
    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(
            code="NAVIGATION_FAILED", message=message or "Navigation failed.",
            recoverable=True, **kw,
        )


class ExtractionError(OperatorError):
    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(
            code="EXTRACTION_FAILED",
            message=message or "Page extraction failed.",
            recoverable=True, **kw,
        )


class WebPageNotConnectedError(OperatorError):
    """No DOM surface (no DevTools) for a requested web extraction."""

    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(
            code="WEB_PAGE_NOT_CONNECTED",
            message=message or (
                "No DevTools connection to the browser; DOM extraction is "
                "unavailable. Open the page via open_url first."
            ),
            recoverable=True, **kw,
        )


class DownloadBlockedError(OperatorError):
    """A download was refused by policy (type/size/host)."""

    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(
            code="DOWNLOAD_BLOCKED", message=message or "Download refused by policy.",
            recoverable=False, **kw,
        )


# --- verification / lifecycle / security ---------------------------------------------
class VerificationError(OperatorError):
    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(
            code="VERIFICATION_FAILED",
            message=message or "The action did not have the expected effect.",
            recoverable=True, **kw,
        )


class SecurityError(OperatorError):
    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(
            code="SECURITY_DENIED",
            message=message or "The action was denied by policy.",
            recoverable=False, **kw,
        )


# --- historical aliases (existing modules keep their exception names) ---------------
# These subclass the structured base so both worlds interoperate.
from ..computer.controller import ComputerError as _ComputerError  # noqa: E402
from ..computer.browser import BrowserError as _BrowserError  # noqa: E402


class ComputerError(OperatorError, _ComputerError):  # type: ignore[misc]
    """Structured-compatible alias of the controller error."""

    def __init__(self, message: str = "", **kw: Any) -> None:
        OperatorError.__init__(
            self, code="COMPUTER_ERROR", message=message, recoverable=True, **kw
        )
        Exception.__init__(self, self.message)


class BrowserError(OperatorError, _BrowserError):  # type: ignore[misc]
    """Structured-compatible alias of the browser driver error."""

    def __init__(self, message: str = "", **kw: Any) -> None:
        OperatorError.__init__(
            self, code="BROWSER_ERROR", message=message, recoverable=True, **kw
        )
        Exception.__init__(self, self.message)
