"""Parts and supplier listing request/response bodies."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

Country = Literal["IE", "UK"]


def _required_text(value: str) -> str:
    """Strip surrounding whitespace and refuse blank identity strings, so that 'Acme' and
    'Acme ' are the same part and a part can never be nameless."""
    value = value.strip()
    if not value:
        raise ValueError("This field cannot be blank.")
    return value


class ListingCreate(BaseModel):
    supplier_name: str = Field(max_length=200, description="Shop name.")

    _clean_supplier = field_validator("supplier_name")(_required_text)
    country: Country = Field(description="IE (Ireland) or UK.")
    url: str = Field(max_length=2000, description="Product page link.")
    price_eur: float | None = Field(None, ge=0, description="Price in euro, if known.")
    in_stock: bool | None = Field(None, description="Stock status at last check, if known.")
    last_checked_at: datetime | None = Field(None, description="When the listing was checked.")

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("The listing URL must start with http:// or https://.")
        return value


class ListingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    part_id: int
    supplier_name: str
    country: str
    url: str
    price_eur: float | None
    in_stock: bool | None
    last_checked_at: datetime | None
    url_ok: bool | None = Field(
        None, description="Server-side link check: true for HTTP 200-399; null if never checked."
    )
    url_status: int | None = Field(None, description="HTTP status of the last link check.")
    url_checked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field(description="Not checked in the last 30 days (or never).")  # type: ignore[prop-decorator]
    @property
    def stale(self) -> bool:
        from app.engine.selection import listing_is_stale

        return listing_is_stale({"last_checked_at": self.last_checked_at})


class PartBase(BaseModel):
    manufacturer: str = Field(max_length=200, description="Maker of the part.")
    model: str = Field(max_length=200, description="Model name or part number.")
    mass_g: float = Field(ge=0, description="Mass of the part in grams.")
    price_eur_estimate: float | None = Field(
        None, ge=0, description="Rough price in euro when no listing exists."
    )
    source: str = Field("", max_length=2000, description="Where the specification came from.")
    verified: bool = Field(False, description="False for placeholders and unchecked data.")
    notes: str = Field("", max_length=10000, description="Free-text notes.")

    _clean_identity = field_validator("manufacturer", "model")(_required_text)


class PartCreate(PartBase):
    category: str = Field(description="Category key, see GET /api/parts/categories.")
    spec: dict[str, Any] = Field(description="Category-specific specification.")
    listings: list[ListingCreate] = Field(default_factory=list, description="Supplier listings.")


# Columns that are NOT NULL in the database: an explicit null in a PATCH is a client error
# (422), not a uniqueness conflict.
_NON_NULLABLE_PART_FIELDS = (
    "category",
    "manufacturer",
    "model",
    "mass_g",
    "spec",
    "source",
    "verified",
    "notes",
)


class PartUpdate(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            nulls = [k for k in _NON_NULLABLE_PART_FIELDS if k in data and data[k] is None]
            if nulls:
                raise ValueError(
                    f"{', '.join(nulls)} cannot be null; omit the field to leave it unchanged."
                )
        return data

    _clean_identity = field_validator("manufacturer", "model")(
        lambda v: None if v is None else _required_text(v)
    )

    category: str | None = None
    manufacturer: str | None = Field(None, max_length=200)
    model: str | None = Field(None, max_length=200)
    mass_g: float | None = Field(None, ge=0)
    price_eur_estimate: float | None = Field(None, ge=0)
    spec: dict[str, Any] | None = None
    source: str | None = Field(None, max_length=2000)
    verified: bool | None = None
    notes: str | None = Field(None, max_length=10000)


class PartOut(PartBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category: str
    spec: dict[str, Any]
    listings: list[ListingOut]
    listings_refreshed_at: datetime | None = Field(
        None, description="Last supplier lookup (one per part per hour)."
    )
    listings_refresh_status: str | None = Field(
        None, description="queued | running | done | refused | error, or null if never run."
    )
    listings_refresh_message: str | None = None
    created_at: datetime
    updated_at: datetime


class CategoryField(BaseModel):
    name: str
    label: str
    unit: str | None
    type: str
    required: bool
    description: str
    enum: list[dict[str, Any]] | None = None
    min: float | None = None
    max: float | None = None


class CategoryOut(BaseModel):
    key: str
    label: str
    description: str
    fields: list[CategoryField]
