"""Env-driven on/off switches for every external API — no code changes to flip one.

Convention: LILA_ENABLE_<NAME>=on|off in .env (or the real environment). <NAME> is
the registered name uppercased, with . and - replaced by _:

    LILA_ENABLE_SAM_GOV=off            # data source 'sam.gov'
    LILA_ENABLE_USASPENDING_GOV=off    # data source 'usaspending.gov'
    LILA_ENABLE_WEB=off                # data source 'web'
    LILA_ENABLE_APOLLO_HANDOFF=off     # contact finder 'apollo-handoff'
    LILA_ENABLE_ARBITER_OPENAI=on      # report arbiter 'arbiter-openai'
    LILA_ENABLE_INTAKE_AUTO_APPROVE=off  # restore the Step 1 human click
                                         # (default ON when unset)

Unset means the component's own default (sources/finders default on; the OpenAI
arbiter defaults OFF until you activate it). Real environment wins over .env, as
always (tools/env.py).
"""

from __future__ import annotations

import os

_TRUE = {"1", "true", "on", "yes", "y"}
_FALSE = {"0", "false", "off", "no", "n"}


def toggle_key(name: str) -> str:
    return "LILA_ENABLE_" + name.upper().replace(".", "_").replace("-", "_").replace(" ", "_")


def is_enabled(name: str, default: bool = True) -> bool:
    """The switch for a registered component. Unset -> `default`."""
    raw = os.environ.get(toggle_key(name))
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in _TRUE:
        return True
    if val in _FALSE:
        return False
    return default  # unparseable -> fail safe to the default
