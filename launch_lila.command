#!/bin/zsh
# LILA Control Room launcher — double-clicked via the Desktop icon.
# Always runs the CURRENT code: any existing server is stopped first, so a
# stale process can never serve yesterday's UI again.
cd "$HOME/federal-sales-os" || exit 1

if pgrep -f "run_ui.py" > /dev/null; then
  echo "Stopping previous LILA server..."
  pkill -f "run_ui.py"
  sleep 1
fi

# Open the browser once the fresh server has had a moment to bind.
( sleep 2; open "http://127.0.0.1:8321" ) &

echo "Starting LILA control room (Ctrl+C to stop)..."
exec python3 run_ui.py
