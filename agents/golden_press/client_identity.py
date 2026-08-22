"""Client identity: who the client IS, kept distinct from who they fight.

WHY THIS EXISTS (measured 2026-08-06). apexanalytix pressed zero decision
rows against a 330k-notice store, and it read as an empty market. It was
not. The profile filed the client and its own products
(apexanalytix, apexportal, QubitOn, BankPro, SmartVM) under
`named_competitors_and_incumbents`, so `entities.client` came out EMPTY and
every entity resolved as a rival. R1 needs client paper and rival paper; R2
needs a rival inside a client-history agency; R4 needs client-paper
adjacency. With no client side, all three are impossible BY CONSTRUCTION,
and the decision layer silently produced nothing.

riverbed has the same defect (SteelHead, SteelCentral and AppResponse are
Riverbed's own products, filed as competitors). thinklogical is the correct
shape: products and competitors are separate kinds and the client is not in
its own competitor list.

TWO JOBS, BOTH LOUD.

  audit_client_identity   splits a raw competitor list into client, product
                          and competitor, reclassifying the client's own
                          name and anything the operator marks as a product,
                          and RECEIPTING every move. It flags entries it
                          cannot classify mechanically rather than guessing.

  assert_client_resolvable  the guard that would have caught this: an entity
                          set with no client term fails loudly instead of
                          zeroing the decision layer and reading as an empty
                          market.

The client's own NAME is reclassified mechanically and safely. Product names
that do not carry the client's name (QubitOn, BankPro) cannot be told from a
rival by string alone, so they are moved only when the operator has listed
them in `client_products`, and otherwise flagged for an operator decision.
Guessing a product is a rival, or a rival a product, both corrupt the
decision layer; the honest move is to name the ambiguity.
"""

from __future__ import annotations

import re
from typing import Any

CLIENT_IDENTITY_VERSION = "client_identity.v1.2026-08-06"

CLIENT = "client"
PRODUCT = "product"
COMPETITOR = "competitor"


class ClientIdentityError(RuntimeError):
    """The client cannot be told apart from its rivals. Never silent."""


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _tokens(value: Any) -> list:
    return [t for t in _norm(value).split() if t]


def _is_client_name(name: Any, client_name: str) -> bool:
    """Whether an entry is the client itself or a client-name-prefixed form.

    Exact match, or the entry begins with the client's full token sequence
    ("apexanalytix Platform" under client "apexanalytix"). A bare shared word
    is NOT enough: "Global Bank Account Confidence Score" shares no client
    token and must not be swept in.
    """
    cn = _tokens(client_name)
    en = _tokens(name)
    if not cn or not en:
        return False
    if en == cn:
        return True
    return len(en) > len(cn) and en[:len(cn)] == cn


def audit_client_identity(profile: Any) -> dict:
    """Correctly-kinded entities from a raw profile, with a full receipt.

    Reads `client_name`, `named_competitors_and_incumbents` and the optional
    `client_products` list. Returns the entities the retrieval layer should
    use, plus a receipt naming every reclassification and every entry that
    needs an operator decision.
    """
    def _get(key, default=None):
        if isinstance(profile, dict):
            return profile.get(key, default)
        return getattr(profile, key, default)

    client_name = " ".join(str(_get("client_name", "") or "").split())
    raw_competitors = list(_get("named_competitors_and_incumbents", []) or [])
    declared_products = list(_get("client_products", []) or [])
    product_norms = {_norm(p) for p in declared_products}

    entities: list = []
    seen: set = set()

    def _add(kind: str, name: str) -> None:
        key = _norm(name)
        if not key or key in seen:
            return
        seen.add(key)
        entities.append({"kind": kind, "name": " ".join(str(name).split())})

    receipt = {
        "version": CLIENT_IDENTITY_VERSION,
        "client_name": client_name,
        "reclassified_client": [],     # client name found among competitors
        "reclassified_product": [],    # operator-declared product among competitors
        "needs_operator_decision": [], # cannot classify by string alone
        "competitors_kept": [],
    }

    if not client_name:
        raise ClientIdentityError(
            "profile carries no client_name; the client side cannot be "
            "established and every decision rule needs it")
    _add(CLIENT, client_name)

    for name in declared_products:
        _add(PRODUCT, name)

    for name in raw_competitors:
        if _is_client_name(name, client_name):
            # the client itself, mislabeled as its own rival
            if _norm(name) != _norm(client_name):
                _add(PRODUCT, name)
                receipt["reclassified_product"].append(name)
            else:
                receipt["reclassified_client"].append(name)
            continue
        if _norm(name) in product_norms:
            receipt["reclassified_product"].append(name)
            continue                                # already added as product
        # A real rival, OR a client product the operator has not declared.
        # If it looks like it could be either, the operator must say.
        if _looks_like_own_product(name, raw_competitors, client_name):
            receipt["needs_operator_decision"].append({
                "name": name,
                "why": ("listed as a competitor but may be the client's own "
                        "product; confirm before it is searched as a rival")})
        _add(COMPETITOR, name)
        receipt["competitors_kept"].append(name)

    receipt["counts"] = {
        CLIENT: sum(1 for e in entities if e["kind"] == CLIENT),
        PRODUCT: sum(1 for e in entities if e["kind"] == PRODUCT),
        COMPETITOR: sum(1 for e in entities if e["kind"] == COMPETITOR),
    }
    return {"entities": entities, "receipt": receipt}


def _looks_like_own_product(name: Any, competitors: Any,
                            client_name: str) -> bool:
    """A weak heuristic, used ONLY to FLAG, never to reclassify.

    A single-token invented-looking name (QubitOn, SmartVM, BankPro) sitting
    in a list otherwise full of company-shaped names is worth an operator
    glance. This never moves an entry; it only asks the question.
    """
    toks = _tokens(name)
    if not toks or _is_client_name(name, client_name):
        return False
    # A SINGLE fused/camelCase token reads as a product (SmartVM, QubitOn,
    # BankPro). A multi-word name is a company or a product line and cannot
    # be told apart mechanically, so it is never flagged: "Cisco
    # ThousandEyes" and "Broadcom AppNeta" are real rivals, and a false flag
    # on them is noise that trains the operator to ignore the flag.
    words = str(name).split()
    if len(words) != 1:
        return False
    word = words[0]
    return bool(re.search(r"[a-z][A-Z]", word))


def assert_client_resolvable(entities: Any, *, client_name: str = "") -> None:
    """THE GUARD. Fail loudly when no client term exists.

    This is the check whose absence let apexanalytix read as an empty
    market. An entity set with competitors but no client cannot classify a
    single record as client paper, so every decision rule is structurally
    dead. That is a configuration fault, not a finding, and it must stop the
    press rather than produce a confident zero.
    """
    rows = entities
    if isinstance(entities, dict):
        rows = [{"kind": k, "name": n}
                for k, names in (entities.get("entities") or entities).items()
                for n in (names or [])] if all(
            isinstance(v, list) for v in entities.values()) else (
            entities.get("entities") or [])
    kinds = set()
    for row in (rows or []):
        kind = row.get("kind") if isinstance(row, dict) else None
        if kind:
            kinds.add(kind)
    if CLIENT not in kinds:
        raise ClientIdentityError(
            f"no client entity resolved for {client_name or 'this client'}: "
            f"the entity set carries {sorted(kinds) or 'nothing'} but no "
            f"client term. Every decision rule needs to tell client paper "
            f"from rival paper, so this would zero the decision layer and "
            f"read as an empty market. Check that the client and its own "
            f"products are not filed under named_competitors_and_incumbents.")
