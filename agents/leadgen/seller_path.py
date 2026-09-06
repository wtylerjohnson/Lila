"""SellerTransactionPath: how this seller can transact.

Reuses Assess ``PartnerDirection`` literals. Path is not a contact.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from ._base import SCHEMA_VERSION, _FrozenContract
from .enums import SellerPathKind


class SellerTransactionPath(_FrozenContract):
    """One seller transaction path. Fail-closed unknown yields HOLD, not prime."""

    schema_version: Literal["leadgen.contracts.v1"] = SCHEMA_VERSION
    path_id: str = Field(min_length=1)
    kind: SellerPathKind
    holder: Optional[str] = None
    vehicle: Optional[str] = None
    dossier_cite: Optional[str] = None
    prime_posture_cite: Optional[str] = None
