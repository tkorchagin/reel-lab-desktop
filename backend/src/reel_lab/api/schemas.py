"""Request models for the HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(default="New reel", min_length=1, max_length=160)


class SessionTransferCreate(BaseModel):
    next_path: str = Field(min_length=1, max_length=2048)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    duration: Literal[0, 15, 30, 60] | None = None
    include_all: bool | None = None
    media_filter: Literal["all", "video", "photo"] | None = None
    media_order: Literal["capture_date", "manual", "random"] | None = None
    random_seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    min_clip_duration: float | None = Field(default=None, ge=0.2, le=2.0)
    processing_profile: Literal["fast", "smart", "maximum"] | None = None
    music_track_id: str | None = None
    music_start: float | None = Field(default=None, ge=0)


class AssetSelection(BaseModel):
    selected: bool


class AttachAssets(BaseModel):
    asset_ids: list[str] = Field(min_length=1)


class AssetOrder(BaseModel):
    asset_ids: list[str] = Field(min_length=1)


class ProcessRequest(BaseModel):
    profile: Literal["fast", "smart", "maximum"] = "maximum"


class GenerateRequest(BaseModel):
    duration: Literal[0, 15, 30, 60]
    include_all: bool
    media_filter: Literal["all", "video", "photo"]
    media_order: Literal["capture_date", "manual", "random"]
    random_seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    min_clip_duration: float = Field(default=0.6, ge=0.2, le=2.0)
    music_track_id: str
    music_start: float | None = Field(default=None, ge=0)
