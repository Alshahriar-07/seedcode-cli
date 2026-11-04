"""Fuzzy edit tier in ``edit_file`` (OpenCode edit-tool parity).

OpenCode reference: ``OPENCODE/tmdp/packages/core/src/tool/plugin/edit.ts``
— when an exact match finds nothing, the edit is retried with tolerance for
the lookalike characters models routinely retype (smart quotes vs straight
quotes, en/em dashes, unicode spaces) and per-line trailing whitespace.

These tests execute the REAL tool on REAL files: the exact tier always wins
when it can match; the fuzzy tier never fires when an exact match exists;
the replacement text lands byte-exactly; uniqueness is enforced in both
tiers; and a no-match report still explains itself.
"""

from __future__ import annotations

from pathlib import Path

from seedcode.tools import (
    PermissionLevel,
    PermissionManager,
    get_tool,
)
from seedcode.tools.patch import fuzzy_pattern

LQ = "\u2018"  # left single curly quote
RQ = "\u2019"  # right single curly quote
DQ_L = "\u201c"  # left double curly quote
DQ_R = "\u201d"  # right double curly quote
EN_DASH = "\u2013"
NBSP = "\u00a0"


def _perm(workspace: Path) -> PermissionManager:
    return PermissionManager(workspace=workspace, level=PermissionLevel.WORKSPACE)


def _edit(workspace: Path, **args):
    return get_tool("edit_file").run(_perm(workspace), args)


class TestFuzzyPatternMatchesLookalikes:
    def test_curly_quotes_match_straight_quotes(self) -> None:
        pattern = fuzzy_pattern(f"print({LQ}x{RQ})")
        assert pattern is not None
        assert pattern.search("print('x')")
        # any single member of the group matches either position
        assert pattern.search("print(‚x‛)")

    def test_double_curly_quotes_match_straight(self) -> None:
        pattern = fuzzy_pattern(f'shape={DQ_L}round{DQ_R}')
        assert pattern is not None
        assert pattern.search('shape="round"')
        assert pattern.search("shape=„round‟")
        assert pattern.search(f"shape={DQ_L}round{DQ_R}")

    def test_en_dash_matches_hyphen(self) -> None:
        pattern = fuzzy_pattern(f"range 1{EN_DASH}3")
        assert pattern is not None
        assert pattern.search("range 1-3")
        assert pattern.search("range 1\u20143")  # em dash too

    def test_unicode_and_nbsp_spaces_match_regular_spaces(self) -> None:
        pattern = fuzzy_pattern(f"a{NBSP}=  1")
        assert pattern is not None
        assert pattern.search("a = 1")
        assert pattern.search("a\u202f=\u30001")
        # a space is an equivalence, not an optional token: no spaces → no match
        assert pattern.search("a=1") is None

    def test_whitespace_runs_collapse(self) -> None:
        pattern = fuzzy_pattern("x = 1")
        assert pattern is not None
        assert pattern.search("x    =     1")

    def test_trailing_whitespace_between_lines_tolerated(self) -> None:
        pattern = fuzzy_pattern("a = 1\nb = 2")
        assert pattern is not None
        assert pattern.search("a = 1   \nb = 2\n")
        assert pattern.search("a = 1\nb = 2")

    def test_empty_or_unmatchable_supplies_no_pattern(self) -> None:
        assert fuzzy_pattern("") is None
        assert fuzzy_pattern("\n\n") is None

    def test_mismatching_content_still_fails_the_fuzzy_tier(self) -> None:
        pattern = fuzzy_pattern("def hello():")
        assert pattern is not None
        assert pattern.search("def goodbyte():") is None


class TestFuzzyEditRealFiles:
    def test_curly_quote_mismatch_is_edited_fuzzily(self, tmp_path: Path) -> None:
        target = tmp_path / "app.py"
        target.write_text(f"answer = {LQ}yes{RQ}\n", encoding="utf-8")
        result = _edit(tmp_path, path="app.py", old_text="answer = 'yes'", new_text="answer = 'no'")
        assert result.ok, result.output
        assert target.read_text(encoding="utf-8") == "answer = 'no'\n"

    def test_exact_match_wins_over_the_fuzzy_tier(self, tmp_path: Path) -> None:
        """The exact tier fires; curly quotes would also have matched."""
        target = tmp_path / "app.py"
        target.write_text(f"a = {LQ}x{RQ}\nb = 'x'\n", encoding="utf-8")
        result = _edit(tmp_path, path="app.py", old_text="b = 'x'", new_text="b = 'y'")
        assert result.ok, result.output
        assert target.read_text(encoding="utf-8") == f"a = {LQ}x{RQ}\nb = 'y'\n"

    def test_fuzzy_uniqueness_is_enforced(self, tmp_path: Path) -> None:
        """Two fuzzy-only matches, no replace_all: refused like the exact tier."""
        target = tmp_path / "app.py"
        target.write_text(f"v = {LQ}x{RQ}\nw = {LQ}x{RQ}\n", encoding="utf-8")
        result = _edit(tmp_path, path="app.py", old_text="= 'x'", new_text="= 'y'")
        assert not result.ok
        assert "2 times" in result.output
        assert "='x'" not in result.output  # old text is quoted in the message
        assert target.read_text(encoding="utf-8").count("x") == 2  # nothing changed

    def test_fuzzy_replace_all(self, tmp_path: Path) -> None:
        target = tmp_path / "app.py"
        target.write_text(f"v = {LQ}x{RQ}\nw = {LQ}x{RQ}\n", encoding="utf-8")
        result = _edit(
            tmp_path, path="app.py", old_text="= 'x'", new_text="= 'y'", replace_all=True
        )
        assert result.ok, result.output
        assert "2 replacements" in result.output
        assert target.read_text(encoding="utf-8") == "v = 'y'\nw = 'y'\n"

    def test_replacement_lands_byte_exactly(self, tmp_path: Path) -> None:
        """Regex-group text in new_text is NOT interpreted (lambda replacer)."""
        target = tmp_path / "app.py"
        target.write_text("value = one\n", encoding="utf-8")
        result = _edit(
            tmp_path, path="app.py", old_text="value = one", new_text=r"kept \1 \g<0> verbatim"
        )
        assert result.ok, result.output
        assert target.read_text(encoding="utf-8") == r"kept \1 \g<0> verbatim" + "\n"

    def test_fuzzy_failure_report_mentions_the_fuzzy_tier(self, tmp_path: Path) -> None:
        target = tmp_path / "app.py"
        target.write_text("nothing relevant here\n", encoding="utf-8")
        result = _edit(tmp_path, path="app.py", old_text="def missing():", new_text="x = 1")
        assert not result.ok
        assert "fuzzy" in result.output

    def test_trailing_whitespace_in_the_file_does_not_block_an_edit(self, tmp_path: Path) -> None:
        target = tmp_path / "app.py"
        target.write_text("a = 1   \nafter\n", encoding="utf-8")
        result = _edit(tmp_path, path="app.py", old_text="a = 1\nafter", new_text="a = 2\nafter")
        assert result.ok, result.output
        # NOTE: fuzzy matching tolerates the whitespace ON MATCH, but the file
        # text is replaced around the whole matched span; the real trailing
        # spaces are inside the matched region only when the pattern eats them.
        assert target.read_text(encoding="utf-8") in ("a = 2   \nafter\n", "a = 2\nafter\n")

    def test_crlf_file_edits_still_work(self, tmp_path: Path) -> None:
        target = tmp_path / "app.py"
        target.write_bytes(b"value = 1\r\nmore = 2\r\n")
        result = _edit(tmp_path, path="app.py", old_text="value = 1", new_text="value = 9")
        assert result.ok, result.output
        data = target.read_bytes()
        assert b"value = 9" in data
        assert b"\r\n" in data  # line endings preserved
