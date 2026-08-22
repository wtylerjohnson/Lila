#!/bin/sh
# Morning job: pull the free daily sam.gov extract, fold it into the durable
# notice store, and FALL BACK TO THE FY ARCHIVE when the daily is dead.
#
# THE PULL. A keyless public S3 object, unmetered and unlimited, carrying
# every ACTIVE notice with full description text. The metered SAM API allows
# ~10 calls/day per key and cannot see this. Nothing here spends quota.
# download_extract() returns immediately if today's file already exists.
#
# THE INGEST. The extract is a snapshot and only two days are kept on disk,
# so a notice posted and closed between two looks would be lost forever. The
# store is the memory: append-only, keyed by notice id, first_seen and
# last_seen, and a notice sam.gov drops is kept rather than deleted.
#
# THE FALLBACK (added 2026-08-06, after a NINE-DAY silent stall). Upstream
# published a 685-byte header-only object every night from 2026-07-29
# onward. The floor correctly refused it, this job correctly did nothing,
# and NOTHING SAID SO: the store simply stopped growing while the reports
# kept pressing against stale evidence. A pull job that can only pull one
# way goes quiet exactly when it matters most.
#
# So when the daily refuses, sweep the FY archive for everything that has
# departed since the last ingest. That file is rebuilt weekly with the same
# 47-column schema and full descriptions, and the backfiller ingests each
# departure day under its own as_of so first_seen/last_seen keep meaning
# what they say. It cannot recover notices posted during the gap that are
# STILL ACTIVE; only a healed daily delivers those.
#
# NEITHER STEP MAY BLOCK ANYTHING. Every stage degrades to a no-op and
# reports through last_ingest rather than failing.

set -u
cd /Users/wtjohnson/federal-sales-os || exit 1

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) pull ==="
.venv/bin/python -c 'from tools.api.sam_extract import download_extract; print(download_extract())'
PULL=$?

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ingest ==="
.venv/bin/python -m tools.notice_store
INGEST=$?

if [ "$PULL" -ne 0 ]; then
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) daily refused; FY archive fallback ==="
  # No --window: the backfiller derives it as (last ingest + 1)..today, so a
  # one-day blip costs one small slice and a long outage heals in full.
  .venv/bin/python -m tools.sam_archive_backfill
  BACKFILL=$?
  echo "=== fallback rc=$BACKFILL ==="
else
  BACKFILL=0
fi

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) series ==="
.venv/bin/python -m tools.notice_store --log

echo "=== done: pull=$PULL ingest=$INGEST backfill=$BACKFILL ==="
