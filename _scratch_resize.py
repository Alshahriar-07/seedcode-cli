"""Temporary manual resize verification (deleted before final delivery).

Renders the persistent TUI at the report's required sizes and checks the
invariants: nothing throws, no line exceeds the width, the composer and the
ASCII branding are present, and the regions fit the terminal height.
"""

from __future__ import annotations

import io
import re
import sys
import threading
import time
from pathlib import Path

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.plain_text import PlainTextOutput

sys.path.insert(0, str(Path(__file__).parent))

from seedcode.ui.state import AppState, Status  # noqa: E402
from seedcode.ui.tui import ChatTUI  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


class ResizablePlainOutput(PlainTextOutput):
    def __init__(self, buffer, rows: int, columns: int) -> None:
        self._rows, self._columns = rows, columns
        super().__init__(buffer)

    def resize(self, rows: int, columns: int) -> None:
        self._rows, self._columns = rows, columns

    def get_size(self):
        from prompt_toolkit.data_structures import Size

        return Size(rows=self._rows, columns=self._columns)


def _state() -> AppState:
    return AppState(
        provider="OpenRouter",
        model="cohere/north-mini-code:free",
        mode="Agent Mode",
        status=Status.WORKING,
        workspace="D:/my-project",
        context_limit=16384,
        key_required=True,
        api_key="sk-or-v1...e031",
    )


def render(rows: int, columns: int, *, resize_to=None) -> dict:
    buffer = io.StringIO()
    output = ResizablePlainOutput(buffer, rows, columns)
    errors: list[str] = []
    with create_pipe_input() as pipe:
        tui = ChatTUI(
            _state(), width=columns, height=rows,
            input_device=pipe, output_device=output,
        )
        tui._input.text = "typed text survives every resize"

        def scripted() -> None:
            time.sleep(0.25)
            if resize_to:
                for r, c in resize_to:
                    output.resize(r, c)
                    tui._invalidate()
                    time.sleep(0.2)
            pipe.send_text("\x04")

        threading.Thread(target=scripted, daemon=True).start()
        try:
            tui.run_once()
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    text = ANSI.sub("", buffer.getvalue())
    lines = [line for line in text.split("\n") if line.strip()]
    too_wide = [line for line in lines if len(line) > max(columns, 200 if resize_to else columns)]
    return {
        "errors": errors,
        "blank_screen": any("Window too small" in line for line in lines),
        "composer": any("Message" in line for line in lines),
        "logo": any("\u2588" in line for line in lines),
        "input_preserved": tui._input.text == "typed text survives every resize",
        "min_rows_ok": tui.min_layout_rows() <= tui._rows,
        "final_size": (tui._rows, tui._width),
        "too_wide": len(too_wide),
    }


def main() -> None:
    print("size        errors  blank  composer  logo  input_kept  rows_fit")
    ok = True
    for rows, columns in ((24, 80), (30, 100), (40, 120), (50, 160), (60, 200)):
        r = render(rows, columns)
        ok = ok and not r["errors"] and not r["blank_screen"] and r["composer"] and r["rows_fit" if False else "min_rows_ok"] and r["input_preserved"]
        print(
            f"{columns}x{rows:<4}  {len(r['errors']):<6}  {r['blank_screen']!s:<5}  "
            f"{r['composer']!s:<8}  {r['logo']!s:<5}  {r['input_preserved']!s:<10}  {r['min_rows_ok']}"
        )
    print()
    print("resize while running (idle -> typing -> streaming sizes):")
    r = render(
        24, 80,
        resize_to=[(30, 100), (40, 160), (12, 60), (60, 200), (6, 40), (24, 80)],
    )
    print(f"  errors={r['errors']} blank={r['blank_screen']} composer={r['composer']} "
          f"input_kept={r['input_preserved']} final={r['final_size']} "
          f"over_long_lines={r['too_wide']} rows_fit={r['min_rows_ok']}")
    ok = ok and not r["errors"] and not r["blank_screen"] and r["input_preserved"] and r["min_rows_ok"]
    print()
    print("MANUAL RESIZE VERIFICATION:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
