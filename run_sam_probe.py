#!/usr/bin/env python3
"""SAM.gov forensic probe — one minimal call, full evidence, no theories.

    python3 run_sam_probe.py

Spends exactly ONE quota call and prints everything the API tells us:
HTTP status, the X-RateLimit-* headers (your ACTUAL daily limit and what's
left — this settles the quota question with data), response time, and the
body. Whatever is making searches fail will name itself here.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()

import httpx  # noqa: E402

import tools.api.sam_quota as sam_quota  # noqa: E402

URL = "https://api.sam.gov/opportunities/v2/search"


def main() -> int:
    key = os.environ.get("SAM_GOV_API_KEY")
    if not key:
        print("SAM_GOV_API_KEY not set in .env — that would explain everything.")
        return 1

    from datetime import date, timedelta
    today = date.today()
    params = {
        "api_key": key,
        "postedFrom": (today - timedelta(days=30)).strftime("%m/%d/%Y"),
        "postedTo": today.strftime("%m/%d/%Y"),
        "ncode": "541512",
        "limit": 1,
        "offset": 0,
    }
    print(f"[probe] one call to {URL} (ncode=541512, last 30 days, limit=1)")
    print(f"[probe] local ledger before: {sam_quota.summary()}")
    sam_quota.note_call("probe")

    from tools.api.sam_gov import SAM_HEADERS

    # ── Step 1: raw TCP reachability per address family. The 2026-07-03 hang
    # was sock.connect() blackholing on an IPv6 address — prove or clear that.
    import socket
    print("\n[probe] TCP connect test, both address families (10s cap each):")
    fam_ok = {}
    for label, fam in (("IPv4", socket.AF_INET), ("IPv6", socket.AF_INET6)):
        try:
            infos = socket.getaddrinfo("api.sam.gov", 443, fam, socket.SOCK_STREAM)
        except socket.gaierror:
            print(f"  {label}: no DNS record")
            fam_ok[label] = None
            continue
        addr = infos[0][4]
        s = socket.socket(fam, socket.SOCK_STREAM)
        s.settimeout(10)
        t = time.monotonic()
        try:
            s.connect(addr)
            print(f"  {label}: CONNECTED to {addr[0]} in {time.monotonic()-t:.1f}s")
            fam_ok[label] = True
        except OSError as e:
            print(f"  {label}: FAILED after {time.monotonic()-t:.1f}s ({e}) — "
                  f"connects on this family hang/blackhole")
            fam_ok[label] = False
        finally:
            s.close()
    if fam_ok.get("IPv4") and fam_ok.get("IPv6") is False:
        print("  ^ SMOKING GUN: IPv6 blackholes while IPv4 works. The system now "
              "forces IPv4 on every API call, so this is fixed in the pipeline.")

    # ── Step 2: the real API call, IPv4-forced like the pipeline now is.
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=90.0,
                          transport=httpx.HTTPTransport(local_address="0.0.0.0"),
                          follow_redirects=True) as c:
            resp = c.get(URL, params=params, headers=SAM_HEADERS)
    except httpx.TransportError as e:
        print(f"\nVERDICT: network-level failure after {time.monotonic()-t0:.1f}s — "
              f"{type(e).__name__}: {e}")
        print("Even IPv4-forced the call failed — SAM's edge is dropping this "
              "network's traffic, or something local blocks it. Retry in a few "
              "minutes; if it persists, try from a different network to isolate.")
        return 1
    dt = time.monotonic() - t0

    print(f"\nHTTP {resp.status_code} in {dt:.1f}s")
    for h in ("x-ratelimit-limit", "x-ratelimit-remaining", "retry-after",
              "content-type", "server"):
        if h in resp.headers:
            print(f"  {h}: {resp.headers[h]}")

    body = (resp.text or "").strip()
    print(f"  body ({len(body)} bytes): {body[:500]}")

    print()
    if resp.status_code == 200:
        try:
            total = resp.json().get("totalRecords")
            print(f"VERDICT: API + key are working. totalRecords={total}. "
                  f"If searches still come back empty, the failure is in OUR call "
                  f"pattern, not the API — rerun the search and read the new "
                  f"self-describing errors.")
        except ValueError:
            print("VERDICT: 200 but non-JSON body — SAM's edge (WAF) served a block "
                  "page. The client needs browser-style headers.")
    elif resp.status_code == 429:
        lim = resp.headers.get("x-ratelimit-limit", "?")
        print(f"VERDICT: rate limited. Your key's real daily limit is {lim} "
              f"(see header above). The pool is permanent; the daily extract "
              f"is the primary path and costs nothing. "
              f"to move to 1,000/day.")
    elif resp.status_code in (401, 403):
        print("VERDICT: key rejected (bad key, expired key, or missing role for "
              "this endpoint). Log into sam.gov -> Account Details -> API Key and "
              "regenerate, then update .env.")
    else:
        print(f"VERDICT: SAM returned {resp.status_code} — read the body above; "
              f"it names the problem.")
    return 0 if resp.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
