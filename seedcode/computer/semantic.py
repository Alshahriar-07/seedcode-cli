"""Semantic element actions: id in, verified action out.

The bridge between the Screen Intelligence Engine (:mod:`.screen_state`) and
the low-level drivers. The AI never supplies coordinates here; it supplies a
stable element id from a previous query, and this module:

1. resolves the id through the engine (cache → fresh validation),
2. re-locates the element if the UI changed (bounded stale-requery),
3. performs the action through the controller's guarded drivers,
4. returns a compact, model-readable result with post-action state.

Failures raise structured operator errors (ELEMENT_NOT_FOUND,
STALE_ELEMENT) that flow back to the planner like any other tool result.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import ElementNotFoundError, StaleElementError
from ..core.limits import MAX_STALE_REQUERY
from .screen_state import Element, ScreenEngine, get_screen_engine


class SemanticActions:
    """Element-id-addressed actions over the screen engine + controller."""

    def __init__(self, engine: ScreenEngine | None = None, controller: Any = None) -> None:
        self._engine = engine
        self._controller = controller

    # --- plumbing -------------------------------------------------------------
    @property
    def engine(self) -> ScreenEngine:
        if self._engine is None:
            self._engine = get_screen_engine()
        return self._engine

    @property
    def controller(self) -> Any:
        if self._controller is None:
            from .controller import ComputerController

            self._controller = ComputerController()
        return self._controller

    def _resolve(self, element_id: str) -> Element:
        """Resolve an id to a fresh element with bounded stale-requery.

        A stale id (UI changed between query and action) triggers up to
        ``MAX_STALE_REQUERY`` re-locate cycles by the element's identity
        (role + name + automation id) before giving up with STALE_ELEMENT.
        """
        try:
            return self.engine.get_element(element_id)
        except StaleElementError as exc:
            cached = self.engine._by_id.get((element_id or "").strip().lower())
            identity = getattr(cached, "name", "") or element_id
            for _ in range(MAX_STALE_REQUERY):
                try:
                    return self.engine.find_element(identity, fresh=True)
                except ElementNotFoundError:
                    continue
            raise exc

    # --- actions ---------------------------------------------------------------
    def click(self, element_id: str, *, button: str = "left", double: bool = False) -> str:
        el = self._resolve(element_id)
        self.controller.mouse_click(el.x, el.y, button=button, double=double)
        return f"clicked {el.describe()}"

    def type_into(self, element_id: str, text: str, *, secret: bool = False) -> str:
        el = self._resolve(element_id)
        self.controller.mouse_click(el.x, el.y)  # focus first
        self.controller.type_text(text)
        shown = "•" * len(text) if secret else text
        return f"typed '{shown}' into {el.describe()}"

    def focus(self, element_id: str) -> str:
        el = self._resolve(element_id)
        self.controller.mouse_click(el.x, el.y)
        return f"focused {el.describe()}"

    def press(self, element_id: str, keys: list[str]) -> str:
        """Focus the element, then send a (guarded) hotkey to it."""
        el = self._resolve(element_id)
        self.controller.mouse_click(el.x, el.y)
        self.controller.hotkey([str(k) for k in keys])
        return f"pressed {'+'.join(keys)} on {el.describe()}"

    def select(self, element_id: str) -> str:
        """Select a combobox/list option by clicking it (checkbox/radio included)."""
        return self.click(element_id)

    def check(self, element_id: str, *, checked: bool = True) -> str:
        el = self._resolve(element_id)
        if el.checked is checked:
            return f"{el.describe()} already {'checked' if checked else 'unchecked'}"
        self.controller.mouse_click(el.x, el.y)
        return f"{'checked' if checked else 'unchecked'} {el.describe()}"
