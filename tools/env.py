"""Minimal .env loader (no python-dotenv dependency).

Reads KEY=VALUE lines from the project-root .env into os.environ without overriding
variables already set in the real environment. Called at the top of the run_*.py CLIs
so persisted keys (SAM_GOV_API_KEY, ANTHROPIC_API_KEY) are picked up automatically.
"""

from __future__ import annotations

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env(path: str | None = None) -> None:
    path = path or os.path.join(_ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)  # real env wins over .env
