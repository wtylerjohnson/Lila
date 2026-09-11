"""Offline Step2 before/after audit. Writes only to an explicit output directory."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from agents.decisions.triage import deterministic_prefilter
from tools.relevance.taxonomy import load_taxonomy, retrieval_vocabulary, vocabulary_receipt


def audit(sweep_path: Path, baseline_path: Path, client: str, expected_sha: str) -> tuple[list, dict]:
    payload = sweep_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha:
        raise ValueError("saved sweep digest does not match the declared input")
    sweep = json.loads(payload)
    notices = sweep["results"]["sam.gov"]
    baseline_rows = [json.loads(line) for line in baseline_path.read_text().splitlines() if line]
    if [r["source_id"] for r in baseline_rows] != [n["source_id"] for n in notices]:
        raise ValueError("baseline and sweep must have identical native-order identities")
    if len({n['source_id'] for n in notices}) != len(notices):
        raise ValueError("duplicate input source identity")
    for before, notice in zip(baseline_rows, notices):
        record_sha = hashlib.sha256(json.dumps(notice, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if record_sha != before['original_record_sha256']:
            raise ValueError("baseline record digest differs from saved source")
    tax = load_taxonomy(client)
    if tax is None:
        raise ValueError("a versioned taxonomy is required")
    # Match baseline043: this audit does not re-adjudicate engagement scope.
    candidates, ruled, receipt = deterministic_prefilter(notices, tax)
    records = []
    for position, before in enumerate(baseline_rows, 1):
        sid = before['source_id']
        after = receipt['screening_records'][sid]
        records.append({
            'native_position': position, 'source_id': sid,
            'title': before['title'], 'url': before.get('url'),
            'recorded_retrieval_matches': before['recorded_retrieval_matches'],
            'before': before['saved_verdict'],
            'after': ruled.get(sid, {'verdict': 'model_review_required'}),
            'evidence': after,
            'stage_changed': after['stage'] != 'deterministic_screen',
            'disposition_changed': before['saved_verdict']['verdict'] != ruled.get(sid, {}).get('verdict'),
        })
    by_term = []
    for mapping in tax.retrieval_mappings:
        members = [r for r in records if mapping.term in r['recorded_retrieval_matches']]
        by_term.append({**mapping.model_dump(), 'recorded_retrieval_count': len(members),
                        'screen_states': dict(Counter(r['evidence']['screen_state'] for r in members)),
                        'source_ids': [r['source_id'] for r in members]})
    summary = {
        'input_sha256': digest, 'baseline_sha256': hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        'candidate_census': len(notices), 'model_candidates': len(candidates),
        'dispositions': dict(Counter(r['after']['verdict'] for r in records)),
        'screen_states': dict(Counter(r['evidence']['screen_state'] for r in records)),
        'stage_changed_ids': [r['source_id'] for r in records if r['stage_changed']],
        'disposition_changed_ids': [r['source_id'] for r in records if r['disposition_changed']],
        'supported_capability_ids': [r['source_id'] for r in records if r['evidence']['relevance']['core_terms']],
        'by_retrieval_term': by_term,
        'vocabulary': vocabulary_receipt(tax, retrieval_vocabulary(tax)),
        'limits': ['Development replay of frozen inputs, not a fresh capture or independent holdout.',
                   'No LLM judgment, qualification or engagement-scope re-adjudication.',
                   'Accumulated-store term yield is not the final daily-extract population.',
                   'Positive matches are review candidates, not verified false negatives or leads.'],
    }
    if hashlib.sha256(sweep_path.read_bytes()).hexdigest() != digest:
        raise ValueError("saved source changed during audit")
    return records, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sweep', required=True, type=Path)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--client', required=True)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    records, summary = audit(args.sweep, args.baseline, args.client, args.expected_sha)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'saved356_after.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
    (args.output_dir / 'audit_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({k: summary[k] for k in ('candidate_census', 'model_candidates', 'dispositions', 'screen_states')}, indent=2))


if __name__ == '__main__':
    main()
