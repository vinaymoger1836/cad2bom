"""STEP extraction in cad-worker via OpenCascade XCAF (docs/03 §1-3, docs/05 §1).

Reads STEP assembly hierarchy, extracts unique shape features, checks quality,
generates a scaled binary GLB for Three.js, and outputs CadExtractResult JSON.
"""

import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import Any

from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.Message import Message_ProgressRange
from OCP.Quantity import Quantity_ColorRGBA
from OCP.RWGltf import RWGltf_CafWriter
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
from OCP.TColStd import TColStd_IndexedDataMapOfStringString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_LabelSequence, TDF_Tool
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

from c2b_cad_worker.features import compute_shape_features
from c2b_domain.cad import CadExtractResult, CadNode, CadNotice, ShapeFeatures

try:
    import resource

    _t = int(os.environ.get("C2B_CAD_TIMEOUT_S", "120"))
    resource.setrlimit(resource.RLIMIT_CPU, (_t, _t + 5))
except ImportError:
    pass

log = logging.getLogger("c2b.cad_extract")


def _label_name(label: TDF_Label) -> str:
    attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        return str(attr.Get().ToExtString())
    return ""


def _set_label_name(label: TDF_Label, name: str) -> None:
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))


def _label_entry(label: TDF_Label) -> str:
    asc = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, asc)
    return str(asc.ToCString())


def _transform_matrix_4x4(comp: TDF_Label, st: Any) -> list[float]:
    """Get 16 floats representing 4x4 column-major transformation matrix."""
    try:
        loc = st.GetLocation_s(comp)
        tr = loc.Transformation()
        # Three.js / GLTF column-major 4x4 matrix
        return [
            float(tr.Value(1, 1)),
            float(tr.Value(2, 1)),
            float(tr.Value(3, 1)),
            0.0,
            float(tr.Value(1, 2)),
            float(tr.Value(2, 2)),
            float(tr.Value(3, 2)),
            0.0,
            float(tr.Value(1, 3)),
            float(tr.Value(2, 3)),
            float(tr.Value(3, 3)),
            0.0,
            float(tr.Value(1, 4)),
            float(tr.Value(2, 4)),
            float(tr.Value(3, 4)),
            1.0,
        ]
    except Exception:
        return [
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ]


def _read_color(label: TDF_Label, ct: Any) -> tuple[float, float, float] | None:
    """Read RGB color from XCAF ColorTool if assigned."""
    with contextlib.suppress(Exception):
        color = Quantity_ColorRGBA()
        for ctype in (
            XCAFDoc_ColorType.XCAFDoc_ColorGen,
            XCAFDoc_ColorType.XCAFDoc_ColorSurf,
            XCAFDoc_ColorType.XCAFDoc_ColorCurv,
        ):
            if ct.GetColor_s(label, ctype, color):
                c = color.GetRGB()
                return (
                    round(float(c.Red()), 4),
                    round(float(c.Green()), 4),
                    round(float(c.Blue()), 4),
                )
    return None


def _export_glb(
    doc: Any,
    shape_tool: Any,
    shapes: dict[str, ShapeFeatures],
    entry_to_shape_id: dict[str, str],
    out_dir: Path,
    notices: list[CadNotice],
) -> str | None:
    """Tessellate the unique shapes and write `<out_dir>/model.glb` (mm -> m). Returns its
    path relative to the data dir (`out/<job id>/model.glb`), or None if it could not be made."""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        glb_path = out_dir / "model.glb"

        for sid in shapes:
            for entry_str, registered_sid in entry_to_shape_id.items():
                if registered_sid == sid:
                    proto_lbl = TDF_Label()
                    TDF_Tool.Label_s(doc.GetData(), TCollection_AsciiString(entry_str), proto_lbl)
                    if not proto_lbl.IsNull():
                        s = shape_tool.GetShape_s(proto_lbl)
                        if not s.IsNull():
                            BRepMesh_IncrementalMesh(s, 0.05, False, 0.5, True)
                    break

        writer = RWGltf_CafWriter(TCollection_AsciiString(str(glb_path)), True)
        writer.ChangeCoordinateSystemConverter().SetInputLengthUnit(0.001)  # mm -> meters
        writer.Perform(doc, TColStd_IndexedDataMapOfStringString(), Message_ProgressRange())

        if glb_path.exists() and glb_path.stat().st_size > 0:
            return f"out/{out_dir.name}/model.glb"
    except Exception as exc:
        log.warning("GLB export failed: %s", exc)
        notices.append(
            CadNotice(
                code="glb_export_failed",
                message=f"3D GLB preview generation failed: {exc}",
                severity="warning",
            )
        )
    return None


def extract_step(step_path: Path, out_dir: Path | None = None) -> CadExtractResult:
    """Full extraction pipeline for a STEP file."""
    if not step_path.exists():
        return CadExtractResult(
            status="error",
            notices=[
                CadNotice(
                    code="broken_file",
                    message=f"File not found: {step_path.name}",
                    severity="error",
                )
            ],
        )

    # Initialize XCAF document
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    XCAFApp_Application.GetApplication_s().NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)

    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    reader.SetLayerMode(True)
    reader.SetPropsMode(True)

    read_status = reader.ReadFile(str(step_path))
    if read_status != IFSelect_ReturnStatus.IFSelect_RetDone:
        status_name = {
            IFSelect_ReturnStatus.IFSelect_RetVoid: "void",
            IFSelect_ReturnStatus.IFSelect_RetError: "error",
            IFSelect_ReturnStatus.IFSelect_RetFail: "fail",
            IFSelect_ReturnStatus.IFSelect_RetStop: "stop",
        }.get(read_status, str(read_status))
        return CadExtractResult(
            status="error",
            notices=[
                CadNotice(
                    code="broken_file",
                    message=(
                        f"The CAD reader could not open '{step_path.name}' "
                        f"(status: {status_name}). "
                        "The file may be corrupt or not a valid STEP AP203/AP214/AP242 file."
                    ),
                    severity="error",
                )
            ],
        )

    try:
        transferred = reader.Transfer(doc)
    except Exception as exc:
        return CadExtractResult(
            status="error",
            notices=[
                CadNotice(
                    code="broken_file",
                    message=f"Failed transferring CAD data into XCAF: {exc}",
                    severity="error",
                )
            ],
        )

    if not transferred:
        return CadExtractResult(
            status="error",
            notices=[
                CadNotice(
                    code="broken_file",
                    message="STEP transfer returned false; no valid geometry was transferred.",
                    severity="error",
                )
            ],
        )

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)
    if free_shapes.Length() == 0:
        return CadExtractResult(
            status="error",
            notices=[
                CadNotice(
                    code="empty_shape",
                    message=f"No shapes or assemblies found in '{step_path.name}'.",
                    severity="error",
                )
            ],
        )

    nodes: list[CadNode] = []
    shapes: dict[str, ShapeFeatures] = {}
    notices: list[CadNotice] = []

    # Map of prototype label entry string -> shape_id ("S-0001"...)
    entry_to_shape_id: dict[str, str] = {}
    shape_counter = 1
    node_counter = 1
    total_leaves = 0

    # Cache for setting GLTF node names on all component labels
    label_to_node_id: list[tuple[TDF_Label, str]] = []

    def get_or_create_shape(proto_label: TDF_Label, props: dict[str, str]) -> str:
        nonlocal shape_counter
        entry_str = _label_entry(proto_label)
        if entry_str in entry_to_shape_id:
            return entry_to_shape_id[entry_str]

        sid = f"S-{shape_counter:04d}"
        shape_counter += 1
        entry_to_shape_id[entry_str] = sid

        topods_shape = shape_tool.GetShape_s(proto_label)
        features = compute_shape_features(topods_shape, sid, props)
        shapes[sid] = features

        # Quality checks on shape
        if features.volume_mm3 <= 1e-4:
            notices.append(
                CadNotice(
                    code="empty_shape",
                    message=f"Shape {sid} has near-zero volume ({features.volume_mm3:.2f} mm³).",
                    severity="warning",
                )
            )
        if not features.closed:
            notices.append(
                CadNotice(
                    code="open_shell",
                    message=f"Shape {sid} is an unclosed shell or has open boundary edges.",
                    severity="warning",
                )
            )
        if features.obb_mm[0] > 10_000.0 or (features.obb_mm[0] < 0.1 and features.obb_mm[0] > 0.0):
            notices.append(
                CadNotice(
                    code="unit_mismatch",
                    message=(
                        f"Shape {sid} dimension {features.obb_mm[0]:.1f} mm may indicate "
                        "a unit scale mismatch (expected millimetres)."
                    ),
                    severity="warning",
                )
            )

        return sid

    def traverse(label: TDF_Label, parent_id: str | None = None) -> str:
        nonlocal node_counter, total_leaves
        nid = f"N-{node_counter:04d}"
        node_counter += 1

        raw_name = _label_name(label) or f"Node_{nid}"
        is_asm = bool(shape_tool.IsAssembly_s(label))
        color = _read_color(label, color_tool)

        if not is_asm:
            # Single part root
            total_leaves += 1
            shape_id = get_or_create_shape(label, {})
            node = CadNode(
                id=nid,
                parent=parent_id,
                kind="part",
                name=raw_name,
                product_name=raw_name,
                shape_id=shape_id,
                instance_count=1,
                transform=[],
                color_rgb=color,
            )
            nodes.append(node)
            label_to_node_id.append((label, nid))
            return nid

        # Assembly node
        node = CadNode(
            id=nid,
            parent=parent_id,
            kind="assembly",
            name=raw_name,
            product_name=raw_name,
            instance_count=1,
            transform=[],
            color_rgb=color,
        )
        nodes.append(node)
        label_to_node_id.append((label, nid))

        # Explore components
        comps = TDF_LabelSequence()
        shape_tool.GetComponents_s(label, comps)

        # Group components by referred shape to consolidate instance counts
        grouped: dict[str, list[TDF_Label]] = {}
        for i in range(1, comps.Length() + 1):
            comp_label = comps.Value(i)
            ref_label = TDF_Label()
            shape_tool.GetReferredShape_s(comp_label, ref_label)
            ref_entry = _label_entry(ref_label)
            grouped.setdefault(ref_entry, []).append(comp_label)

        for _ref_entry, comp_list in grouped.items():
            first_comp = comp_list[0]
            ref_label = TDF_Label()
            shape_tool.GetReferredShape_s(first_comp, ref_label)
            sub_is_asm = bool(shape_tool.IsAssembly_s(ref_label))

            child_nid = f"N-{node_counter:04d}"
            node_counter += 1

            inst_count = len(comp_list)
            child_name = _label_name(ref_label) or _label_name(first_comp) or f"Part_{child_nid}"
            comp_color = _read_color(first_comp, color_tool) or _read_color(ref_label, color_tool)
            transform = _transform_matrix_4x4(first_comp, shape_tool)

            # Check for duplicate overlapping instances (identical transforms)
            if inst_count > 1:
                transforms_seen: set[str] = set()
                for c in comp_list:
                    tr_str = str([round(v, 2) for v in _transform_matrix_4x4(c, shape_tool)])
                    if tr_str in transforms_seen:
                        notices.append(
                            CadNotice(
                                code="duplicate_instance",
                                message=f"Component '{child_name}' has duplicate instances.",
                                node_id=child_nid,
                                severity="info",
                            )
                        )
                        break
                    transforms_seen.add(tr_str)

            # Assign GLTF node name to all component instance labels
            for c in comp_list:
                label_to_node_id.append((c, child_nid))

            if sub_is_asm:
                sub_node = CadNode(
                    id=child_nid,
                    parent=nid,
                    kind="assembly",
                    name=child_name,
                    product_name=child_name,
                    instance_count=inst_count,
                    transform=transform,
                    color_rgb=comp_color,
                )
                nodes.append(sub_node)

                # Recursively process subassembly children
                sub_comps = TDF_LabelSequence()
                shape_tool.GetComponents_s(ref_label, sub_comps)
                sub_grouped: dict[str, list[TDF_Label]] = {}
                for j in range(1, sub_comps.Length() + 1):
                    sc = sub_comps.Value(j)
                    sc_ref = TDF_Label()
                    shape_tool.GetReferredShape_s(sc, sc_ref)
                    sub_grouped.setdefault(_label_entry(sc_ref), []).append(sc)

                for _sc_entry, sc_list in sub_grouped.items():
                    sc_first = sc_list[0]
                    sc_ref = TDF_Label()
                    shape_tool.GetReferredShape_s(sc_first, sc_ref)
                    sc_is_asm = bool(shape_tool.IsAssembly_s(sc_ref))
                    sc_nid = f"N-{node_counter:04d}"
                    node_counter += 1
                    sc_count = len(sc_list)
                    sc_name = _label_name(sc_ref) or _label_name(sc_first) or f"Part_{sc_nid}"
                    sc_transform = _transform_matrix_4x4(sc_first, shape_tool)
                    sc_color = _read_color(sc_first, color_tool) or _read_color(sc_ref, color_tool)

                    for sc_lbl in sc_list:
                        label_to_node_id.append((sc_lbl, sc_nid))

                    if not sc_is_asm:
                        total_leaves += sc_count
                        sc_shape_id = get_or_create_shape(sc_ref, {})
                        nodes.append(
                            CadNode(
                                id=sc_nid,
                                parent=child_nid,
                                kind="part",
                                name=sc_name,
                                product_name=sc_name,
                                shape_id=sc_shape_id,
                                instance_count=sc_count,
                                transform=sc_transform,
                                color_rgb=sc_color,
                            )
                        )
                    else:
                        traverse(sc_ref, parent_id=child_nid)
            else:
                total_leaves += inst_count
                shape_id = get_or_create_shape(ref_label, {})
                child_node = CadNode(
                    id=child_nid,
                    parent=nid,
                    kind="part",
                    name=child_name,
                    product_name=child_name,
                    shape_id=shape_id,
                    instance_count=inst_count,
                    transform=transform,
                    color_rgb=comp_color,
                )
                nodes.append(child_node)

        return nid

    # Traverse each root shape
    for r_idx in range(1, free_shapes.Length() + 1):
        traverse(free_shapes.Value(r_idx))

    sampled = False
    if total_leaves > 5000:
        sampled = True
        notices.append(
            CadNotice(
                code="large_file",
                message=(
                    f"Assembly has {total_leaves:,} component instances. "
                    "Geometric features computed on prototype shapes; deep wall sampling adjusted."
                ),
                severity="info",
            )
        )

    # Label assignment for GLTF node names
    for lbl, nid in label_to_node_id:
        _set_label_name(lbl, nid)

    shape_tool.UpdateAssemblies()

    # GLB preview only when the caller names an output folder (the cad-worker server always does;
    # library-style calls such as the verification scripts must not write into the repository).
    glb_relpath: str | None = None
    if out_dir is not None:
        glb_relpath = _export_glb(doc, shape_tool, shapes, entry_to_shape_id, out_dir, notices)

    return CadExtractResult(
        job_id=out_dir.name if out_dir else None,
        status="done",
        unit_length="mm",
        unit_mass="g",
        scale_to_mm=1.0,
        nodes=nodes,
        shapes=shapes,
        notices=notices,
        glb_relpath=glb_relpath,
        leaf_count=total_leaves,
        sampled=sampled,
    )


def main() -> None:
    if len(sys.argv) < 2:
        res = CadExtractResult(
            status="error",
            notices=[
                CadNotice(
                    code="invalid_arguments",
                    message="Usage: python -m c2b_cad_worker.extract <step_file> [out_dir]",
                    severity="error",
                )
            ],
        )
        print(res.model_dump_json())
        sys.exit(1)

    step_path = Path(sys.argv[1]).resolve()
    out_dir = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None

    try:
        result = extract_step(step_path, out_dir)
    except Exception as exc:
        result = CadExtractResult(
            status="error",
            notices=[
                CadNotice(
                    code="broken_file",
                    message=f"Unexpected error extracting CAD: {exc}",
                    severity="error",
                )
            ],
        )

    # Print JSON line for parent process
    print(result.model_dump_json())


if __name__ == "__main__":
    main()
