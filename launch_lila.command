#!/bin/zsh
# Delegate to the operator-approved checkout and its virtual environment.
# Do not select a historical checkout, use ambient Python, or kill unrelated
# run_ui.py processes. The canonical launcher remains the single entry point.
set -eu
exec /bin/zsh /Users/wtjohnson/Lila/LILA.command
