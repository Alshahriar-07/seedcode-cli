"""Popup Manager: make browser interruptions invisible to the planner.

Every real browsing session is punctuated by modals the user never asked for —
a cookie/consent wall, "Translate this page?", "Show notifications?", a sign-in
interstitial, a camera/location permission prompt. Historically the AI saw
these, reasoned about them, and burned turns emitting clicks to clear them.
That is the wrong boundary: dismissing chrome is mechanical work with a known
answer, so it belongs to the engine.

:class:`PopupManager` sweeps for them automatically before and after every
browser action. The AI is never told a popup existed; skills simply behave as
though the page were clean.

Two detection surfaces, in ladder order:

1. **DOM** (:mod:`.browser_cdp`) — in-page banners: consent walls, sign-in
   interstitials, newsletter modals. Matched by CSS selector for the big known
   offenders, then by button text ("Accept all", "No thanks") for the long
   tail. Clicks happen *in the page*, so they cannot miss.
2. **Accessibility tree** — browser-*chrome* bubbles that live outside the
   document and therefore have no DOM at all: Chrome's translate bar, the
   notification/location permission bubble, the password-save prompt.

Both degrade to no-ops when unavailable, and the sweep never raises: a popup
sweep that fails must not fail the workflow that called it.

Deterministic, offline, no AI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# --- what counts as a popup --------------------------------------------------


@dataclass(frozen=True, slots=True)
class PopupRule:
    """One recognisable popup and how to make it go away."""

    kind: str            # translate | cookies | notifications | signin | permissions
    label: str           # human description, for the execution log
    # CSS selectors whose presence means this popup is on screen. The first
    # match also acts as the click target when ``accept_text`` finds nothing.
    selectors: tuple[str, ...] = ()
    # Button captions to click, in preference order. Matched case-insensitively
    # against visible text / aria-label / title.
    accept_text: tuple[str, ...] = ()
    # Accessibility-tree element names for browser-chrome popups with no DOM.
    chrome_names: tuple[str, ...] = ()


# Ordered most-blocking first: a consent wall blocks the whole page, while a
# translate bar merely shifts it. Selector lists cover the dominant consent
# platforms (OneTrust, Quantcast, Cookiebot, Didomi, Osano, TrustArc, Usercentrics)
# plus Google/YouTube's own consent frame, which together account for the
# overwhelming majority of real-world banners.
RULES: tuple[PopupRule, ...] = (
    PopupRule(
        kind="cookies",
        label="cookie / consent banner",
        selectors=(
            "#onetrust-banner-sdk", "#onetrust-consent-sdk",
            ".qc-cmp2-container", "#qc-cmp2-ui",
            "#CybotCookiebotDialog",
            "#didomi-popup", ".didomi-popup-container",
            ".osano-cm-dialog", "#truste-consent-track",
            "#usercentrics-root", "#cmpbox",
            "[aria-label='Cookie banner']", "[id*='cookie-banner']",
            "[class*='cookie-consent']", "[class*='cookie-banner']",
            "c-wiz[jsrenderer] form[action*='consent']",
        ),
        accept_text=(
            # Consent captions only. "Allow all" and friends are deliberately
            # absent: they read as granting a permission, so _forbidden blocks
            # them anyway — a consent wall is cleared by accepting *cookies*,
            # never by clicking a bare "Allow".
            "accept all cookies", "accept all", "accept cookies",
            "i agree", "agree to all", "got it", "understood",
            "reject all", "essential only", "only necessary",
            "accept", "agree", "ok",
        ),
    ),
    PopupRule(
        kind="signin",
        label="sign-in / account interstitial",
        selectors=(
            "#credential_picker_container", "iframe[src*='accounts.google.com/gsi']",
            "[aria-label='Sign in to YouTube']",
            "tp-yt-paper-dialog:has(ytd-consent-bump-v2-lightbox)",
            "[data-testid='sheetDialog']",
            "[class*='login-modal']", "[class*='signup-modal']", "[id*='login-overlay']",
        ),
        # Never click "Sign in" — dismissing must not start an auth flow.
        accept_text=(
            "not now", "no thanks", "stay signed out", "maybe later",
            "continue without signing in", "skip for now", "dismiss", "close",
        ),
        chrome_names=("Save password", "Never", "Not now"),
    ),
    PopupRule(
        kind="translate",
        label="translate bar",
        # Chrome's translate UI is browser chrome, not page DOM, so it is
        # detected through the accessibility tree only.
        chrome_names=(
            "Translate this page?", "Translate page?", "Nope", "No thanks",
            "Never translate", "Close", "Translate",
        ),
    ),
    PopupRule(
        kind="notifications",
        label="notification permission prompt",
        selectors=("[class*='push-notification']", "[id*='notification-prompt']"),
        accept_text=("block", "no thanks", "not now", "don't allow", "later"),
        chrome_names=(
            "Show notifications", "Block", "Don't allow", "Never allow",
        ),
    ),
    PopupRule(
        kind="permissions",
        label="site permission prompt (camera / mic / location)",
        chrome_names=(
            "Use your microphone", "Use your camera", "Know your location",
            "Block", "Don't allow", "Never allow",
        ),
    ),
)

# Captions that must never be clicked while dismissing: clearing a popup must
# never grant a permission, start a sign-in, or accept a purchase on the user's
# behalf. Matched against the caption as a whole or as a *word sequence*, never
# as a bare substring — "allow" appearing inside "Don't allow" (the correct way
# to decline a notification prompt) must not block it.
_NEVER_CLICK = (
    "sign in", "log in", "login", "sign up", "subscribe", "buy", "purchase",
    "allow", "allow all", "enable notifications", "turn on", "yes, i'm in",
    "create account",
)

# Words that flip a caption from affirmative to declining. A caption carrying
# one of these is a dismissal even when it contains a forbidden word.
_NEGATORS = (
    "no", "not", "never", "don't", "dont", "do not", "block", "deny",
    "reject", "decline", "without", "later", "skip", "close", "dismiss",
    "nope", "stay", "essential only", "necessary only",
)


@dataclass(slots=True)
class SweepResult:
    """What one sweep dismissed."""

    dismissed: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.dismissed)

    def describe(self) -> str:
        if not self.dismissed:
            return "no popups present"
        return "dismissed " + ", ".join(self.dismissed)


class PopupManager:
    """Detects and clears browser popups without involving the AI.

    ``cdp`` and ``controller`` are injectable so the logic is unit-testable
    with no live browser.
    """

    def __init__(self, cdp: Any = None, controller: Any = None, rules: Any = None) -> None:
        if cdp is None:
            from . import browser_cdp as cdp  # type: ignore
        self._cdp = cdp
        self._controller = controller
        self._rules = tuple(rules) if rules is not None else RULES

    # --- public API ----------------------------------------------------------
    def sweep(self, max_passes: int = 2) -> SweepResult:
        """Dismiss every popup currently on screen.

        Runs more than one pass because popups stack: dismissing a consent wall
        frequently reveals the sign-in prompt that was behind it. Stops early
        once a pass finds nothing, so the common clean-page case costs one
        cheap DOM query.
        """
        result = SweepResult()
        for _ in range(max(1, max_passes)):
            found_this_pass = False
            for rule in self._rules:
                if self._dismiss(rule):
                    result.dismissed.append(rule.kind)
                    found_this_pass = True
            if not found_this_pass:
                break
        return result

    def present(self) -> list[str]:
        """Which popup kinds are detectable right now (diagnostic)."""
        return [rule.kind for rule in self._rules if self._detect(rule)]

    # --- detection -----------------------------------------------------------
    def _detect(self, rule: PopupRule) -> bool:
        """Whether this popup appears to be on screen (DOM, then chrome)."""
        for selector in rule.selectors:
            if self._safe(lambda s=selector: self._cdp.exists(s)) is True:
                return True
        return self._chrome_element(rule) is not None

    def _chrome_element(self, rule: PopupRule):
        """Find a browser-chrome popup in the accessibility tree."""
        if not rule.chrome_names or self._controller is None:
            return None
        vision = getattr(self._controller, "vision", None)
        if vision is None:
            return None
        elements = self._safe(lambda: vision.snapshot(None))
        if not elements:
            return None
        try:
            _title, items = elements
        except (TypeError, ValueError):
            return None
        for wanted in rule.chrome_names:
            needle = wanted.lower()
            for el in items or []:
                name = (getattr(el, "name", "") or "").strip().lower()
                if name and needle in name:
                    return el
        return None

    # --- dismissal -----------------------------------------------------------
    def _dismiss(self, rule: PopupRule) -> bool:
        """Try to clear one popup. Returns whether anything was dismissed."""
        if not self._detect(rule):
            return False
        # 1) In-page button by caption — the precise, reliable path.
        for caption in rule.accept_text:
            if _forbidden(caption):
                continue
            if self._safe(lambda c=caption: self._cdp.click_text(c)) is True:
                return True
        # 2) The banner's own close control, when captions did not match.
        for selector in rule.selectors:
            close = f"{selector} [aria-label*='close' i], {selector} button.close"
            if self._safe(lambda s=close: self._cdp.click_selector(s)) is True:
                return True
        # 3) Browser-chrome bubble: click it through the accessibility tree.
        if self._click_chrome(rule):
            return True
        # 4) Last resort: Esc closes most transient bubbles and never submits.
        return self._press_escape(rule)

    def _click_chrome(self, rule: PopupRule) -> bool:
        """Click a chrome popup's dismiss control via the accessibility tree."""
        if self._controller is None:
            return False
        # Only ever click a *dismissing* caption, never the affirmative one.
        for wanted in rule.chrome_names:
            if not _is_dismissive(wanted) or _forbidden(wanted):
                continue
            element = self._chrome_element(
                PopupRule(kind=rule.kind, label=rule.label, chrome_names=(wanted,))
            )
            if element is None:
                continue
            clicked = self._safe(
                lambda el=element: self._controller.mouse_click(el.x, el.y)
            )
            if clicked is not None:
                return True
        return False

    def _press_escape(self, rule: PopupRule) -> bool:
        """Send Esc, then confirm the popup actually went away.

        Esc is only reported as a dismissal when detection stops firing —
        otherwise a stubborn banner would be logged as cleared while still
        blocking the page.
        """
        if self._controller is None:
            return False
        if self._safe(lambda: self._controller.hotkey(["esc"])) is None:
            return False
        return not self._detect(rule)

    # --- plumbing ------------------------------------------------------------
    @staticmethod
    def _safe(call):
        """Run a driver call, converting any failure into ``None``.

        A popup sweep is opportunistic housekeeping; if the browser is gone or
        DevTools is unreachable, the workflow continues unaffected.
        """
        try:
            return call()
        except Exception:
            return None


def _forbidden(caption: str) -> bool:
    """Whether clicking this caption could act on the user's behalf.

    A negated caption ("Don't allow", "Reject all") is always safe: it is the
    *decline* control, and it frequently contains the very word that makes the
    affirmative version dangerous. Checking negation first is what keeps
    "Don't allow" clickable while "Allow" stays blocked.
    """
    low = " ".join((caption or "").strip().lower().split())
    if not low:
        return True
    if _is_dismissive(low):
        return False
    # Whole-caption match, or the forbidden phrase appearing as whole words.
    return any(bad == low or _has_phrase(low, bad) for bad in _NEVER_CLICK)


def _has_phrase(caption: str, phrase: str) -> bool:
    """Whether ``phrase`` occurs in ``caption`` on word boundaries."""
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", caption) is not None


def _is_dismissive(caption: str) -> bool:
    """Whether a caption declines rather than accepts."""
    low = " ".join((caption or "").strip().lower().split())
    return any(_has_phrase(low, word) for word in _NEGATORS)
