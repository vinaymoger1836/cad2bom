"""Job, upload and event shapes shared by the API, the worker and the generated TS types."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal[
    "uploading", "converting", "extracting", "building", "review", "approved", "failed"
]

UploadKind = Literal["cad", "photo", "bom_file", "datasheet"]


class ApiModel(BaseModel):
    """Base for API shapes: unknown fields are rejected (and keep generated TS types tight)."""

    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class ProductInfo(ApiModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=4000)] | None = None
    product_type: Annotated[str, Field(max_length=120)] | None = None
    annual_volume: Annotated[int, Field(ge=0, le=1_000_000_000)] | None = None
    variants: Annotated[str, Field(max_length=2000)] | None = None


class JobCreate(ProductInfo):
    """Body of POST /jobs."""

    consent_native_conversion: bool = False


class JobUpdate(ApiModel):
    """Body of PATCH /jobs/{job_id}."""

    consent_native_conversion: bool | None = None


class JobCreated(ApiModel):
    """Response of POST /jobs. `token` is shown once; only its hash is stored."""

    id: UUID
    link: str
    token: str


class JobOut(ApiModel):
    id: UUID
    status: JobStatus
    product: ProductInfo
    library_version: str
    consent_native_conversion: bool = False
    expires_at: datetime | None = None
    created_at: datetime
    uploads: list["UploadOut"] = Field(default_factory=list)


class UploadOut(ApiModel):
    id: UUID
    filename: str
    kind: UploadKind
    detected_type: str
    size_bytes: int
    sha256: str
    origin: Literal["user", "converted"] = "user"
    created_at: datetime


class JobEvent(ApiModel):
    """One progress event, streamed over SSE and replayable by id."""

    id: int
    job_id: UUID
    kind: str  # "status", "step", "notice", "cad_stub", ...
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ProblemDetail(ApiModel):
    """RFC 7807-style error body. `detail` is written for the person using the app."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str
    code: Literal[
        "unauthorized",
        "not_found",
        "file_too_large",
        "unsupported_type",
        "type_mismatch",
        "too_many_files",
        "invalid_request",
        "wrong_state",
        "not_ready",
        "native_not_configured",
        "consent_required",
        "rate_limited",
        "forbidden",
    ]


JobOut.model_rebuild()

__all__ = [
    "JobCreate",
    "JobCreated",
    "JobEvent",
    "JobOut",
    "JobStatus",
    "JobUpdate",
    "ProblemDetail",
    "ProductInfo",
    "UploadKind",
    "UploadOut",
]
