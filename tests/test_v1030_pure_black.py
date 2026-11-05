"""Pure-black canvas contract (v10.3.1).

The whole terminal viewport is painted by the root prompt_toolkit style; the
composer, message area, overlays and every palette inherit it. No component
may reintroduce a gray band of its own.
"""

from __future__ import annotations

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from seedcode.ui.state import AppState
from seedcode.ui.theme import CANVAS, PALETTES, pt_style, rich_theme
from seedcode.ui.tui import ChatTUI


def _bg(style, class_name: str) -> str:
    return str(style.get_attrs_for_style_str(class_name).bgcolor or "")


def test_default_canvas_and_surfaces_are_pure_black() -> None:
    assert CANVAS == "#000000"
    style = pt_style("seed")
    # "" paints every unpainted cell, canvas paints layout gaps, composer is
    # no longer a lighter card.
    for class_name in ("", "class:canvas", "class:composer"):
        assert _bg(style, class_name) == "000000", class_name
    assert "000000" in str(rich_theme("seed").styles[""])


def test_every_palette_shares_the_same_canvas() -> None:
    for name in PALETTES:
        style = pt_style(name)
        for class_name in ("", "class:canvas", "class:composer"):
            assert _bg(style, class_name) == "000000", f"{name}:{class_name}"


def test_root_layout_is_full_screen_on_the_canvas() -> None:
    tui = ChatTUI(AppState(), width=80, height=24)
    # Git Bash has no Windows console buffer; drive the app off a pipe.
    with create_pipe_input() as pipe:
        tui._input_device = pipe
        tui._output_device = DummyOutput()
        app = tui._build_app()
    assert app.full_screen is True
    assert "class:canvas" in str(app.layout.container.style)
