"""CISA CDM Approved Products List and DEFEND primes (A6, 2026-08-18).

The APL page carries a standing competitive fact, quoted verbatim on the
store: "The APL is not accepting new submissions for the indefinite
future." Whoever is listed stays listed and no rival can be added. The
published workbook (April 2025 APL) is downloaded from the page's own
href, cached with its SHA-256, and parsed from the Product List sheet
(header row is the row whose first cell is "Product Manufacturer";
87,818 rows measured 2026-08-18).

Target matching is column-honest: a manufacturer-column match is APL
presence; a mention anywhere else (a rival named inside another
manufacturer's description) is counted separately and never claims
presence. Row links cite the cached file, sheet, and row number, with the
row's own cells as the receipt.

DEFEND primes ride USASpending award records (keyword pulls, group letter
parsed from the award description); a group with no award row stays
absent rather than guessed. Store:
data/state/cdm/<slug>.json, rendered later as a teaming lane and a
competitive fact. Zero LLM, zero SAM quota; bash is the sanctioned lane.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

APL_PAGE_URL = ("https://www.cisa.gov/resources-tools/programs/"
                "continuous-diagnostics-and-mitigation-cdm-program/"
                "program-approved-products-list-apl")
APL_FILE_URL = ("https://www.cisa.gov/sites/default/files/2025-05/"
                "April_2025_APL_4.23.25.xlsx")
APL_FILE_LABEL = "April 2025 CDM Approved Products List (APL)"
SUBMISSION_FREEZE = ("The APL is not accepting new submissions for the "
                     "indefinite future.")
FREEZE_RETRIEVED_AT = "2026-08-18"
PRODUCT_SHEET = "Product List"
MANUFACTURER_HEADER = "Product Manufacturer"
SAMPLE_ROWS = 3

_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = _ROOT / "data" / "cache" / "cdm"
STORE_DIR = _ROOT / "data" / "state" / "cdm"

USASPENDING_SEARCH = ("https://api.usaspending.gov/api/v2/search/"
                      "spending_by_award/")
DEFEND_KEYWORDS = (
    "CDM DEFEND",
    "DYNAMIC AND EVOLVING FEDERAL ENTERPRISE NETWORK DEFENSE",
    "CDM DEFEND GROUP D",
)
_GROUP = re.compile(r"\b(?:GROUP\s*|DEFEND\s+)([A-F])\b")

#: Versioned press-reported fact for award-record gaps (the SEWP_V_FACTS
#: pattern): stated once, cited, and tiered separately from award rows.
DEFEND_WEB_FACTS = {
    "D": {
        "prime": "Booz Allen Hamilton",
        "ceiling": "up to $1.03B, base year plus five option years",
        "agencies": ("GSA, HHS, NASA, SSA, Treasury, USPS"),
        "source_url": ("https://www.meritalk.com/articles/booz-allen-"
                       "hamilton-wins-second-cdm-defend-task-order-for-"
                       "group-d/"),
        "evidence_tier": ("press-reported, not award-record-backed; the "
                          "keyword pulls surfaced no Group D award row"),
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cached_apl_path() -> Path:
    return CACHE_DIR / APL_FILE_URL.rsplit("/", 1)[-1]


def download_apl(*, fetch: Optional[Callable[[str], bytes]] = None) -> Path:
    """Download the published workbook into the cache, once."""
    path = cached_apl_path()
    if path.exists() and path.stat().st_size > 1_000_000:
        return path
    if fetch is None:
        from tools.api._http import get_bytes

        def fetch(url: str) -> bytes:
            return get_bytes(url, timeout=90.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(fetch(APL_FILE_URL))
    return path


def parse_apl(path: Path, targets: list[str]) -> dict[str, Any]:
    """Per-target APL presence from the Product List sheet."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb[PRODUCT_SHEET]
    header_row_idx = None
    header: list[str] = []
    manufacturer_col = 0
    out: dict[str, Any] = {t: {
        "target": t, "on_apl": False, "manufacturer_row_count": 0,
        "mention_only_row_count": 0, "first_row": None,
        "sample_rows": [], "contract_holders": set(),
        "gsa_contracts": set(), "sewp_contracts": set(),
    } for t in targets}
    lows = {t: t.casefold() for t in targets}
    total = 0
    for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        cells = ["" if c is None else str(c).strip() for c in row]
        if header_row_idx is None:
            if cells and cells[0] == MANUFACTURER_HEADER:
                header_row_idx = idx
                header = cells
                manufacturer_col = 0
            continue
        if not any(cells):
            continue
        total += 1
        manufacturer = cells[manufacturer_col].casefold()
        joined = " ".join(cells).casefold()
        for target, low in lows.items():
            rec = out[target]
            if low in manufacturer:
                rec["on_apl"] = True
                rec["manufacturer_row_count"] += 1
                if rec["first_row"] is None:
                    rec["first_row"] = idx
                if len(rec["sample_rows"]) < SAMPLE_ROWS:
                    rec["sample_rows"].append({
                        "row": idx,
                        "row_link": (f"{APL_FILE_URL}#'{PRODUCT_SHEET}'"
                                     f"!A{idx}"),
                        "cells": dict(zip(header[:8], cells[:8])),
                    })
                if len(cells) > 6 and cells[6]:
                    rec["contract_holders"].add(cells[6])
                if len(cells) > 4 and cells[4]:
                    rec["gsa_contracts"].add(cells[4])
                if len(cells) > 5 and cells[5]:
                    rec["sewp_contracts"].add(cells[5])
            elif low in joined:
                rec["mention_only_row_count"] += 1
    for rec in out.values():
        rec["contract_holders"] = sorted(rec["contract_holders"])
        rec["gsa_contracts"] = sorted(rec["gsa_contracts"])
        rec["sewp_contracts"] = sorted(rec["sewp_contracts"])
    return {"targets": out, "product_rows": total,
            "header_row": header_row_idx, "header": header[:8]}


def defend_primes(*, post: Optional[Callable] = None) -> dict[str, Any]:
    """DEFEND primes per agency group from USASpending award records."""
    if post is None:
        import httpx

        def post(body: dict) -> dict:
            r = httpx.post(USASPENDING_SEARCH, json=body, timeout=60)
            r.raise_for_status()
            return r.json()

    fields = ["Award ID", "Recipient Name", "Description", "Award Amount",
              "Start Date", "End Date", "Awarding Sub Agency"]
    rows: list[dict] = []
    for kw in DEFEND_KEYWORDS:
        page = 1
        while page <= 3:
            data = post({
                "filters": {"time_period": [{"start_date": "2017-10-01",
                                             "end_date":
                                                 datetime.now(timezone.utc)
                                                 .date().isoformat()}],
                            "award_type_codes": ["A", "B", "C", "D"],
                            "keywords": [kw]},
                "fields": fields, "limit": 100, "page": page,
                "sort": "Award Amount", "order": "desc"})
            rows.extend(data.get("results") or [])
            if not (data.get("page_metadata") or {}).get("hasNext"):
                break
            page += 1
            time.sleep(0.2)
    groups: dict[str, dict] = {}
    for r in rows:
        desc = (r.get("Description") or "").upper()
        if "DEFEND" not in desc:
            continue
        m = _GROUP.search(desc)
        if not m:
            continue
        g = m.group(1)
        rec = r.get("Recipient Name")
        gid = r.get("generated_internal_id")
        entry = groups.setdefault(g, {"group": g, "awards": {}})
        a = entry["awards"].setdefault(rec, {
            "prime": rec, "obligated": 0.0, "award_ids": [],
            "sample_description": (r.get("Description") or "")[:160],
            "usaspending_urls": [],
        })
        a["obligated"] += r.get("Award Amount") or 0
        if r.get("Award ID") not in a["award_ids"]:
            a["award_ids"].append(r.get("Award ID"))
            a["usaspending_urls"].append(
                f"https://www.usaspending.gov/award/{gid}/")
    for g, entry in groups.items():
        ranked = sorted(entry["awards"].values(),
                        key=lambda x: -x["obligated"])
        entry["current_prime"] = ranked[0]["prime"] if ranked else None
        entry["awards"] = ranked
    missing = sorted(set("ABCDEF") - set(groups))
    return {"groups": {g: groups[g] for g in sorted(groups)},
            "groups_without_award_rows": missing,
            "web_reported_primes": {
                g: DEFEND_WEB_FACTS[g] for g in missing
                if g in DEFEND_WEB_FACTS},
            "note": ("A group absent here had no matching award row in the "
                     "keyword pulls; it is a named gap, never a guess."),
            "source": "usaspending_api.spending_by_award",
            "source_url": USASPENDING_SEARCH,
            "retrieved_at": _now_iso()}


def build_store(client_slug: str, targets: list[str], *,
                out_dir: Optional[Path] = None,
                apl_path: Optional[Path] = None,
                defend: Optional[dict] = None) -> tuple[Path, dict]:
    apl_path = apl_path or download_apl()
    parsed = parse_apl(apl_path, targets)
    sha = hashlib.sha256(apl_path.read_bytes()).hexdigest()
    payload = {
        "schema_version": 1,
        "client": client_slug,
        "generated_at": _now_iso(),
        "apl": {
            "label": APL_FILE_LABEL,
            "page_url": APL_PAGE_URL,
            "file_url": APL_FILE_URL,
            "cached_file": str(apl_path),
            "file_sha256": sha,
            "product_rows": parsed["product_rows"],
            "header": parsed["header"],
            "submission_freeze_quote": SUBMISSION_FREEZE,
            "submission_freeze_retrieved_at": FREEZE_RETRIEVED_AT,
            "competitive_fact": (
                "Submissions are frozen indefinitely: a listed vendor "
                "stays listed and an unlisted rival cannot be added."),
        },
        "targets": parsed["targets"],
        "defend": defend if defend is not None else defend_primes(),
        "source": "cisa_cdm_apl",
        "source_url": APL_PAGE_URL,
        "retrieved_at": _now_iso(),
    }
    out_dir = Path(out_dir) if out_dir else STORE_DIR
    out_path = out_dir / f"{client_slug}.json"
    from tools.artifacts import atomic_write_json
    atomic_write_json(out_path, payload)
    return out_path, payload


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--client", default="varonis")
    ap.add_argument("--targets", default="Varonis,BigID,Netwrix,Cyera,"
                                         "Everfox,Forcepoint,Proofpoint,"
                                         "Microsoft Purview")
    args = ap.parse_args(argv)
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    path, payload = build_store(args.client, targets)
    print(f"APL rows={payload['apl']['product_rows']} "
          f"sha={payload['apl']['file_sha256'][:16]}")
    for t in targets:
        rec = payload["targets"][t]
        holders = ",".join(rec["contract_holders"][:3])
        print(f"  {t:<18} on_apl={str(rec['on_apl']):<5} "
              f"mfr_rows={rec['manufacturer_row_count']:<5} "
              f"mentions={rec['mention_only_row_count']:<4} "
              f"first_row={rec['first_row']} holders={holders}")
    d = payload["defend"]
    for g, entry in d["groups"].items():
        print(f"  DEFEND {g}: {entry['current_prime']} "
              f"(${entry['awards'][0]['obligated']:,.0f}, "
              f"{entry['awards'][0]['award_ids'][0]})")
    if d["groups_without_award_rows"]:
        print(f"  DEFEND groups without award rows: "
              f"{d['groups_without_award_rows']}")
    print(f"WROTE {path}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
