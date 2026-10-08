"""Authentication request and response bodies."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    password: str = Field(description="The owner password.")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="User id.")
    email: str = Field(description="Display identity of the owner.")
    display_name: str = Field(description="Name shown in the top bar.")


class LoginResponse(BaseModel):
    user: UserOut
