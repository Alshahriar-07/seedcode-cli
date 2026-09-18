"""Embedded default OpenRouter credential (v6.2.0 build-time artifact).

This file is NOT committed to source control. The release build generates
``seedcode/_default_key.py`` from the local ``.env`` (see
``scripts/windows/embed_default_key.py``). Source checkouts without that
generated file fall back to the empty default below, so behavior is
unchanged for developers and CI.
"""

DEFAULT_OPENROUTER_API_KEY = ""
DEFAULT_API_ENABLED = False
DEFAULT_API_NOTE = "embedded default key unavailable (development checkout)"
