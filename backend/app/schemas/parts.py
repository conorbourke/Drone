"""Parts and supplier listing request/response bodies."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Country = Literal["IE", "UK"]


class ListingCreate(BaseModel):
    supplier_name: str = Field(max_length=200, description="Shop name.")
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
    created_at: datetime
    updated_at: datetime


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


class PartCreate(PartBase):
    category: str = Field(description="Category key, see GET /api/parts/categories.")
    spec: dict[str, Any] = Field(description="Category-specific specification.")
    listings: list[ListingCreate] = Field(default_factory=list, description="Supplier listings.")


class PartUpdate(BaseModel):
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
