from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import (
    AliasChoices,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from app.schemas.common import APIModel
from app.schemas.tags import TagReference


class InspirationCreate(APIModel):
    title: str | None = Field(default=None, max_length=200)
    content: str = Field(min_length=1, max_length=20000)
    source_url: HttpUrl | None = None
    tag_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @field_validator("tag_ids")
    @classmethod
    def unique_tags(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("tag_ids 不得重复")
        return value


class InspirationPatch(APIModel):
    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
        extra="forbid",
    )

    version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=20000)
    summary: str | None = Field(default=None, max_length=2000)
    expanded_content: str | None = Field(default=None, max_length=20000)
    source_url: HttpUrl | None = None
    tag_ids: list[UUID] | None = Field(default=None, max_length=20)

    @field_validator("tag_ids")
    @classmethod
    def unique_tags(cls, value: list[UUID] | None) -> list[UUID] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("tag_ids 不得重复")
        return value

    @model_validator(mode="after")
    def content_cannot_be_null(self) -> InspirationPatch:
        if "content" in self.model_fields_set and self.content is None:
            raise ValueError("content 不能设为 null")
        return self


class InspirationResponse(APIModel):
    id: UUID
    workspace_id: UUID
    author_id: UUID = Field(
        validation_alias=AliasChoices("user_id", "author_id")
    )
    title: str | None
    content: str
    summary: str | None
    expanded_content: str | None
    source_url: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    tags: list[TagReference]


class VersionRequest(APIModel):
    version: int = Field(ge=1)
