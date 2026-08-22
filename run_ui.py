#!/usr/bin/env python3
"""Launch the LILA control room (local web dashboard).

    python3 run_ui.py            # -> http://127.0.0.1:8321
    LILA_UI_PORT=9000 python3 run_ui.py

Pipeline board, strategy review gates, step runners with live logs, deliverable
viewer, API toggle panel. Localhost only — this thing can spend API credits.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
