"""Contratos de dados do GTA Shorts Factory (Pydantic v2)."""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class RightsStatus(str, Enum):
    APPROVED = "APPROVED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"


class CategoryEnum(str, Enum):
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
    speaker: Optional[str] = None


class RightsMetadata(BaseModel):
    source_id: str
    creator_handle: str
    status: RightsStatus
    commercial_permission: bool
    evidence_url_or_path: str
    expiration_date: Optional[str] = None


class ViralCandidate(BaseModel):
    candidate_id: str
    start_time: float
    end_time: float
    duration: float
    category: CategoryEnum
    score: float = Field(..., ge=0, le=100)
    hook_text: str
    justification: str
    risk_flags: List[str] = []
    confidence: float


class EditPlan(BaseModel):
    job_id: str
    composition_template: str  # ex: "GTASplitScreen", "GTACinematic"
    candidate_id: str
    source_video_path: str
    output_duration_seconds: float
    crop_mode: str
    words: List[WordSegment]
    sfx_triggers: List[dict]
    background_music_track: Optional[str] = None


class QCReport(BaseModel):
    job_id: str
    passed: bool
    audio_clipping_detected: bool
    black_frames_detected: bool
    aspect_ratio_valid: bool
    rights_check_passed: bool
    failure_reasons: List[str] = []
