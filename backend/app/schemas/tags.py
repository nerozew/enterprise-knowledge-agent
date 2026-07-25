from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import APIModel

COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")


class TagCreate(APIModel):
    name: str = Field(min_length=1, max_length=50)
    color: str | None = Field(default=None, max_length=9)

    @field_validator("color")
    @classmethod
    def valid_color(cls, value: str | None) -> str | None:
        if value is not None and not COLOR_PATTERN.fullmatch(value):
            raise ValueError("颜色必须为 #RRGGBB 或 #RRGGBBAA")
        return value.upper() if value else value


class TagPatch(APIModel):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=50)
    color: str | None = Field(default=None, max_length=9)

    @field_validator("color")
    @classmethod
    def valid_color(cls, value: str | None) -> str | None:
        if value is not None and not COLOR_PATTERN.fullmatch(value):
            raise ValueError("颜色必须为 #RRGGBB 或 #RRGGBBAA")
        return value.upper() if value else value


class TagReference(APIModel):
    id: UUID
    name: str
    normalized_name: str
    color: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class TagResponse(TagReference):
    usage_count: int = Field(ge=0)
