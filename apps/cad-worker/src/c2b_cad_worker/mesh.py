"""Mesh extraction for converted SolidWorks files (ADR-018).

APS returns SolidWorks geometry as OBJ meshes, not exact solids, so this reads the triangles and
computes the features that are meaningful for a mesh: volume, area, oriented box, whether the
surface is closed. Cylinders, wall thickness and helix evidence need exact surfaces and stay empty.
Like STEP reading, this parses untrusted input, so it runs only here in the sandbox.

Run as a child process (`python -I -m c2b_cad_worker.mesh <out_dir> <name> <obj>...`) so a hostile
file can only cost one killed process.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import sys
from pathlib import Path

import numpy as np

from c2b_domain.cad import CadExtractResult, CadNode, CadNotice, ShapeFeatures

MAX_VERTICES = 6_000_000
MAX_OBJ_BYTES = 400 * 1024 * 1024
_NAME_CLEAN = re.compile(r"[^\w .\-()+]")


def clean(text: str) -> str:
    return _NAME_CLEAN.sub("_", text).strip() or "part"


class MeshError(ValueError):
    """The mesh file could not be used; the message is meant for the person."""


class Part:
    def __init__(self, name: str) -> None:
        self.name = name
        self.verts: list[tuple[float, float, float]] = []
        self.tris: list[tuple[int, int, int]] = []


def parse_obj(path: Path) -> list[Part]:
    """Read OBJ groups/objects into parts with their own vertices. Faces are fan-triangulated."""
    if path.stat().st_size > MAX_OBJ_BYTES:
        raise MeshError("The converted mesh file is too large to read.")
    all_v: list[tuple[float, float, float]] = []
    parts: list[Part] = []
    current: Part | None = None
    remap: dict[int, int] = {}

    def start(name: str) -> Part:
        part = Part(name)
        parts.append(part)
        remap.clear()
        return part

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            if raw.startswith("v "):
                bits = raw.split()
                try:
                    all_v.append((float(bits[1]), float(bits[2]), float(bits[3])))
                except (ValueError, IndexError):
                    raise MeshError("The converted mesh file has a malformed vertex.") from None
                if len(all_v) > MAX_VERTICES:
                    raise MeshError("The converted mesh has too many vertices to read.")
            elif raw.startswith(("o ", "g ")):
                name = raw[2:].strip() or f"{path.stem}_{len(parts)}"
                if current is not None and not current.tris:
                    current.name = name  # `o` followed by `g` for the same object
                else:
                    current = start(name)
            elif raw.startswith("f "):
                if current is None:
                    current = start(path.stem)
                idx: list[int] = []
                for tok in raw.split()[1:]:
                    try:
                        i = int(tok.split("/")[0])
                    except ValueError:
                        raise MeshError("The converted mesh file has a malformed face.") from None
                    i = i - 1 if i > 0 else len(all_v) + i
                    if not 0 <= i < len(all_v):
                        raise MeshError("The converted mesh file refers to a missing vertex.")
                    if i not in remap:
                        remap[i] = len(current.verts)
                        current.verts.append(all_v[i])
                    idx.append(remap[i])
                for k in range(1, len(idx) - 1):
                    current.tris.append((idx[0], idx[k], idx[k + 1]))
    return [p for p in parts if p.tris]


def _features(part: Part, shape_id: str) -> tuple[ShapeFeatures, bool]:
    v = np.asarray(part.verts, dtype=np.float64)
    t = np.asarray(part.tris, dtype=np.int64)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    cross = np.cross(b - a, c - a)
    area = float(0.5 * np.linalg.norm(cross, axis=1).sum())
    volume = abs(float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0))

    # Oriented box from the principal axes of the vertices.
    centred = v - v.mean(axis=0)
    if len(v) >= 4:
        _, vecs = np.linalg.eigh(np.cov(centred.T))
        proj = centred @ vecs
    else:
        proj = centred
    dims = sorted((float(x) for x in (proj.max(axis=0) - proj.min(axis=0))), reverse=True)

    # Closed surface: after welding coincident vertices every edge is shared by exactly two faces.
    welded = np.unique(np.round(v, 4), axis=0, return_inverse=True)[1].reshape(-1)
    wt = welded[t]
    edges = np.concatenate([wt[:, [0, 1]], wt[:, [1, 2]], wt[:, [2, 0]]])
    edges = np.sort(edges, axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    closed = bool(np.all(counts == 2))

    sig = hashlib.sha1(
        f"{volume:.2f}|{area:.2f}|{dims[0]:.2f}|{dims[1]:.2f}|{dims[2]:.2f}".encode(),
        usedforsecurity=False,
    ).hexdigest()[:16]
    feats = ShapeFeatures(
        shape_id=shape_id,
        signature=sig,
        volume_mm3=round(volume, 3),
        area_mm2=round(area, 3),
        obb_mm=(round(dims[0], 4), round(dims[1], 4), round(dims[2], 4)),
        face_hist={"mesh": 1.0},
        closed=closed,
        mesh_based=True,
    )
    return feats, closed


def _glb(parts: list[tuple[str, Part]], scale: float) -> bytes:
    """A minimal binary glTF: one mesh node per part, node names = CadNode ids (viewer link)."""
    chunks: list[bytes] = []
    views: list[dict[str, int]] = []
    accessors: list[dict[str, object]] = []
    meshes: list[dict[str, object]] = []
    nodes: list[dict[str, object]] = []
    offset = 0

    def add(data: bytes, target: int) -> int:
        nonlocal offset
        pad = (-len(data)) % 4
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        chunks.append(data + b"\0" * pad)
        offset += len(data) + pad
        return len(views) - 1

    for i, (node_id, part) in enumerate(parts):
        pos = (np.asarray(part.verts, dtype=np.float32) * np.float32(scale)).astype("<f4")
        idx = np.asarray(part.tris, dtype="<u4").reshape(-1)
        pv = add(pos.tobytes(), 34962)
        iv = add(idx.tobytes(), 34963)
        accessors.append(
            {
                "bufferView": pv,
                "componentType": 5126,
                "count": len(pos),
                "type": "VEC3",
                "min": pos.min(axis=0).tolist(),
                "max": pos.max(axis=0).tolist(),
            }
        )
        accessors.append(
            {"bufferView": iv, "componentType": 5125, "count": len(idx), "type": "SCALAR"}
        )
        meshes.append({"primitives": [{"attributes": {"POSITION": 2 * i}, "indices": 2 * i + 1}]})
        nodes.append({"name": node_id, "mesh": i})
    blob = b"".join(chunks)
    doc = {
        "asset": {"version": "2.0", "generator": "c2b-cad-worker mesh"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    return (
        struct.pack("<III", 0x46546C67, 2, total)
        + struct.pack("<II", len(js), 0x4E4F534A)
        + js
        + struct.pack("<II", len(blob), 0x004E4942)
        + blob
    )


def extract_mesh(
    obj_paths: list[Path], out_dir: Path | None, product_name: str
) -> CadExtractResult:
    parts: list[Part] = []
    try:
        for p in obj_paths:
            parts.extend(parse_obj(p))
    except MeshError as exc:
        return _error(str(exc))
    except OSError:
        return _error("The converted mesh file could not be read.")
    if not parts:
        return _error("The converted mesh contains no geometry.")

    nodes: list[CadNode] = []
    shapes: dict[str, ShapeFeatures] = {}
    notices: list[CadNotice] = [
        CadNotice(
            code="mesh_based",
            message=(
                "This model was converted to meshes, so shapes are measured from triangles, not "
                "exact solids. Classes that depend on cylinders or wall thickness are less sure. "
                "Upload a STEP AP242 export too for the best result."
            ),
            severity="info",
        )
    ]
    root_id: str | None = None
    if len(parts) > 1:
        root_id = "N-0001"
        nodes.append(CadNode(id=root_id, kind="assembly", name=clean(product_name)))
    glb_parts: list[tuple[str, Part]] = []
    seen: dict[str, str] = {}
    for i, part in enumerate(parts, start=1):
        node_id = f"N-{len(nodes) + 1:04d}"
        sid = f"S-{i:04d}"
        feats, closed = _features(part, sid)
        # Identical meshes share one shape, like STEP prototypes.
        if feats.signature in seen:
            sid = seen[feats.signature]
            feats = shapes[sid]
        else:
            seen[feats.signature] = sid
            shapes[sid] = feats
        if not closed:
            notices.append(
                CadNotice(
                    code="open_shell",
                    message=f"The mesh of '{part.name}' has gaps, so its volume may be off.",
                    node_id=node_id,
                    severity="warning",
                )
            )
        nodes.append(
            CadNode(
                id=node_id,
                parent=root_id,
                kind="part",
                name=clean(part.name),
                shape_id=sid,
            )
        )
        glb_parts.append((node_id, part))

    glb_rel: str | None = None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "model.glb").write_bytes(_glb(glb_parts, 0.001))
        glb_rel = f"out/{out_dir.name}/model.glb"
    return CadExtractResult(
        job_id=out_dir.name if out_dir else None,
        status="done",
        nodes=nodes,
        shapes=shapes,
        notices=notices,
        glb_relpath=glb_rel,
        leaf_count=len(glb_parts),
    )


def _error(message: str) -> CadExtractResult:
    return CadExtractResult(
        status="error",
        notices=[CadNotice(code="broken_file", message=message, severity="error")],
    )


def main() -> None:
    if len(sys.argv) < 4:
        print(
            _error(
                "Usage: python -m c2b_cad_worker.mesh <out_dir> <name> <obj>..."
            ).model_dump_json()
        )
        sys.exit(1)
    out_dir = Path(sys.argv[1]).resolve()
    name = sys.argv[2]
    try:
        result = extract_mesh([Path(p).resolve() for p in sys.argv[3:]], out_dir, name)
    except Exception as exc:
        result = _error(f"Unexpected error reading the converted mesh: {exc}")
    print(result.model_dump_json())


if __name__ == "__main__":
    main()
