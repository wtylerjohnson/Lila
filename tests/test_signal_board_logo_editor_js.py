"""Executed-JavaScript tests for Signal Board logo-size save coordination."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
EDITOR = Path(__file__).parents[1] / "ui" / "signal_board_logo_editor.js"


def _run_node(body: str) -> None:
    if NODE is None:
        pytest.skip("Node.js is required for editor JavaScript execution tests")
    script = f"""
const assert = require('node:assert/strict');
const {{
  attachmentFilename, createSerialSizeSaver, fetchCertifiedHtml,
}} = require({json.dumps(str(EDITOR))});

function deferred() {{
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => {{ resolve = yes; reject = no; }});
  return {{ promise, resolve, reject }};
}}

async function main() {{
{body}
}}

main().catch(error => {{
  console.error(error);
  process.exitCode = 1;
}});
"""
    subprocess.run(
        [NODE, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_logo_size_network_failure_restores_persisted_preview() -> None:
    _run_node("""
  const gate = deferred();
  let preview = 100;
  const calls = [];
  const commits = [];
  const failures = [];
  const saver = createSerialSizeSaver({
    readPersisted: () => 100,
    applyPreview: (_key, percent) => { preview = percent; },
    requestSave: item => {
      calls.push(item.percent);
      return gate.promise;
    },
    commit: (_key, percent) => commits.push(percent),
    succeed: () => assert.fail('a rejected save cannot succeed'),
    fail: (error, _item, persisted) => failures.push([
      error.message, persisted,
    ]),
  });

  const saving = saver.schedule({ key: 'agency:treas', percent: 135 });
  saver.schedule({ key: 'agency:treas', percent: 155 });
  assert.equal(preview, 155);
  assert.deepEqual(calls, [135]);

  gate.reject(new Error('network offline'));
  await saving;

  assert.equal(preview, 100);
  assert.deepEqual(calls, [135]);
  assert.deepEqual(commits, []);
  assert.deepEqual(failures, [['network offline', 100]]);
""")


def test_logo_size_rapid_changes_are_serialized_and_coalesced() -> None:
    _run_node("""
  const first = deferred();
  const latest = deferred();
  const gates = [first, latest];
  const calls = [];
  const commits = [];
  const successes = [];
  let preview = 100;
  let active = 0;
  let maxActive = 0;
  const saver = createSerialSizeSaver({
    readPersisted: () => 100,
    applyPreview: (_key, percent) => { preview = percent; },
    requestSave: async item => {
      const gate = gates[calls.length];
      calls.push(item.percent);
      active += 1;
      maxActive = Math.max(maxActive, active);
      try {
        return await gate.promise;
      } finally {
        active -= 1;
      }
    },
    commit: (_key, percent) => commits.push(percent),
    succeed: (_payload, item) => successes.push(item.percent),
    fail: error => assert.fail(error),
  });

  const saving = saver.schedule({ key: 'company:axon', percent: 120 });
  const coalesced = saver.schedule({ key: 'company:axon', percent: 140 });
  const latestSaving = saver.schedule({ key: 'company:axon', percent: 160 });
  assert.strictEqual(saving, coalesced);
  assert.strictEqual(saving, latestSaving);
  assert.deepEqual(calls, [120]);
  assert.equal(preview, 160);

  first.resolve({ message: 'saved first' });
  while (calls.length < 2) await new Promise(resolve => setImmediate(resolve));

  assert.deepEqual(calls, [120, 160]);
  assert.deepEqual(commits, [120]);
  assert.deepEqual(successes, []);
  assert.equal(preview, 160);
  assert.equal(maxActive, 1);

  latest.resolve({ message: 'saved latest' });
  await saving;

  assert.deepEqual(commits, [120, 160]);
  assert.deepEqual(successes, [160]);
  assert.equal(preview, 160);
  assert.equal(maxActive, 1);
""")


def test_certified_html_download_preserves_payload_and_attachment_name() -> None:
    _run_node("""
  const payload = { exactCertifiedBytes: true };
  const calls = [];
  const result = await fetchCertifiedHtml({
    downloadUrl: '/client/mark43/download/signal-board.html',
    editorToken: 'secret-session',
    fetchImpl: async (url, options) => {
      calls.push([url, options]);
      return {
        ok: true,
        status: 200,
        headers: {
          get: name => name === 'Content-Disposition'
            ? 'attachment; filename="Mark43 Federal Opportunity Pre-Assessment 2026-07-20.html"'
            : null,
        },
        blob: async () => payload,
      };
    },
  });

  assert.strictEqual(result.blob, payload);
  assert.equal(
    result.filename,
    'Mark43 Federal Opportunity Pre-Assessment 2026-07-20.html',
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/client/mark43/download/signal-board.html');
  assert.equal(calls[0][1].method, 'GET');
  assert.equal(calls[0][1].credentials, 'same-origin');
  assert.equal(
    calls[0][1].headers['X-LILA-Editor-Token'], 'secret-session');
  assert.equal(
    attachmentFilename('attachment; filename="../bad.txt"', 'safe.html'),
    'safe.html',
  );
""")
