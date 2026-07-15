"""Enable ``python -m seedcode``.

Uses an absolute import so this module also works as a PyInstaller entry
script, where it runs as top-level ``__main__`` with no parent package.
"""

from __future__ import annotations

from seedcode.cli import main

if __name__ == "__main__":
    main()
