"""Operator-locked external product slot contract.

The slot table in ``docs/MARKET_MAP_CONTRACT.md`` is the human-readable
source of truth. This loader makes it executable and binds it to the digest of
the operator's 2026-08-23 ruling. A slot change must therefore be an
explicit contract amendment, not incidental renderer work.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re


_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = _ROOT / "docs" / "MARKET_MAP_CONTRACT.md"
CONTRACT_VERSION = "market-map.v4.2026-08-23"

# Digest of the canonical ``number|id|heading`` slots approved by the operator.
# Do not update this value without a later explicit operator instruction.
OPERATOR_LOCKED_SLOT_SHA256 = (
    "4ab17cc1357708e9d455242f54213fcaae106e351952ddfadd99087ca4db1b0a"
)

_ROW_RE = re.compile(
    r"^\|\s*(?P<number>\d+)\s*\|\s*`(?P<section_id>[a-z][a-z-]*)`\s*\|"
    r"\s*(?P<heading>[^|]+?)\s*\|\s*$"
)


class ExternalProductContractError(RuntimeError):
    """The operator-locked contract is absent, malformed, or changed."""


@dataclass(frozen=True)
class ExternalProductSlot:
    number: int
    slot_id: str
    heading: str


def _canonical_bytes(slots: tuple[ExternalProductSlot, ...]) -> bytes:
    return ("\n".join(
        f"{slot.number}|{slot.slot_id}|{slot.heading}"
        for slot in slots
    ) + "\n").encode("utf-8")


def load_external_product_slots() -> tuple[ExternalProductSlot, ...]:
    """Parse and verify the sole external product's exact slot order."""

    try:
        text = CONTRACT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExternalProductContractError(
            f"external product contract is unreadable: {exc}"
        ) from exc
    table = re.search(
        r"The client slot identities are:\s*(?P<table>.*?)"
        r"\n\nEach canonical",
        text,
        re.S,
    )
    if table is None:
        raise ExternalProductContractError(
            "external product contract has no client slot table"
        )
    rows: list[ExternalProductSlot] = []
    for line in table.group("table").splitlines():
        match = _ROW_RE.match(line.strip())
        if match is None:
            continue
        rows.append(ExternalProductSlot(
            number=int(match.group("number")),
            slot_id=match.group("section_id"),
            heading=" ".join(match.group("heading").split()),
        ))
    slots = tuple(rows)
    if len(slots) != 8:
        raise ExternalProductContractError(
            f"external product contract must define 8 slots, found {len(slots)}"
        )
    numbers = [slot.number for slot in slots]
    if numbers != list(range(1, 9)):
        raise ExternalProductContractError(
            f"external product slots are not numbered 1 through 8: {numbers}"
        )
    ids = [slot.slot_id for slot in slots]
    if len(set(ids)) != len(ids):
        raise ExternalProductContractError(
            f"external product slot ids repeat: {ids}"
        )
    digest = hashlib.sha256(_canonical_bytes(slots)).hexdigest()
    if digest != OPERATOR_LOCKED_SLOT_SHA256:
        raise ExternalProductContractError(
            "external product slot membership, names, or order changed "
            "without an operator-approved lock amendment"
        )
    return slots


def external_product_slot_ids() -> tuple[str, ...]:
    return tuple(
        slot.slot_id for slot in load_external_product_slots()
    )


__all__ = (
    "CONTRACT_PATH",
    "CONTRACT_VERSION",
    "OPERATOR_LOCKED_SLOT_SHA256",
    "ExternalProductContractError",
    "ExternalProductSlot",
    "external_product_slot_ids",
    "load_external_product_slots",
)
