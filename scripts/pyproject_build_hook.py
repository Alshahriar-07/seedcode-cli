"""Hatchling build hook: keep the embedded key module out of PyPI artifacts.

The generated ``seedcode/_default_key.py`` is a release-EXE-only artifact
(git-ignored, produced by ``scripts/windows/embed_default_key.py``). This
hook guarantees it can never enter a wheel or sdist:

* the repository ``.gitignore`` already excludes it and hatchling applies
  VCS exclusion patterns to wheel/sdist builds;
* this hook additionally *reserves* the file's distribution path (hatchling
  excludes reserved paths from normal file recursion) so the exclusion holds
  even for ``pip install .`` / editable builds made from a build-machine
  tree where the generated module exists — exactly the flow
  ``scripts/windows/build.bat`` uses right before PyInstaller packaging.

An explicit refusal guards the reverse mistake: if a plain (non-editable)
``python -m build`` runs while the secret module exists, the build fails
loudly instead of silently relying on pattern matching.
"""

from __future__ import annotations

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_GENERATED = "seedcode/_default_key.py"


class DefaultKeyExclusionHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        if not (Path(self.root) / _GENERATED).exists():
            return  # source checkout without the generated module: nothing to do

        if version == "editable":
            # Editable builds from the release pipeline: exclude, never refuse.
            build_data["force_include_exclude"] = [_GENERATED]
            return

        # Non-editable wheel/sdist: refuse so the secret can never ship.
        raise SystemExit(
            f"BUILD REFUSED: {_GENERATED} exists. This generated secret module "
            "must never be packaged into a wheel or sdist. Run: "
            "python scripts/windows/embed_default_key.py --clean"
        )
