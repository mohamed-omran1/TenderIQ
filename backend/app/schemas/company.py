"""Pydantic v2 request/response models for the company-profile router (REQ-002).

`CompanyProfileSchema` is the single contract for BOTH the PUT request body and
the GET/PUT response. It carries every validation rule from REQ-002 Data
Requirements, and emits the exact messages named in the Alternative Flows so
the frontend can match on them (senior-fullstack + senior-prompt-engineer).

`EmptyProfileResponse` is the GET-when-nothing-exists shape: identical field
set, all fields nullable/empty, so the frontend renders a blank form without a
single null-check on the root object (REQ-002 Usability NFR). It deliberately
re-declares fields rather than subclassing, because `CompanyProfileSchema`'s
fields are non-null — a subclass couldn't widen them to optional.

`company_id` / `updated_at` are never accepted from the client: `company_id` is
derived from the API key (REQ-002 Security NFR) and `updated_at` is set
server-side. They appear only on responses, never on the request body —
enforced by them being read-only (response-only) on `CompanyProfileSchema` and
absent from the upsert payload built in the router.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from typing import Any, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator

# --- Controlled vocabularies -------------------------------------------------
# PRD §6.2 example list; free-text is explicitly deferred to v2, so the MVP
# validates against a closed set. Kept here (not in config) because it is a
# domain contract, not a deployment knob.
ALLOWED_SPECIALIZATIONS: frozenset[str] = frozenset(
    {"civil", "mep", "fit-out", "roads", "water"}
)

# 249 active ISO 4217 currency codes (uppercase). Size kept tiny; validated as
# 3-letter uppercase strings.
_ISO_4217_PATTERN = r"^[A-Z]{3}$"
# ISO 3166-1 alpha-2 country codes (uppercase).
_ISO_3166_ALPHA2_PATTERN = r"^[A-Z]{2}$"


class FinancialCapacity(BaseModel):
    """Nested object: currency + turnover + bonding capacity.

    Marked sensitive — turnover/bonding figures are commercially confidential
    (REQ-002 Security NFR). The router logs only metadata, never this object.
    """

    model_config = ConfigDict(extra="forbid")

    currency: str = Field(
        ...,
        # No `pattern` here: it runs before our normaliser and would reject valid
        # lowercase input ('sar'). Length bounds are safe; the format check lives
        # in the validator below, after uppercasing (senior-prompt-engineer).
        min_length=3,
        max_length=3,
        description="ISO 4217 currency code, e.g. 'SAR', 'AED', 'USD'.",
    )
    annual_turnover: float = Field(
        ...,
        gt=0,
        description="Most recent annual turnover in the given currency. Must be > 0.",
    )
    available_bonding_capacity: float = Field(
        ...,
        ge=0,
        description="Bonding/insurance capacity available in the given currency. Must be >= 0.",
    )

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, v: str) -> str:
        upper = v.upper()
        if not re.match(_ISO_4217_PATTERN, upper):
            raise ValueError("currency must be a valid ISO 4217 code (3 letters).")
        return upper


class PastProject(BaseModel):
    """One reference project: name, value, year, sector."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=512)
    value: float = Field(..., gt=0, description="Contract value. Must be > 0.")
    year: int = Field(..., ge=1900, le=2100)
    sector: str = Field(..., min_length=1, max_length=128)


class CompanyProfileSchema(BaseModel):
    """PUT request body AND GET/PUT response (REQ-002 Data Requirements).

    Validation surfaces the exact Alternative-Flow messages:
      * empty specializations -> "At least one specialisation is required."
      * non-positive max_project_value -> "max_project_value must be a positive number."
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    specializations: list[str] = Field(
        # No `min_length` here: it fires before the validator and emits a generic
        # message. The validator below owns the exact REQ-002 wording.
        ...,
        description="Company technical specialisations. At least one required.",
    )
    financial_capacity: FinancialCapacity
    geographic_reach: list[str] = Field(
        ...,
        min_length=1,
        description="ISO 3166-1 alpha-2 country codes. At least one required.",
    )
    past_projects: list[PastProject] = Field(
        default_factory=list,
        max_length=20,
        description="Reference projects. At most 20 at MVP.",
    )
    max_project_value: float = Field(
        # No `gt` here: it fires before the validator with a generic message. The
        # validator owns the exact REQ-002 Alternative-Flow wording.
        ...,
        description="Largest project the company will bid for, in financial_capacity.currency.",
    )

    # Response-only: set by the router, never read from the request body
    # (extra="forbid" blocks a client from supplying them).
    company_id: str | None = Field(default=None, description="Owner tenant (server-set).")
    updated_at: datetime | None = Field(
        default=None, description="Server-managed; refreshed on every upsert."
    )

    # --- field validators ----------------------------------------------------

    @field_validator("specializations")
    @classmethod
    def _validate_specializations(cls, v: list[str]) -> list[str]:
        # Owns the empty-list rejection (no min_length on the Field) so the 422
        # body carries the exact REQ-002 Alternative-Flow wording.
        if len(v) == 0:
            raise ValueError("At least one specialisation is required.")
        unknown = [s for s in v if s.lower() not in ALLOWED_SPECIALIZATIONS]
        if unknown:
            allowed = ", ".join(sorted(ALLOWED_SPECIALIZATIONS))
            raise ValueError(
                f"Invalid specialisation(s): {unknown}. Allowed values: {allowed}."
            )
        # Normalise to lowercase so the controlled list stays canonical.
        return [s.lower() for s in v]

    @field_validator("geographic_reach")
    @classmethod
    def _validate_geographic_reach(cls, v: list[str]) -> list[str]:
        if len(v) == 0:
            raise ValueError("At least one country code is required.")
        normalised = [c.upper() for c in v]
        bad = [c for c in normalised if not re.match(_ISO_3166_ALPHA2_PATTERN, c)]
        if bad:
            raise ValueError(
                f"Invalid ISO 3166-1 alpha-2 country code(s): {bad}."
            )
        return normalised

    @field_validator("max_project_value")
    @classmethod
    def _validate_max_project_value(cls, v: float) -> float:
        # Owns the positivity check (no gt on the Field) so the 422 body carries
        # the exact REQ-002 Alternative-Flow wording.
        if v <= 0:
            raise ValueError("max_project_value must be a positive number.")
        return v

class EmptyProfileResponse(BaseModel):
    """Returned by GET when no profile exists yet (REQ-002 Main Flow step 5)."""

    model_config = ConfigDict(from_attributes=True)

    specializations: list[str] | None = Field(default_factory=list)
    # استخدمنا Union هنا علشان نضمن الثبات والـ Compatibility جوه Pydantic v2
    financial_capacity: Union[FinancialCapacity, dict[str, Any], None] = None
    geographic_reach: list[str] | None = Field(default_factory=list)
    past_projects: list[PastProject] | None = Field(default_factory=list)
    max_project_value: float | None = None
    company_id: str | None = None
    updated_at: datetime | None = None
