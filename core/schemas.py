"""Contratos de dados do Shorts Factory (Pydantic v2)."""

from enum import StrEnum

from pydantic import BaseModel, Field


class RightsStatus(StrEnum):
    APPROVED = "APPROVED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"


class CategoryEnum(StrEnum):
    FUNNY = "FUNNY"
    SURPRISE = "SURPRISE"
    FAIL = "FAIL"
    CHAOS = "CHAOS"
    REACTION = "REACTION"
    DISCOVERY = "DISCOVERY"
    TENSION = "TENSION"


class WordSegment(BaseModel):
    word: str
    start: float
    end: float
    score: float
    speaker: str | None = None


class RightsMetadata(BaseModel):
    source_id: str
    creator_handle: str
    status: RightsStatus
    commercial_permission: bool
    evidence_url_or_path: str
    expiration_date: str | None = None


class ViralCandidate(BaseModel):
    candidate_id: str
    start_time: float
    end_time: float
    duration: float
    category: CategoryEnum
    score: float = Field(..., ge=0, le=100)
    hook_text: str
    justification: str
    risk_flags: list[str] = []
    confidence: float


class EditPlan(BaseModel):
    job_id: str
    composition_template: str  # ex: "ShortSplitScreen", "ShortCinematic"
    candidate_id: str
    source_video_path: str
    output_duration_seconds: float
    crop_mode: str
    words: list[WordSegment]
    sfx_triggers: list[dict]
    background_music_track: str | None = None


class QCReport(BaseModel):
    job_id: str
    passed: bool
    audio_clipping_detected: bool
    black_frames_detected: bool
    aspect_ratio_valid: bool
    rights_check_passed: bool
    failure_reasons: list[str] = []
