"""Generate docs/reports/SeedCode-CLI-v9.2.0-Technical-Report.docx."""
from __future__ import annotations

import json
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "docs" / "reports" / "SeedCode-CLI-v9.2.0-Technical-Report.md"
OUT = ROOT / "docs" / "reports" / "SeedCode-CLI-v9.2.0-Technical-Report.docx"
MANIFEST = ROOT / "dist" / "release" / "9.2.0" / "BUILD-MANIFEST.json"


def _add_runs(paragraph, text: str, *, base_bold: bool = False) -> None:
    """Render **bold** and `code` inline fragments of a markdown line."""
    pos = 0
    for match in re.finditer(r"\*\*(.+?)\*\*|`([^`]+)`", text):
        if match.start() > pos:
            paragraph.add_run(text[pos : match.start()]).bold = base_bold
        if match.group(1) is not None:
            paragraph.add_run(match.group(1)).bold = True
        else:
            run = paragraph.add_run(match.group(2))
            run.font.name = "Consolas"
            run.bold = base_bold
        pos = match.end()
    if pos < len(text):
        run = paragraph.add_run(text[pos:])
        run.bold = base_bold


def _p(doc, text, style=None, size=None, bold=False):
    paragraph = doc.add_paragraph(style=style)
    paragraph.paragraph_format.space_after = Pt(4)
    _add_runs(paragraph, text, base_bold=bold)
    if size is not None:
        for run in paragraph.runs:
            run.font.size = Pt(size)
    return paragraph


def _table(doc, headers, rows):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Light Grid Accent 1"
    for col, header in enumerate(headers):
        cell = table.rows[0].cells[col]
        cell.text = ""
        _p(cell, f"**{header}**", size=9)
    for row_i, row in enumerate(rows, start=1):
        for col, value in enumerate(row):
            cell = table.rows[row_i].cells[col]
            cell.text = ""
            _p(cell, str(value), size=9)
    doc.add_paragraph()


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    tokens = {"artifact": manifest["artifacts"][0]["name"], "sha256": manifest["artifacts"][0]["sha256"]}
    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("Seed Code CLI v9.2.0 — Technical Report")
    run.bold = True
    run.font.size = Pt(20)
    run.font.color.rgb = RGBColor(0x0B, 0x66, 0x23)

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run("Version 9.2.0 · Tag v9.2.0 · Built 2026-11-02 · Windows 11 x64\n"
                           "github.com/Alshahriar-07/seedcode-cli")
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    def heading(text, level=1):
        heading_par = doc.add_heading("", level=level)
        _add_runs(heading_par, text)
        heading_par.runs[0].font.color.rgb = RGBColor(0x0B, 0x66, 0x23)

    heading("1. Project Overview")
    _p(doc, "Seed Code CLI is an autonomous AI coding agent for the terminal. The user types a "
            "task; the agent analyses it, inspects the workspace, builds a plan, executes it through "
            "real tool calls, verifies each result and reports the outcome honestly. v9.2.0 completes "
            "the single-agent pivot and adds the model-thinking panel and the right-side task plan panel.")

    heading("2. Architecture Overview")
    flow = (
        ("User request", "→ task understanding (deterministic classifier)"),
        ("Planning", "→ workspace inspection → TODO state"),
        ("Orchestrator loop", "→ provider → tool registry → execution"),
        ("Validation", "→ result returned to model → next task"),
        ("Completion", "→ verification → final report"),
    )
    for label, arrow in flow:
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(1)
        paragraph.add_run(f"{label}  ").bold = True
        paragraph.add_run(arrow)
    doc.add_paragraph()
    _p(doc, "Key properties verified by tests: sequential multi-tool calls; tool results returned "
            "to the model; bounded iteration limits; tool failures surfaced as failures; UI driven "
            "only by real orchestrator events.")

    heading("3. Major Bugs Fixed")
    _table(doc, ["Bug", "Fix"], [
        ("NameError: name 'active_workspace' is not defined",
         "Permission manager constructed with the selected workspace."),
        ("ImportError: AgentEngine entry-point drift",
         "Import surface consolidated on seedcode.core.agent.AgentEngine."),
        ("Ollama connected but no thinking visible",
         "Capability detection via GET /api/show; think flag for capable models; ThinkingDelta emitted, relayed, rendered."),
        ("Installer rejected the displayed version banner",
         "Robust semantic-version extraction; regression-tested."),
        ("False completion reports",
         "Evidence-based verification: files re-read, exit codes parsed, tests inspected."),
    ])

    heading("4. Agent Orchestration Design")
    _p(doc, "AgentEngine owns the turn lifecycle. Multiple ```tool blocks per reply dispatch exactly one "
            "tool call each; the real result is fed back and execution continues. A model-free classifier "
            "selects needed context; TODO state updates only from observed outcomes — never from the "
            "model's claim of completion.")

    heading("5. Tool Calling Improvements")
    _table(doc, ["Tool", "Verified behaviour"], [
        ("run_command", "stdout/stderr/exit code/duration/cwd; PowerShell + POSIX; timeouts"),
        ("read_file", "encoding-aware reads with line numbers"),
        ("write_file / edit_file", "create/update with post-write verification"),
        ("list_dir / make_dir / move / copy / delete / exists", "workspace-boundary validated"),
        ("search", "code + filename search; caches excluded"),
        ("research / web_search", "keyless search; query and sources surfaced as real events"),
        ("inspect_project", "structure, languages, dependencies, entry points"),
    ])
    _p(doc, "Parameter validation happens before dispatch; dispatch errors return the real error to the "
            "model. Workspace permissions prevent writes outside the workspace; dangerous actions ask first.")

    heading("6. Ollama Improvements")
    for item in [
        "Reachability check (GET /api/tags) and live model listing.",
        "Model availability validated before generation; auto-start never duplicates a running server.",
        "Streaming robustness: partial/malformed chunks, empty responses, interruptions and timeouts surface real errors.",
        "Capability detection: think flag sent only for thinking-capable models; silent degradation otherwise.",
        "ThinkingDelta events stream the model's own reasoning to the UI.",
        "The selected provider is never silently switched; failover is opt-in.",
    ]:
        _p(doc, item, style="List Bullet")

    heading("7. TODO UI — Right-Side Task Plan Panel")
    for item in [
        "States: ○ pending · ● in progress · ✓ completed · ✗ failed · ↻ retrying.",
        "Driven only by orchestrator task events (task_start, task_done, task_retry, task_failed, plan updates).",
        "Live N / M completed progress; 'All tasks completed' only when all verified.",
        "Narrow-width degradation; resizing re-fits without breaking the conversation layout.",
    ]:
        _p(doc, item, style="List Bullet")

    heading("8. Thinking UI")
    for item in [
        "Shows only ThinkingDelta content the model itself exposes; never invented, never mixed with the response.",
        "Bounded height (~25% of terminal, clamped); responsive to resizing.",
        "Auto-scroll follows the stream unless the user scrolls up; collapses after the turn.",
    ]:
        _p(doc, item, style="List Bullet")

    heading("9. Completion Verification")
    _p(doc, "Created/modified files are re-read after write; terminal commands report parsed real exit "
            "codes shown as ✗ on failure; test runs report real counts; a multi-step task is COMPLETED "
            "only when every TODO item is verified, otherwise PARTIALLY COMPLETED or FAILED with the "
            "unfinished items named.")

    heading("10. Installer Improvements")
    for item in [
        "setup.iss extracts the semantic version from the installed exe banner and compares it with the installer's version.",
        "install.ps1 / install.sh verify downloads against the release SHA256SUMS.txt and abort on any mismatch.",
        "SHA256SUMS.txt uses LF newlines so POSIX verification never sees stray CR bytes.",
    ]:
        _p(doc, item, style="List Bullet")

    heading("11. Test Results")
    tests = manifest["test_status"]
    _table(doc, ["Metric", "Result"], [
        ("Total tests", tests["total"]),
        ("Passed", tests["passed"]),
        ("Failed", tests["failed"]),
        ("Skipped", tests["skipped"]),
        ("New regression tests", "40 (thinking stream, task panel, installer version, terminal title)"),
    ])

    heading("12. Build Results")
    rows = [
        (a["name"], f"{a['size_bytes']:,}", "verified" if a["sha_verified"] else "NOT verified")
        for a in manifest["artifacts"]
    ]
    _table(doc, ["Artifact", "Size (bytes)", "SHA256"], rows)
    _p(doc, f"Checksum file: {manifest['checksum_verification']} — hashes generated from the actual final "
            "files by stage_release.py, then re-verified with sha256sum -c.")

    heading("13. Known Limitations")
    for item in manifest["known_limitations"]:
        _p(doc, item, style="List Bullet")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT)
    print(f"[OK] wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
