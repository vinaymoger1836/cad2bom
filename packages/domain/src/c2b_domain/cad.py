"""CAD extraction domain models (docs/05 §1). Pure Pydantic."""

from typing import Literal

from pydantic import Field

from c2b_domain.jobs import ApiModel

CadNodeKind = Literal["assembly", "part", "reference"]


class Cyl(ApiModel):
    """Cylindrical face feature (radius, length, axis vector, inner bore)."""

    radius_mm: float
    length_mm: float
    axis: tuple[float, float, float]
    is_inner: bool


CylinderFeature = Cyl


class ShapeFeatures(ApiModel):
    """Geometric descriptors computed once per unique prototype shape (docs/03 §3, docs/05 §1)."""

    shape_id: str
    signature: str
    volume_mm3: float
    area_mm2: float
    obb_mm: tuple[float, float, float]  # sorted dimensions (length >= width >= height)
    face_hist: dict[str, float] = Field(default_factory=dict)  # surface area fraction per face type
    cylinders: list[Cyl] = Field(default_factory=list)
    rot_symmetry: float = 0.0  # 0.0 to 1.0 fraction of revolution-symmetric surface area
    const_thickness_mm: float | None = None
    wall_sample_mm: list[float] = Field(default_factory=list)
    thin_plate: bool = False
    helix_evidence: bool = False
    closed: bool = True


class CadNode(ApiModel):
    """One node in the assembly tree (XCAF hierarchy)."""

    id: str  # "N-0001", "N-0002", ...
    parent: str | None = None  # id of parent CadNode
    kind: CadNodeKind
    name: str
    product_name: str | None = None
    props: dict[str, str] = Field(default_factory=dict)
    shape_id: str | None = None  # "S-0001" if part, None if assembly
    instance_count: int = 1
    transform: list[float] = Field(default_factory=list)  # 16 floats (4x4 matrix)
    color_rgb: tuple[float, float, float] | None = None
    excluded: str | None = None  # reason when filtered


class CadNotice(ApiModel):
    """CAD quality check notice (open shell, zero volume, scale mismatch, etc.)."""

    code: str
    message: str
    node_id: str | None = None
    severity: Literal["info", "warning", "error"] = "warning"


class CadExtractResult(ApiModel):
    """Full extraction result emitted by cad-worker and stored in Postgres."""

    job_id: str | None = None
    upload_id: str | None = None
    status: Literal["done", "error"]
    unit_length: str = "mm"
    unit_mass: str = "g"
    scale_to_mm: float = 1.0
    nodes: list[CadNode] = Field(default_factory=list)
    shapes: dict[str, ShapeFeatures] = Field(default_factory=dict)
    notices: list[CadNotice] = Field(default_factory=list)
    glb_relpath: str | None = None
    leaf_count: int = 0
    sampled: bool = False


__all__ = [
    "CadExtractResult",
    "CadNode",
    "CadNodeKind",
    "CadNotice",
    "Cyl",
    "CylinderFeature",
    "ShapeFeatures",
]
