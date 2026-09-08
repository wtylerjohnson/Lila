#!/bin/zsh
# One operator run location; historical/development worktrees are preserved.
set -eu
cd /Users/wtjohnson/Lila
print "LILA operating checkout: $PWD"
git log -1 --format='Version: %h %s'
exec .venv/bin/python run_ui.py
