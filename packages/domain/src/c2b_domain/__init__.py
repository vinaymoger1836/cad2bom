"""Domain models for CAD-to-BoM. Pure Pydantic; no I/O, no framework imports."""

from c2b_domain.bom import (
    BomDraft,
    BomLine,
    Designation,
    MakeBuyType,
    ReviewStatus,
)
from c2b_domain.cad import (
    CadExtractResult,
    CadNode,
    CadNodeKind,
    CadNotice,
    Cyl,
    CylinderFeature,
    ShapeFeatures,
)
from c2b_domain.jobs import (
    JobCreate,
    JobCreated,
    JobEvent,
    JobOut,
    JobStatus,
    JobUpdate,
    ProblemDetail,
    ProductInfo,
    UploadKind,
    UploadOut,
)
from c2b_domain.prov import Prov, Source

__all__ = [
    "BomDraft",
    "BomLine",
    "CadExtractResult",
    "CadNode",
    "CadNodeKind",
    "CadNotice",
    "Cyl",
    "CylinderFeature",
    "Designation",
    "JobCreate",
    "JobCreated",
    "JobEvent",
    "JobOut",
    "JobStatus",
    "JobUpdate",
    "MakeBuyType",
    "ProblemDetail",
    "ProductInfo",
    "Prov",
    "ReviewStatus",
    "ShapeFeatures",
    "Source",
    "UploadKind",
    "UploadOut",
]
