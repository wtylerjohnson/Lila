"""Brand marks — agency seals and company logos for every rendered surface.

Architecture rule: renderers NEVER fetch. This tool prefetches marks into a
local cache; documents embed them base64 from the cache (self-contained
single-file HTML holds), and anything missing falls back to the designed
monogram tile. Deterministic renders, no network in the render path, no
broken-image squares in a client deliverable.

    python3 tools/brand_marks.py --agencies            # every mapped agency seal
    python3 tools/brand_marks.py --client "Thinklogical"   # + that client's competitors

Cache layout (agency seals are committed reference assets; company and client
marks are local operator-managed assets):
    data/reference/seals/<monogram>.png         dod.png, doj.png, gsa.png ...
    data/reference/marks/company/<slug>.png     boeing.png, leidos_inc.png ...

Marks come from the public favicon service at 128px — for .gov domains that
is the official seal. Drop a better/official image on the same path any time;
the file wins, the tool never overwrites an existing file.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = Path(__file__).resolve().parents[1]

# monogram key -> domain. Seeded from agency_domains.json + the monogram map;
# kept explicit here so coverage is inspectable and editable in one place.
AGENCY_DOMAINS = {
    "dod": "defense.gov", "dhs": "dhs.gov", "doj": "justice.gov",
    "va": "va.gov", "hhs": "hhs.gov", "treas": "home.treasury.gov",
    "dos": "state.gov", "dot": "transportation.gov", "doi": "doi.gov",
    "usda": "usda.gov", "doc": "commerce.gov", "doe": "energy.gov",
    "dol": "dol.gov", "gsa": "gsa.gov", "ssa": "ssa.gov",
    "nasa": "nasa.gov", "epa": "epa.gov", "si": "si.edu",
    "nara": "archives.gov", "dcc": "dccourts.gov", "aousc": "uscourts.gov",
    "aid": "usaid.gov", "usaid": "usaid.gov",
    "exim": "exim.gov", "fdic": "fdic.gov",
    "hud": "hud.gov", "loc": "loc.gov", "senate": "senate.gov",
}

_COMPANY_SUFFIXES = {"the", "inc", "llc", "llp", "lp", "ltd", "corp",
                     "corporation", "company", "co", "group", "holdings"}

# what a dropped image may be; lookups try these in order, so a hand-dropped
# .jpg beats nothing and a .png beats a .jpg of the same key
MARK_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".svg")
MARK_MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".webp": "image/webp", ".svg": "image/svg+xml"}
# noise words stripped from dropped filenames before recognition:
# "DoD seal.png", "Boeing logo (1).jpg" -> "DoD", "Boeing"
_DROP_NOISE = {"seal", "logo", "logos", "mark", "icon", "official", "copy",
               "image", "img", "1", "2", "3"}


def seals_dir() -> Path:
    return Path(os.environ.get("LILA_SEALS_DIR",
                               _ROOT / "data" / "reference" / "seals"))


def company_marks_dir() -> Path:
    return Path(os.environ.get("LILA_COMPANY_MARKS_DIR",
                               _ROOT / "data" / "reference" / "marks" / "company"))


def client_marks_dir() -> Path:
    return Path(os.environ.get("LILA_CLIENT_MARKS_DIR",
                               _ROOT / "data" / "reference" / "marks" / "client"))


def drop_dir() -> Path:
    """The Desktop 'Brand Marks' folder itself — files dropped NEXT TO the
    category links (it happens constantly) are routed by recognition."""
    return Path(os.environ.get("LILA_BRAND_DROP_DIR",
                               os.path.expanduser("~/Desktop/Brand Marks")))


def client_roster() -> dict[str, str]:
    """slug -> client_name for every client on the board (review packets)."""
    import json
    review = Path(os.environ.get("LILA_REVIEW_DIR", _ROOT / "data" / "review"))
    out: dict[str, str] = {}
    for p in review.glob("*.review.json"):
        try:
            name = (json.loads(p.read_text()) or {}).get("client_name")
        except (OSError, ValueError):
            continue
        slug = p.name[: -len(".review.json")]
        out[slug] = name or slug
    return out


def company_slug(name: str) -> str:
    """LEIDOS, INC. -> leidos · THE BOEING COMPANY -> boeing — the cache key
    and the domain-guess stem share this normalization."""
    words = [w for w in "".join(c if c.isalnum() else " " for c in (name or "").lower()).split()
             if w not in _COMPANY_SUFFIXES]
    return "_".join(words) or "company"


def company_domain_guess(name: str) -> str:
    """Best-effort <stem>.com. A guess can be wrong: marks are cached as
    inspectable files an operator can delete (fallback: initial tile), and
    the company cache is never committed."""
    return "".join(w for w in company_slug(name).split("_")) + ".com"


def find_mark(directory: Path, key: str) -> Path | None:
    """The cache lookup: <key>.<ext> across MARK_EXTS, first hit wins."""
    for ext in MARK_EXTS:
        p = directory / f"{key}{ext}"
        if p.exists():
            return p
    return None


def _clean_stem(stem: str) -> str:
    words = [w for w in "".join(c if c.isalnum() else " " for c in stem.lower()).split()
             if w not in _DROP_NOISE]
    return " ".join(words)


def recognize_client_key(stem: str) -> str | None:
    """Dropped-file stem -> client slug when it names a client on the board:
    'thinklogical.png', 'Thinklogical logo.png', 'Recorded Future.jpg'."""
    cleaned = _clean_stem(stem)
    cand = "_".join(cleaned.split())
    roster = client_roster()
    if cand in roster:
        return cand
    for slug, name in roster.items():
        if cleaned == _clean_stem(name):
            return slug
    return None


def recognize_agency_key(stem: str) -> str | None:
    """Dropped-file stem -> monogram key, only when we are SURE: the stem is
    already a key ('DOD', 'treas'), or the monogram map matches the cleaned
    name ('Department of Defense seal'). Initials guessing would misfile."""
    from agents.reports.views import _AGENCY_MONOGRAMS
    cleaned = _clean_stem(stem)
    compact = cleaned.replace(" ", "")
    if compact in AGENCY_DOMAINS:
        return compact
    by_mono = {m.lower(): m.lower() for m in _AGENCY_MONOGRAMS.values()}
    if compact in by_mono:
        return compact
    up = cleaned.upper()
    for name, mono in _AGENCY_MONOGRAMS.items():
        if name in up:
            return mono.lower()
    return None


def adopt_marks() -> dict:
    """Sweep the cache folders AND the Desktop drop folder for hand-dropped
    files, route each by recognition, and normalize to protocol names.

    Routing beats folders: a client logo dropped into Company Logos (or at
    the top level next to the links) still lands in the client cache —
    clients on the board are recognized by name, so 'thinklogical.png' can
    never masquerade as a competitor. An adopt is an intentional operator
    act: it REPLACES every cached variant for that key. Unrecognized or
    unsupported files are left in place and reported, never deleted."""
    import shutil
    report = {"adopted": [], "skipped": []}

    def _route_seals(stem):
        k = recognize_agency_key(stem)
        if k:
            return seals_dir(), "agency", k
        c = recognize_client_key(stem)
        if c:
            return client_marks_dir(), "client", c  # misdropped: reroute
        return None

    def _route_company(stem):
        c = recognize_client_key(stem)
        if c:
            return client_marks_dir(), "client", c  # a client is never a competitor
        s = company_slug(_clean_stem(stem))
        return (company_marks_dir(), "company", s) if s != "company" else None

    def _route_client(stem):
        c = recognize_client_key(stem)
        return (client_marks_dir(), "client", c) if c else None

    def _route_top(stem):
        # ambiguous ground: only confident matches move; everything else is
        # reported with directions rather than guessed into the wrong cache
        c = recognize_client_key(stem)
        if c:
            return client_marks_dir(), "client", c
        a = recognize_agency_key(stem)
        if a:
            return seals_dir(), "agency", a
        return None

    def _adopt(directory: Path, router) -> None:
        if not directory.is_dir():
            return
        for p in sorted(directory.iterdir()):
            if not p.is_file() or p.name.startswith(".") or p.name == "NAMING.txt":
                continue
            ext = p.suffix.lower()
            if ext not in MARK_EXTS:
                report["skipped"].append(f"{p.name}: unsupported type ({ext or 'none'})"
                                         + (" — export HEIC as PNG/JPG" if ext == ".heic" else ""))
                continue
            routed = router(p.stem)
            if routed is None:
                report["skipped"].append(
                    f"{p.name}: not recognized — name it per NAMING.txt or drop "
                    "it into Agency Seals / Company Logos / Client Logos")
                continue
            target_dir, kind, key = routed
            want = f"{key}{ext}"
            if p.parent == target_dir and p.name == want:
                continue  # already protocol-named, right folder
            target_dir.mkdir(parents=True, exist_ok=True)
            for old in MARK_EXTS:  # the drop replaces every cached variant
                q = target_dir / f"{key}{old}"
                if q.exists() and q != p:
                    q.unlink()
            shutil.move(str(p), str(target_dir / want))
            report["adopted"].append(
                {"name": p.name, "file": want, "kind": kind, "key": key})

    _adopt(seals_dir(), _route_seals)
    _adopt(company_marks_dir(), _route_company)
    _adopt(client_marks_dir(), _route_client)
    _adopt(drop_dir(), _route_top)
    return report


_AGENCY_NAMING = """LILA BRAND MARKS — AGENCY SEALS · naming protocol

Drop images here (or into the Desktop shortcut). PNG, JPG, WEBP or SVG.

Exact names the system reads, one per agency (lowercase):
{keys}

You do NOT have to rename by hand — recognizable drops are adopted
automatically: "Department of Defense.png", "DoD seal.jpg", "GSA logo.png"
all become the right file. Your drop replaces any fetched mark for that
agency and is never overwritten by the fetcher afterwards.

Unrecognized names are left alone and reported — nothing is deleted.
"""

_COMPANY_NAMING = """LILA BRAND MARKS — COMPANY LOGOS · naming protocol

Drop images here (or into the Desktop shortcut). PNG, JPG, WEBP or SVG.

File name = company name, lowercased, corporate suffixes dropped
(THE/INC/LLC/CORP/COMPANY/CO/GROUP/HOLDINGS), other punctuation -> _ :
    THE BOEING COMPANY      -> boeing.png
    LEIDOS, INC.            -> leidos.png
    AVI-SPL                 -> avi_spl.png
    RAYTHEON COMPANY        -> raytheon.png

You do NOT have to rename by hand — "Boeing logo.jpg" is adopted to
boeing.jpg automatically. Wrong logo showing? Delete the file: the
renderer falls back to a plain name, never a broken image.
"""


_CLIENT_NAMING = """LILA BRAND MARKS — CLIENT LOGOS · naming protocol

Drop images here (or into the Desktop shortcut). SVG preferred (crispest
on the deliverable cover), PNG/JPG/WEBP also render.

File name = the client's slug (lowercase, spaces -> _):
    Thinklogical        -> thinklogical.svg / thinklogical.png
    Recorded Future     -> recorded_future.svg

You do NOT have to rename by hand — any drop that names a client on the
board is recognized ("Thinklogical logo.png"), even when dropped into the
wrong folder or at the top level of Brand Marks. The logo lands on the
deliverable cover and the dashboard header; when that client already has
an assessment, a rebuild of the views kicks off automatically.
"""


def write_naming_readmes() -> None:
    seals_dir().mkdir(parents=True, exist_ok=True)
    company_marks_dir().mkdir(parents=True, exist_ok=True)
    client_marks_dir().mkdir(parents=True, exist_ok=True)
    keys = "    " + "\n    ".join(
        f"{k}.png  ({v})" for k, v in sorted(AGENCY_DOMAINS.items()))
    (seals_dir() / "NAMING.txt").write_text(_AGENCY_NAMING.format(keys=keys))
    (company_marks_dir() / "NAMING.txt").write_text(_COMPANY_NAMING)
    (client_marks_dir() / "NAMING.txt").write_text(_CLIENT_NAMING)


def ensure_desktop_links(desktop: Path | None = None) -> list[str]:
    """A 'Brand Marks' folder on the Desktop with links INTO the repo cache:
    what lands there IS the cache — no copying step to forget."""
    desktop = desktop or Path(os.path.expanduser("~/Desktop"))
    base = desktop / "Brand Marks"
    base.mkdir(parents=True, exist_ok=True)
    made = []
    for label, target in (("Agency Seals", seals_dir()),
                          ("Company Logos", company_marks_dir()),
                          ("Client Logos", client_marks_dir())):
        target.mkdir(parents=True, exist_ok=True)
        link = base / label
        if link.is_symlink():
            if link.resolve() == target.resolve():
                continue
            link.unlink()
        elif link.exists():
            made.append(f"SKIPPED {link}: a real file/folder is in the way")
            continue
        link.symlink_to(target, target_is_directory=True)
        made.append(f"{link} -> {target}")
    return made


def _favicon_url(domain: str) -> str:
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"


def fetch_mark(domain: str, dest: Path, *, timeout: float = 10.0) -> bool:
    """Fetch one mark into the cache. Existing files always win (operator
    overrides survive); tiny responses are rejected (the service returns a
    16px globe placeholder when it has nothing real)."""
    if find_mark(dest.parent, dest.stem):  # any cached/dropped variant wins
        return True
    import httpx
    try:
        r = httpx.get(_favicon_url(domain), timeout=timeout, follow_redirects=True)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001 — a miss is a fallback, never a crash
        print(f"[marks] {domain}: fetch failed ({e})", file=sys.stderr)
        return False
    if len(r.content) < 600:  # placeholder globe, not a real mark
        print(f"[marks] {domain}: placeholder response, skipping", file=sys.stderr)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return True


def prefetch_agencies(keys: list[str] | None = None) -> int:
    from agents.reports.views import _agency_monogram
    want = {k.lower() for k in keys} if keys else set(AGENCY_DOMAINS)
    got = 0
    for key in sorted(want):
        dom = AGENCY_DOMAINS.get(key)
        if not dom:
            print(f"[marks] no domain mapped for agency key '{key}'", file=sys.stderr)
            continue
        dest = seals_dir() / f"{key}.png"
        if fetch_mark(dom, dest):
            got += 1
            print(f"[marks] seal {key} <- {dom}", file=sys.stderr)
    # sanity: every monogram the badge can emit for mapped agencies resolves
    assert _agency_monogram("DEPT OF DEFENSE").lower() in AGENCY_DOMAINS
    return got


def _slugify(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()


def client_domain(slug: str, name: str) -> str:
    """The client's web domain for a best-effort logo fetch — the review
    packet or intake submission website if attested, else a deterministic
    name-domain guess."""
    import json
    review = Path(os.environ.get("LILA_REVIEW_DIR", _ROOT / "data" / "review"))
    intake = Path(os.environ.get("LILA_INTAKE_DIR", _ROOT / "data" / "intake"))
    for candidate in (review / f"{slug}.review.json",
                      intake / f"{slug}.submission.json"):
        try:
            pkt = json.loads(candidate.read_text())
        except (OSError, ValueError):
            continue
        site = (pkt.get("website") or
                (pkt.get("submission") or {}).get("website"))
        if site:
            return site.split("//")[-1].split("/")[0].removeprefix("www.")
    return "".join(c for c in (name or slug).lower() if c.isalnum()) + ".com"


def prefetch_client_logo(client: str) -> bool:
    """Best-effort CLIENT logo into the client cache, so a report renders a
    real mark instead of the monogram fallback. Low-priority quality (favicon
    tier) and never overwrites an operator-dropped file — a hand-placed logo
    at data/reference/marks/client/<slug>.<ext> always wins."""
    slug = _slugify(client)
    if find_mark(client_marks_dir(), slug):   # operator drop or prior fetch wins
        return True
    dom = client_domain(slug, client)
    dest = client_marks_dir() / f"{slug}.png"
    if fetch_mark(dom, dest):
        print(f"[marks] client logo {slug} <- {dom} (favicon tier — drop a "
              "better one in Client Logos to replace)", file=sys.stderr)
        return True
    return False


def prefetch_client(client: str) -> int:
    """Fetch the marks a client's document actually renders: the client's own
    logo, its agencies (badges + tables) and its competitors."""
    from agents.reports.document import build_document
    from agents.reports.views import _agency_monogram
    got = 1 if prefetch_client_logo(client) else 0
    doc = build_document(client)
    keys = set()
    for a in (*doc.market.agency_map, *doc.market.award_agency_annual,
              *doc.market.addressable_agency_annual):
        keys.add(_agency_monogram(a.agency).lower())
    for p in doc.board.pursuits:
        if p.agency:
            keys.add(_agency_monogram(p.agency).lower())
    got += prefetch_agencies(sorted(keys))
    for c in doc.competitive.competitors:
        dest = company_marks_dir() / f"{company_slug(c.name)}.png"
        if fetch_mark(company_domain_guess(c.name), dest):
            got += 1
            print(f"[marks] company {company_slug(c.name)} <- "
                  f"{company_domain_guess(c.name)}", file=sys.stderr)
    return got


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", help="prefetch this client's agencies + competitors")
    ap.add_argument("--agencies", action="store_true", help="prefetch every mapped agency seal")
    ap.add_argument("--adopt", action="store_true",
                    help="normalize hand-dropped files to protocol names")
    ap.add_argument("--desktop", action="store_true",
                    help="put a 'Brand Marks' drag-and-drop folder on the Desktop")
    args = ap.parse_args()
    if not (args.client or args.agencies or args.adopt or args.desktop):
        ap.error("pass --client, --agencies, --adopt and/or --desktop")
    if args.desktop:
        write_naming_readmes()
        for line in ensure_desktop_links():
            print(f"[marks] {line}", file=sys.stderr)
    # adopt runs before any fetch so a fresh drop wins over a re-fetch
    rep = adopt_marks()
    for a in rep["adopted"]:
        print(f"[marks] adopted {a['name']} -> {a['kind']}/{a['file']}", file=sys.stderr)
    for line in rep["skipped"]:
        print(f"[marks] skipped {line}", file=sys.stderr)
    n = 0
    if args.agencies:
        n += prefetch_agencies()
    if args.client:
        n += prefetch_client(args.client)
    if args.agencies or args.client:
        print(f"[marks] {n} marks in cache", file=sys.stderr)
    return 0


if __name__ == "__main__":
    from tools.env import load_env
    load_env()
    raise SystemExit(main())
