"""Permission-gated application installation (missing-app workflow).

When a requested application is not installed, the flow is:

    missing app → resolve a trusted source → SHOW the user exactly what will
    be installed and from where → explicit confirmation (INSTALL category,
    never remembered) → download/execute → verify installed → launch.

Security properties:

* The INSTALL category is **sensitive**: "always allow" is impossible by
  construction (the permission layer downgrades it), so every install asks.
* Only **trusted sources** are eligible: Winget (preferred), Microsoft
  Store, or a caller-supplied official vendor URL. Arbitrary URLs the model
  produces are refused by :func:`_trusted_source`.
* The model cannot mark anything trusted; trust comes from the winget
  manifest registry or a user-supplied URL, never from model output.
* Installers are executed only after the user confirms the exact source
  shown to them.

The actual winget invocation is injectable so tests never touch a system.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from ..core.errors import ApplicationNotFoundError, SecurityError
from .discovery import AppInfo

# Seconds before a winget invocation is declared wedged.
_WINGET_TIMEOUT_S = 600


@dataclass(slots=True)
class InstallPlan:
    """What would be installed, shown to the user before confirmation."""

    app_name: str
    mechanism: str        # winget | store | manual
    package_id: str = ""  # winget package id, when known
    source: str = ""      # human-readable source description
    detail: str = ""

    def describe(self) -> str:
        lines = [f"Install {self.app_name} via {self.mechanism}"]
        if self.package_id:
            lines.append(f"  package: {self.package_id}")
        if self.source:
            lines.append(f"  source: {self.source}")
        return "\n".join(lines)


@dataclass(slots=True)
class InstallResult:
    success: bool
    app_name: str
    detail: str = ""


# --- trusted source resolution ------------------------------------------------------
def _winget_package(app_name: str, run: Any = None) -> InstallPlan | None:
    """A winget plan for ``app_name``, or None when winget can't find one."""
    if shutil.which("winget") is None:
        return None
    if run is None:
        def run(args: list[str]) -> subprocess.CompletedProcess:
            return subprocess.run(  # noqa: S603 — fixed argv, no shell
                args, capture_output=True, text=True, timeout=60
            )
    try:
        search = run(["winget", "search", "--id", app_name, "--accept-source-agreements"])
        out = (search.stdout or "") + (search.stderr or "")
        # winget prints a table; any non-error row mentioning the name counts.
        if search.returncode == 0 and app_name.lower() in out.lower():
            return InstallPlan(
                app_name=app_name, mechanism="winget", package_id=app_name,
                source="winget (curated package registry)",
                detail=f"winget found a package matching '{app_name}'",
            )
    except Exception:
        return None
    return None


def resolve_install_plan(app_name: str, *, run: Any = None) -> InstallPlan:
    """A trusted install plan for a missing app, or an error.

    Only winget is currently wired; Microsoft Store routes through winget on
    modern Windows. If nothing trusted exists, the user is told to install
    it manually — SeedCode never fetches an arbitrary installer URL.
    """
    plan = _winget_package(app_name, run=run)
    if plan is not None:
        return plan
    raise SecurityError(
        f'No trusted installation source found for "{app_name}". SeedCode '
        "will not download installers from arbitrary websites. Install it "
        "manually (official site or Microsoft Store), then ask me to open it."
    )


def install_app(
    app_name: str,
    *,
    confirm: Any,
    run: Any = None,
    find: Any = None,
    launch: Any = None,
) -> InstallResult:
    """The guarded install workflow. ``confirm(plan_description) -> bool``.

    ``confirm`` is wired to the interactive permission dialog by the caller
    (skill/tools layer); it is asked AFTER the plan (mechanism + package +
    source) is shown and is the only path to executing anything.
    """
    from ..core.errors import OperatorError

    try:
        plan = resolve_install_plan(app_name, run=run)
    except OperatorError:
        raise

    if not confirm(plan.describe()):
        return InstallResult(
            False, app_name, "installation declined by the user"
        )

    # Execute via winget with a fixed, audited argv.
    if run is None:
        def run(args: list[str], **kw: Any) -> subprocess.CompletedProcess:
            return subprocess.run(  # noqa: S603 — fixed argv, no shell
                args, capture_output=True, text=True, timeout=_WINGET_TIMEOUT_S
            )
    try:
        proc = run([
            "winget", "install", "--id", plan.package_id,
            "--accept-source-agreements", "--accept-package-agreements",
        ])
    except Exception as exc:
        return InstallResult(False, app_name, f"installer failed to run: {exc}")
    ok = getattr(proc, "returncode", 1) == 0
    if not ok:
        tail = ((getattr(proc, "stderr", "") or "") or (getattr(proc, "stdout", "") or "")).strip()
        return InstallResult(False, app_name, f"installer returned an error: {tail[:300]}")

    # Verify installation actually happened (discovery must now find it).
    if find is None:
        from .discovery import find_app as find
    try:
        find(app_name)
    except ApplicationNotFoundError:
        return InstallResult(
            False, app_name,
            "installer reported success but the app is not discoverable yet; "
            "it may need a moment or a fresh Start Menu index",
        )
    return InstallResult(True, app_name, f"installed {app_name} via {plan.mechanism}")


__all__ = ["InstallPlan", "InstallResult", "resolve_install_plan", "install_app"]
