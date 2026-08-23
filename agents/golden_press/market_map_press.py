"""Legacy internal Market Map studio press: pack in, migration view out.

WHERE THIS SITS. This is NOT a second architecture. `golden_press` builds
the one immutable EvidencePack through every research lane, and this module
is an internal RENDER of that same pack: typed projection, deterministic
renderer, its own validator, its own artifact family (`<slug>.market_map.*`)
beside the golden family. A replay from the saved pack re-renders the same
document byte for byte, because nothing here re-researches anything.

WHY IT IS ADDITIVE AND FAIL-SOFT. This compatibility press cannot authorize
the external LILA product; a studio defect must never cost the internal pack.
Every failure here is caught, logged loudly, and recorded in the returned
receipt, and the golden result ships exactly as it always did.

ZERO LLM, ZERO NETWORK, ZERO METERED QUOTA. The projection reads the pack
and the client profile; the renderer is code; the validator is code. The
whole render leg is replayable offline by construction.

CERTIFICATION IS FAIL-CLOSED. The document is certified only when its own
validator returns zero violations AND the style contract finds no unstyled
region AND the banned-string lint is clean. An uncertified Market Map is
written to data/state with a FAILED marker and never reaches the Desktop,
under the same promotion law as the golden family.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

MARKET_MAP_PRESS_VERSION = "market_map_press.v1.2026-08-07"

_EMDASH = "—"
_STUDIO_SELECTOR = re.compile(
    r"(?:\.studio-(?:toolbar|status)\b|\.is-editing\b|"
    r"\.logo-slot\.is-drop\b|\[data-(?:edit-id|edit-link|action)\b)")


def _lint(html: str) -> list:
    """The banned-string lint, over rendered text only (not CSS/JS)."""
    body = re.sub(r"<script.*?</script>", "", html, flags=re.S | re.I)
    body = re.sub(r"<style.*?</style>", "", body, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", body)
    violations = []
    if _EMDASH in text:
        violations.append({"rule": "em_dash",
                           "detail": "em dash present in rendered text"})
    if re.search(r"corridor", text, re.I):
        violations.append({"rule": "banned_term",
                           "detail": "the banned term is present"})
    for match in re.findall(r"\b[0-9a-f]{32}\b", text):
        violations.append({"rule": "raw_notice_id",
                           "detail": f"raw 32-char notice id rendered: "
                                     f"{match[:8]}..."})
        break
    return violations


def _receipts_appendix(receipts: dict) -> str:
    """Every drawer receipt as a real, scriptless `<details>` block.

    THE CLIENT BUILD'S RECEIPTS MUST OPEN (grading round 1, fatal: the
    stripped build shipped twenty dead buttons under a page that promises
    "every amount opens its receipt"). The studio opens receipts with its
    runtime; the client build opens the same receipts with native
    `<details>`, and every former trigger becomes an anchor to its entry.
    Print forces them open, so the paper copy carries the evidence too.
    """
    if not receipts:
        return ""
    blocks = []
    for key in sorted(receipts):
        entry = receipts[key] or {}
        title = str(entry.get("title") or key)
        summary = str(entry.get("summary") or "")
        formula = str(entry.get("formula") or "")
        links = "".join(
            f'<a class="source-link" href="{url}" target="_blank" '
            f'rel="noopener noreferrer">{label} ↗</a> '
            for label, url in (entry.get("sources") or []) if url)
        blocks.append(
            f'<details class="inline-work" id="receipt-{key}">'
            f"<summary>{title}</summary>"
            f'<p class="receipt-copy">{summary}</p>'
            + (f'<p class="receipt-copy"><b>How it was computed:</b> '
               f"{formula}</p>" if formula else "")
            + (f'<p class="record-links">{links}</p>' if links else "")
            + "</details>")
    return (
        '<aside class="method-ledger receipts-appendix" id="receipts">'
        "<h2>Receipts</h2>"
        '<p class="source-line">Every figure above links here; each entry '
        "names what was counted and the records it came from.</p>"
        + "".join(blocks) +
        "<style>.receipts-appendix{margin:34px 0}"
        ".receipts-appendix .inline-work{padding:8px 0;"
        "border-bottom:1px solid var(--rule)}"
        ".receipts-appendix .receipt-copy{max-width:920px;"
        "color:var(--ink-soft);font-size:12px;line-height:1.5}"
        ".receipts-appendix .record-links{display:flex;flex-wrap:wrap;"
        "gap:8px 14px}"
        "a.work-trigger,button.work-trigger{color:inherit}"
        "a.work-trigger{text-decoration:none}"
        "@media print{.receipts-appendix .inline-work>*{display:block}}"
        "</style></aside>")


def _matching_brace(css: str, opening: int) -> int:
    """Closing brace for one CSS block, respecting strings and comments."""
    depth, i, quote = 1, opening + 1, ""
    while i < len(css):
        if not quote and css.startswith("/*", i):
            end = css.find("*/", i + 2)
            if end < 0:
                return -1
            i = end + 2
            continue
        char = css[i]
        if quote:
            if char == "\\":
                i += 2
                continue
            if char == quote:
                quote = ""
        elif char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _selector_parts(header: str) -> list[str]:
    """Split a selector list without treating commas in :has() as breaks."""
    out, start, depth, quote = [], 0, 0, ""
    for i, char in enumerate(header):
        if quote:
            if char == quote and (i == 0 or header[i - 1] != "\\"):
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            out.append(header[start:i])
            start = i + 1
    out.append(header[start:])
    return out


def _scrub_studio_css(css: str) -> str:
    """Remove complete studio-only selectors without damaging CSS blocks.

    The previous line filter removed a selector line but left its declaration
    block behind.  Browsers then discarded an unpredictable part of the
    client stylesheet.  This small parser works at rule boundaries, recurses
    into media blocks, and preserves non-studio siblings in selector lists.
    """
    out, cursor = [], 0
    while cursor < len(css):
        opening = css.find("{", cursor)
        if opening < 0:
            out.append(css[cursor:])
            break
        # A declaration terminator before the next opening brace belongs to
        # an at-rule without a block; preserve it and keep scanning.
        semi = css.find(";", cursor, opening)
        if semi >= 0:
            out.append(css[cursor:semi + 1])
            cursor = semi + 1
            continue
        closing = _matching_brace(css, opening)
        if closing < 0:
            # Keep malformed input intact; the balance gate below will name
            # the failure instead of this function making it worse.
            out.append(css[cursor:])
            break
        raw_header = css[cursor:opening]
        block = css[opening + 1:closing]
        header = raw_header.strip()
        prefix = raw_header[:len(raw_header) - len(raw_header.lstrip())]
        if header.startswith(("@media", "@supports", "@layer", "@container")):
            out.append(f"{raw_header}{{{_scrub_studio_css(block)}}}")
        elif header.startswith("@"):
            out.append(f"{raw_header}{{{block}}}")
        else:
            keep = [part.strip() for part in _selector_parts(header)
                    if part.strip() and not _STUDIO_SELECTOR.search(part)]
            if keep:
                out.append(f"{prefix}{', '.join(keep)} {{{block}}}")
        cursor = closing + 1
    return "".join(out)


def _css_balanced(css: str) -> bool:
    """True when every CSS brace closes outside strings and comments."""
    depth, i, quote = 0, 0, ""
    while i < len(css):
        if not quote and css.startswith("/*", i):
            end = css.find("*/", i + 2)
            if end < 0:
                return False
            i = end + 2
            continue
        char = css[i]
        if quote:
            if char == "\\":
                i += 2
                continue
            if char == quote:
                quote = ""
        elif char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0 and not quote


def _client_css_violations(html: str) -> list[dict]:
    styles = re.findall(r"<style[^>]*>(.*?)</style>", html or "", flags=re.S)
    if not styles:
        return [{"rule": "client_css_missing",
                 "detail": "client export contains no embedded stylesheet"}]
    return [
        {"rule": "client_css_unbalanced",
         "detail": f"client stylesheet {index} has unbalanced CSS blocks"}
        for index, css in enumerate(styles, start=1) if not _css_balanced(css)
    ]


def hydrate_market_map_client_mark(*, client: str, slug: str, root: Path,
                                   log: Callable[[str], None] = print) -> str:
    """Best-effort live-press preflight for the current client's own logo.

    This is deliberately *outside* :func:`press_market_map`: saved-input
    replays remain zero-network and byte-deterministic.  A live Golden Press
    may call this once when intake supplied a website.  The fetcher writes the
    operator-managed local cache, and the renderer subsequently consumes that
    cache like every other identity asset.
    """
    from agents.reports.report_assets import clear_caches, client_logo

    display = " ".join(str(client or "").split())
    if client_logo(display):
        return "cached"
    intake = root / "data" / "intake" / f"{slug}.submission.json"
    try:
        payload = json.loads(intake.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "fallback"
    if not str(payload.get("website") or "").strip():
        return "fallback"
    try:
        from tools.brand_marks import prefetch_client_logo
        fetched = prefetch_client_logo(display)
    except Exception as exc:  # noqa: BLE001 - identity fallback is designed
        log(f"market map client mark fetch failed ({type(exc).__name__}); "
            "the explicit monogram fallback will render")
        return "fallback"
    if not fetched:
        return "fallback"
    clear_caches()
    return "fetched" if client_logo(display) else "fallback"


def _client_export(html: str, receipts: Any = None) -> str:
    """The client build: the studio stripped, plus working receipts.

    Removal first (scripts, toolbar, editing attributes), then the ONE
    addition: the receipts appendix above, plus former work-trigger buttons
    becoming in-page anchors to their entries. Facts never change; the
    delivery mechanism changes from runtime to native HTML.
    """
    out = html
    # BEFORE attribute stripping: triggers become anchors to their receipt.
    if receipts:
        def _to_anchor(match: Any) -> str:
            attrs, label = match.group(1), match.group(2)
            key_m = re.search(r'data-work="([^"]+)"', attrs)
            if not key_m:
                return match.group(0)
            if key_m.group(1) not in receipts:
                # Leave the invalid runtime-only control visible so the
                # client visual contract rejects the export.  Silently
                # converting it to prose would hide missing evidence.
                return match.group(0)
            attrs = re.sub(r'\s+type="button"', "", attrs, flags=re.I)
            return (f'<a{attrs} href="#receipt-{key_m.group(1)}">'
                    f"{label}</a>")
        # Any control carrying data-work is a drawer trigger, even when a
        # renderer-specific class (such as edition-meta) supplies its look.
        # The script-free client build must convert all of them, not just
        # controls that happen to carry the generic work-trigger class.
        out = re.sub(r"<button([^>]*)>(.*?)</button>",
                     _to_anchor, out, flags=re.S)
        # The studio opens the drawer; the client build opens the native
        # receipt appendix. A stripped-runtime button is a visible failure.
        out = re.sub(
            r'<button([^>]*class="[^"]*show-work-link[^"]*"[^>]*)>'
            r'(.*?)</button>',
            r'<a class="show-work-link" href="#receipts">\2</a>', out,
            flags=re.S | re.I)
    out = re.sub(r"<script[^>]*>.*?</script>", "", out, flags=re.S | re.I)
    out = re.sub(r"<(nav|div|header)[^>]*class=\"[^\"]*studio-toolbar"
                 r"[^\"]*\"[^>]*>.*?</\1>", "", out, flags=re.S | re.I)
    out = re.sub(r'<div[^>]*class="[^"]*drawer-scrim[^"]*"[^>]*>'
                 r'.*?</aside>\s*</div>', "", out, flags=re.S | re.I)
    out = re.sub(r"\s(?:contenteditable|data-edit-id|data-action|"
                 r"data-work|data-resizable-text|data-edit-link|"
                 r"data-logo-slot-id|data-required-logo)=\"[^\"]*\"", "",
                 out)
    # The editing tooltips are studio UI, not content: 65 of them shipped
    # to a client in round 1 ("Edit mode: double-click to change this URL").
    out = re.sub(r"\stitle=\"Edit mode:[^\"]*\"", "", out)

    # The stylesheet's studio rules go too: a selector like
    # `.is-editing [data-edit-id]` is inert without the runtime, but a
    # client build should not even whisper about an editing mode.
    def _scrub_css(match: Any) -> str:
        return f"{match.group(1)}{_scrub_studio_css(match.group(2))}</style>"

    out = re.sub(r"(<style[^>]*>)(.*?)</style>", _scrub_css, out,
                 flags=re.S | re.I)
    appendix = _receipts_appendix(receipts)
    if appendix and "</main>" in out:
        out = out.replace("</main>", appendix + "</main>", 1)
    elif appendix:
        out = out.replace("</body>", appendix + "</body>", 1)
    return out


def press_market_map(
    *,
    pack: Any,
    client: str,
    slug: str,
    root: Path,
    out_dir: Path,
    stamp: str,
    profile: Optional[dict] = None,
    inputs: Optional[dict] = None,
    log: Callable[[str], None] = print,
) -> dict:
    """Project, render, validate, write. Pure consumption of a built pack.

    `inputs` is the captured store state (see `capture_inputs`). Omitted on
    a live press, where the capture happens here and is written beside the
    document; supplied on replay, where the sidecar's bytes are the store.
    """
    from agents.golden_press.market_map_projection import (
        build_market_map, capture_inputs,
    )
    from agents.golden_press.market_map_render import (
        SECTIONS, coverage_items, render_sections, ticker_items, work_details,
    )
    from agents.golden_press.market_map_skeleton import EditIds, build_document
    from agents.golden_press.market_map_validate import validate_market_map
    from agents.golden_press.style_contract import unstyled_in_context
    from agents.golden_press.client_visual_contract import (
        validate_client_visual_contract,
    )
    from tools.artifacts import atomic_write_json

    profile = profile or {}
    if inputs is None:
        inputs = capture_inputs(profile=profile, slug=slug, pack=pack)
    elif "rival_footprint" not in inputs:
        # A sidecar captured before the rival lane joined the capture must
        # not silently trigger a live read inside the projection: the
        # backfill happens HERE, once, and is then part of the inputs that
        # the coverage receipt and the re-written sidecar both describe.
        from agents.golden_press.rival_footprint import load_cached
        inputs = dict(inputs)
        inputs["rival_footprint"] = load_cached(slug) if slug else {}
    doc = build_market_map(pack, profile=profile, slug=slug, as_of=stamp,
                           inputs=inputs)
    ids = EditIds()
    display = " ".join(str(pack.client_name or client).split())
    html = build_document(
        client_name=display, slug=slug, stamp=stamp,
        title="Federal Market Map",
        standfirst=("What we know. What it proves. What is ready to pursue. "
                    "What research comes next."),
        edition=f"{display} · All Federal",
        edition_note=(f"Research pressed {stamp}. Each amount and identifier "
                      "opens its receipt."),
        coverage=coverage_items(doc), sections=SECTIONS,
        content=render_sections(doc, ids),
        ticker_items=ticker_items(doc),
        work_details=work_details(doc), ids=ids)

    # TWO BUILDS, TWO RULE SETS. The STUDIO document legitimately carries
    # scripts and editable fields; validating it with the client-artifact
    # rules failed every press for having its own runtime. The CLIENT build
    # is the studio stripped of scripts, editing attributes and the toolbar,
    # and THAT is what the client-artifact rules judge. Structure, density,
    # receipts and the banned-string lint hold for both by construction,
    # because the client build is derived from the studio by removal only.
    client_html = _client_export(html, receipts=work_details(doc))
    verdict = validate_market_map(client_html)
    violations = list(verdict.get("violations") or [])
    visual = validate_client_visual_contract(client_html, client_name=display)
    violations.extend(visual.get("violations") or [])
    violations.extend(_client_css_violations(client_html))
    unstyled = unstyled_in_context(html)
    if unstyled:
        violations.append({
            "rule": "unstyled_classes",
            "detail": f"classes with no stylesheet rule: "
                      f"{sorted({c for c, _ in unstyled})[:8]}"})
    client_unstyled = unstyled_in_context(client_html)
    if client_unstyled:
        violations.append({
            "rule": "client_unstyled_classes",
            "detail": f"client classes with no stylesheet rule: "
                      f"{sorted({c for c, _ in client_unstyled})[:8]}"})
    violations.extend(_lint(html))
    certified = not violations

    stem = f"{slug}.market_map"
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{stem}.html"
    html_path.write_text(html, encoding="utf-8")
    client_path = out_dir / f"{stem}.client.html"
    client_path.write_text(client_html, encoding="utf-8")
    atomic_write_json(out_dir / f"{stem}.validation.json", {
        "version": MARKET_MAP_PRESS_VERSION, "ok": certified,
        "press_date": stamp, "violations": violations,
        "receipts": verdict.get("receipts"),
        "client_visual_contract": visual,
    })
    atomic_write_json(out_dir / f"{stem}.inputs.json", inputs)
    # THE COVERAGE RECEIPT (gauntlet requirement): what was searched, what
    # was inspected, what was accepted, what was rejected AND WHY, how fresh
    # the stores were, and the as-of stamp. The defensible claim is "every
    # relevant record within the declared universe was evaluated and
    # dispositioned", and this file is that claim's evidence.
    receipts = doc.receipts or {}
    lane = receipts.get("pack_lane") or {}
    store_candidates = inputs.get("store_candidates") or {}
    atomic_write_json(out_dir / f"{stem}.coverage.json", {
        "version": MARKET_MAP_PRESS_VERSION,
        "as_of": stamp,
        "declared_sources": {
            "notice_store": receipts.get("notice_store") or {},
            "usaspending_rival_cache": {
                "rivals_measured": len(inputs.get("rival_footprint") or {})},
            "targets_store": {
                "present": bool(inputs.get("targets_payload"))},
            "evidence_pack_lanes": lane.get("screened", 0),
        },
        "query_plan": {
            "terms_searched": len(store_candidates),
            "candidate_rows_inspected": sum(
                len(v) for v in store_candidates.values()),
        },
        "dispositions": {
            "operator_rulings": receipts.get("operator_rulings") or {},
            "qualified": receipts.get("opportunities", 0),
            "pack_rows_screened": lane.get("screened", 0),
            "pack_rows_rejected": lane.get("rejected", 0),
            "rejected_rows": lane.get("rejected_rows") or [],
            "families_raw": receipts.get("families_raw", 0),
            "families_collapsed": receipts.get("families_collapsed", 0),
            "forecasts_screened": lane.get("forecasts_screened", 0),
            "forecasts_kept": lane.get("forecasts_kept", 0),
        },
        "blind_spots": [
            order.what for order in (doc.research_demand or ())
        ][:20],
        "model_calls": receipts.get("model_calls", 0),
    })
    atomic_write_json(out_dir / f"{stem}.document.json", {
        "version": MARKET_MAP_PRESS_VERSION,
        "as_of": stamp, "slug": slug,
        "sections": [s["id"] if isinstance(s, dict) else s for s in SECTIONS],
        "opportunities": len(doc.qualified_opportunities),
        "contacts": len(doc.contact_actions),
        "events": len(doc.events),
        "competitors": len(doc.competitive_position.competitors),
        "edit_ids": ids.count if hasattr(ids, "count") else None,
    })
    if not certified:
        failed = out_dir / f"{stem}.FAILED.html"
        failed.write_text(html, encoding="utf-8")
        log(f"market map: {len(violations)} violation(s); FAILED copy "
            f"parked at {failed}; NOT delivered "
            f"({'; '.join(sorted({v['rule'] for v in violations}))[:200]})")
    else:
        log(f"market map: certified clean; {html_path}")

    return {"html_path": str(html_path), "client_path": str(client_path),
            "inputs_path": str(out_dir / f"{stem}.inputs.json"),
            "certified": certified,
            "violations": violations, "stem": stem,
            "client_visual_contract": visual,
            "opportunities": len(doc.qualified_opportunities),
            "contacts": len(doc.contact_actions)}


def deliver(result: dict, *, client: str, log: Callable[[str], None],
            deliver_fn: Callable[..., Any]) -> Optional[str]:
    """Desktop promotion under the same fail-closed law as the golden press."""
    return deliver_fn(Path(result["html_path"]), client=client,
                      certified=result["certified"],
                      label="Federal Market Map", log=log)
