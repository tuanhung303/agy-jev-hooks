"""JevGrep semantic code search, Python port.

Port of https://github.com/nassim-arifette/jevgrep v0.1.0 by Nassim Arifette
(MIT, Copyright (c) 2026 Nassim Arifette). See NOTICE in this directory for the
license text and the deliberate divergences from upstream.

Find code by what it does: the engine prepares eligible repository fragments,
asks Jev to score them against a search question, and returns exact source
excerpts with paths and line numbers under a measured response budget.
"""
__version__ = "0.1.0-py1"

from .contracts import create_search_error, parse_search_request  # noqa: F401
from .engine import SearchEngine  # noqa: F401
