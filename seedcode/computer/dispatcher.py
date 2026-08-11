"""Skill dispatcher: the coordinator between AI decisions and deterministic work.

The dispatcher is the single choke point through which every AI-selected action
flows. It:

1. looks up the skill (or builds an ad-hoc action for a semantic UI verb),
2. executes it deterministically via the :class:`SkillContext`,
3. verifies the declared outcome with the :class:`VerificationEngine`,
4. on failure, runs the :class:`RecoveryEngine`'s local strategy ladder,
5. records every stage in an :class:`ExecutionLog`,
6. returns a :class:`DispatchResult` — verified success, or a failure carrying
   a replan hint the AI planner uses (and only then) to think again.

The AI never sees the inner steps or any coordinates. It selects a skill by
name or issues a semantic UI verb with element *descriptions*; the dispatcher
and the engines below it do the rest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .logbook import ExecutionLog
from .recovery import RecoveryEngine
from .skills import Outcome, Skill, SkillContext, SkillError, SkillRegistry
from .verifier import VerificationEngine


@dataclass(slots=True)
class DispatchResult:
    ok: bool
    detail: str
    log: ExecutionLog
    replan_hint: str | None = None

    def for_model(self) -> str:
        """Concise text the AI reads back — verified evidence, never raw steps."""
        head = self.detail
        if not self.ok and self.replan_hint:
            return f"{head}\nReplan hint: {self.replan_hint}"
        return head


# Semantic UI verbs the AI may issue directly (no named skill). Each maps to a
# deterministic body over the resolver + controller; every target is a
# *description*, resolved to coordinates internally.
class _SemanticActions:
    """Ad-hoc, description-driven UI actions built on the resolver."""

    @staticmethod
    def click(ctx: SkillContext, params: dict[str, Any], *, button="left", double=False) -> Outcome:
        target = str(params.get("target", "")).strip()
        if not target:
            raise SkillError("ui action requires a 'target' description")
        detail = ctx.click_described(target, button=button, double=double)
        return Outcome(detail, {"element": target})

    @staticmethod
    def type(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
        target = str(params.get("target", "")).strip()
        text = str(params.get("text", ""))
        secret = bool(params.get("secret", False))
        if not target:
            raise SkillError("ui_type requires a 'target' description")
        detail = ctx.type_into(target, text, secret=secret)
        return Outcome(detail)

    @staticmethod
    def wait_for(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
        target = str(params.get("target") or params.get("description", "")).strip()
        if not target:
            raise SkillError("ui_wait_for requires a 'target' description")
        return Outcome(f"waiting for {target}", {"element": target})

    @staticmethod
    def assert_(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
        target = str(params.get("target") or params.get("description", "")).strip()
        if not target:
            raise SkillError("ui_assert requires a 'target' description")
        return Outcome(f"asserting {target}", {"element": target})


_SEMANTIC = {
    "ui_click": lambda c, p: _SemanticActions.click(c, p),
    "ui_double_click": lambda c, p: _SemanticActions.click(c, p, double=True),
    "ui_right_click": lambda c, p: _SemanticActions.click(c, p, button="right"),
    "ui_type": lambda c, p: _SemanticActions.type(c, p),
    "ui_wait_for": lambda c, p: _SemanticActions.wait_for(c, p),
    "ui_assert": lambda c, p: _SemanticActions.assert_(c, p),
}

# Semantic verbs that actually manipulate the UI. Observation verbs
# (ui_wait_for, ui_assert) are harmless on a browser and stay allowed.
_MUTATING_SEMANTIC = ("ui_click", "ui_double_click", "ui_right_click", "ui_type")

# Window-title fragments that identify a browser. Titles end with the browser
# name on every mainstream platform ("YouTube — Google Chrome").
_BROWSER_MARKERS = (
    "google chrome", "chrome", "microsoft edge", "edge", "mozilla firefox",
    "firefox", "brave", "opera", "vivaldi", "chromium", "safari",
)


def _is_browser_window(title: str | None) -> bool:
    """Whether a window title belongs to a web browser."""
    low = (title or "").strip().lower()
    if not low:
        return False
    return any(marker in low for marker in _BROWSER_MARKERS)


def _browser_skill_names(registry: SkillRegistry) -> str:
    """The browser skills to point the planner at, from the live registry."""
    wanted = (
        "youtube_play", "youtube_search", "google_search", "web_search",
        "open_url", "new_tab", "close_tab", "switch_tab", "back", "forward",
        "refresh",
    )
    available = [name for name in wanted if registry.get(name) is not None]
    return ", ".join(available)


class SkillDispatcher:
    """Coordinates execute → verify → recover for one action at a time."""

    def __init__(
        self,
        controller: Any,
        resolver: Any,
        state: Any,
        permissions: Any,
        registry: SkillRegistry,
        verifier: VerificationEngine | None = None,
        recovery: RecoveryEngine | None = None,
    ) -> None:
        self._ctx = SkillContext(
            controller=controller, resolver=resolver, state=state, permissions=permissions
        )
        self._registry = registry
        self._verifier = verifier or VerificationEngine(
            vision=getattr(controller, "vision", None),
            windows=getattr(controller, "windows", None),
        )
        self._recovery = recovery or RecoveryEngine(controller=controller, state=state)

    def _refuse_browser_ui(self, verb: str, params: dict[str, Any]) -> str | None:
        """The refusal message for a ui_* verb on a browser, or None to allow.

        Deliberately fails *open*: if the foreground window cannot be read, the
        action proceeds. A guard that blocked on uncertainty would break native
        automation on any machine whose window driver is unavailable.
        """
        controller = getattr(self._ctx, "controller", None)
        windows = getattr(controller, "windows", None)
        if windows is None:
            return None
        try:
            active = windows.active_window()
        except Exception:
            return None
        title = getattr(active, "title", None)
        if not _is_browser_window(title):
            return None
        target = str(params.get("target", "")).strip()
        skills = _browser_skill_names(self._registry)
        return (
            f"{verb} is not available on browser windows"
            f'{f" (target: {target!r})" if target else ""}. '
            "Browser work is handled by complete skills that navigate, dismiss "
            "popups, select results, and verify the outcome in one step — "
            "clicking through a page yourself is slower and breaks whenever the "
            f"site changes. Use computer_run with one of: {skills}. "
            "For example, to play a song: "
            'computer_run(skill="youtube_play", params={"query": "<song name>"}).'
        )

    def dispatch(
        self, name: str, params: dict[str, Any] | None = None, expected: dict[str, Any] | None = None
    ) -> DispatchResult:
        """Run one skill or semantic UI verb end-to-end with verification.

        Every outcome — success, failure, and everything the engine did on the
        way — is appended to the structured execution log on disk.
        """
        result = self._dispatch(name, params, expected)
        result.log.persist(label=str(name).strip().lower())
        return result

    def _dispatch(
        self, name: str, params: dict[str, Any] | None = None, expected: dict[str, Any] | None = None
    ) -> DispatchResult:
        log = ExecutionLog()
        params = params or {}
        name = str(name).strip().lower()

        skill = self._registry.get(name)
        semantic = _SEMANTIC.get(name)
        if skill is None and semantic is None:
            log.done(f"unknown skill or action '{name}'", ok=False)
            return DispatchResult(
                False, f"No such skill '{name}'.", log,
                replan_hint="Choose a skill from the catalog or a ui_* action.",
            )

        label = skill.name if skill else name
        log.plan(f"selected {label} {params}")

        # --- browser guard ---------------------------------------------------
        # Browser workflows are owned end-to-end by the browser skills. Letting
        # the AI click its way around a web page is what this architecture
        # exists to prevent: it burns turns, breaks on every layout change, and
        # drags OCR into a problem a URL already solves. So a mutating ui_*
        # verb aimed at a browser window is refused with a pointer to the skill
        # that does the whole job.
        if name in _MUTATING_SEMANTIC:
            refusal = self._refuse_browser_ui(name, params)
            if refusal is not None:
                log.done(refusal, ok=False)
                return DispatchResult(False, refusal, log, replan_hint=refusal)

        # --- execute ---------------------------------------------------------
        def _run() -> Outcome:
            if skill is not None:
                return skill.run(self._ctx, params)
            return semantic(self._ctx, params)

        try:
            outcome = _run()
        except SkillError as exc:
            log.execute(str(exc), ok=False)
            log.done(f"{label} failed: {exc}", ok=False)
            return DispatchResult(False, str(exc), log, replan_hint=str(exc))
        except Exception as exc:  # permission denial, driver error, ...
            log.execute(f"{type(exc).__name__}: {exc}", ok=False)
            log.done(f"{label} failed: {exc}", ok=False)
            return DispatchResult(
                False, f"{label} could not run: {exc}", log,
                replan_hint=f"{type(exc).__name__}: {exc}",
            )
        log.execute(outcome.detail)

        # Caller-supplied expectation overrides the skill's own.
        want = expected or outcome.expected

        # --- verify ----------------------------------------------------------
        result = self._verifier.verify(want)
        if result.ok:
            log.verify(result.detail)
            log.done(outcome.detail)
            return DispatchResult(True, outcome.detail, log)
        log.verify(result.detail, ok=False)

        # --- recover ---------------------------------------------------------
        recovery = self._recovery.recover(
            action=lambda: _run().detail,
            verify=lambda: self._verifier.verify(want),
            window_title=outcome.window_title,
            app_target=outcome.app_target,
        )
        if recovery.recovered:
            log.recover(f"{recovery.detail} (tried: {', '.join(recovery.strategies_tried)})")
            log.done(outcome.detail)
            return DispatchResult(True, outcome.detail, log)

        log.recover(recovery.detail, ok=False)
        hint = (
            f"'{label}' executed but verification failed ({result.detail}); local "
            f"recovery ({', '.join(recovery.strategies_tried) or 'none'}) did not help."
        )
        log.done(hint, ok=False)
        return DispatchResult(False, hint, log, replan_hint=hint)
