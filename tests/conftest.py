"""Suite-wide test defaults, applied before any test module imports sage.

sage.config loads the user's ~/.config/agy/sage.env overlay at import time,
which would inject the live AGY_JEV_API_KEY into every test process and let
the Jev transport reach the real gateway. Force the key to empty so every Jev
call stays inert (fail-open) unless a test explicitly patches
sage.jev.transport or module-level helpers.
"""
import os

os.environ["AGY_JEV_API_KEY"] = ""
