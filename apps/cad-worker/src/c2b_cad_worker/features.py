"""Geometric feature extraction for prototype shapes (docs/03 §3, docs/05 §1)."""

import contextlib
import hashlib
from typing import Any

from OCP.Bnd import Bnd_Box, Bnd_OBB
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_SurfaceType
from OCP.gp import gp_Pnt, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_FACE, TopAbs_IN, TopAbs_OUT, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from c2b_domain.cad import Cyl, ShapeFeatures


def compute_shape_features(
    shape: TopoDS_Shape,
    shape_id: str,
    props: dict[str, str] | None = None,
) -> ShapeFeatures:
    """Compute geometric descriptors for a unique prototype shape."""
    props = props or {}

    # 1. Mass properties
    vprops = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, vprops)
    volume_mm3 = max(0.0, float(vprops.Mass()))

    sprops = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, sprops)
    area_mm2 = max(0.0, float(sprops.Mass()))

    # 2. Bounding boxes (OBB with AABB fallback)
    obb = Bnd_OBB()
    BRepBndLib.AddOBB_s(shape, obb)
    if not obb.IsVoid():
        dims = sorted(
            [
                float(2.0 * obb.XHSize()),
                float(2.0 * obb.YHSize()),
                float(2.0 * obb.ZHSize()),
            ],
            reverse=True,
        )
        obb_mm = (round(dims[0], 4), round(dims[1], 4), round(dims[2], 4))
    else:
        box = Bnd_Box()
        BRepBndLib.Add_s(shape, box, True)
        if not box.IsVoid():
            xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
            dims = sorted([abs(xmax - xmin), abs(ymax - ymin), abs(zmax - zmin)], reverse=True)
            obb_mm = (round(dims[0], 4), round(dims[1], 4), round(dims[2], 4))
        else:
            obb_mm = (0.0, 0.0, 0.0)

    # 3. Face exploration, cylinder extraction, and face-type histogram
    classifier = BRepClass3d_SolidClassifier(shape)
    cylinders: list[Cyl] = []
    area_by_type: dict[str, float] = {
        "plane": 0.0,
        "cylinder": 0.0,
        "cone": 0.0,
        "sphere": 0.0,
        "torus": 0.0,
        "bspline": 0.0,
        "other": 0.0,
    }

    planar_faces_info: list[dict[str, Any]] = []
    helix_evidence = False

    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        f_props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, f_props)
        f_area = max(0.0, float(f_props.Mass()))

        try:
            surf = BRepAdaptor_Surface(face)
            stype = surf.GetType()
        except Exception:
            exp.Next()
            continue

        match stype:
            case GeomAbs_SurfaceType.GeomAbs_Plane:
                area_by_type["plane"] += f_area
                pln = surf.Plane()
                p_norm = pln.Axis().Direction()
                p_loc = pln.Location()
                planar_faces_info.append(
                    {
                        "normal": (p_norm.X(), p_norm.Y(), p_norm.Z()),
                        "point": (p_loc.X(), p_loc.Y(), p_loc.Z()),
                        "area": f_area,
                    }
                )

            case GeomAbs_SurfaceType.GeomAbs_Cylinder:
                area_by_type["cylinder"] += f_area
                cyl = surf.Cylinder()
                r = round(float(cyl.Radius()), 4)
                axis_line = cyl.Axis()
                axis_dir = axis_line.Direction()
                axis = (
                    round(axis_dir.X(), 4),
                    round(axis_dir.Y(), 4),
                    round(axis_dir.Z(), 4),
                )
                umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
                length = round(abs(vmax - vmin), 4)

                is_inner = False
                with contextlib.suppress(Exception):
                    u_mid = (umin + umax) / 2.0
                    v_mid = (vmin + vmax) / 2.0
                    pnt = surf.Value(u_mid, v_mid)
                    loc = axis_line.Location()
                    diff = gp_Vec(loc, pnt)
                    proj = diff.Dot(gp_Vec(axis_dir))
                    rad_x = diff.X() - proj * axis_dir.X()
                    rad_y = diff.Y() - proj * axis_dir.Y()
                    rad_z = diff.Z() - proj * axis_dir.Z()
                    rad_vec = gp_Vec(rad_x, rad_y, rad_z)
                    mag = rad_vec.Magnitude()
                    if mag > 1e-6:
                        rad_dir = rad_vec.Normalized()
                        eps = min(0.05, max(0.001, r * 0.05))
                        p_in = gp_Pnt(
                            pnt.X() - rad_dir.X() * eps,
                            pnt.Y() - rad_dir.Y() * eps,
                            pnt.Z() - rad_dir.Z() * eps,
                        )
                        p_out = gp_Pnt(
                            pnt.X() + rad_dir.X() * eps,
                            pnt.Y() + rad_dir.Y() * eps,
                            pnt.Z() + rad_dir.Z() * eps,
                        )
                        classifier.Perform(p_in, 1e-4)
                        st_in = classifier.State()
                        classifier.Perform(p_out, 1e-4)
                        st_out = classifier.State()
                        is_inner = st_in == TopAbs_OUT and st_out == TopAbs_IN

                cylinders.append(Cyl(radius_mm=r, length_mm=length, axis=axis, is_inner=is_inner))

            case GeomAbs_SurfaceType.GeomAbs_Cone:
                area_by_type["cone"] += f_area
            case GeomAbs_SurfaceType.GeomAbs_Sphere:
                area_by_type["sphere"] += f_area
            case GeomAbs_SurfaceType.GeomAbs_Torus:
                area_by_type["torus"] += f_area
            case (
                GeomAbs_SurfaceType.GeomAbs_BezierSurface
                | GeomAbs_SurfaceType.GeomAbs_BSplineSurface
            ):
                area_by_type["bspline"] += f_area
                # Helical thread check: bspline with non-zero curvature variation or AP242 hint
                if (
                    "thread" in str(props).lower()
                    or "pitch" in str(props).lower()
                    or "iso" in str(props).lower()
                ):
                    helix_evidence = True
            case _:
                area_by_type["other"] += f_area

        exp.Next()

    # Normalise face histogram to area fractions
    face_hist: dict[str, float] = {}
    if area_mm2 > 0.0:
        for k, v in area_by_type.items():
            if v > 0.0:
                face_hist[k] = round(v / area_mm2, 4)

    # 4. Rotational symmetry: fraction of surface area on surfaces of revolution
    rev_area = (
        area_by_type["cylinder"]
        + area_by_type["cone"]
        + area_by_type["sphere"]
        + area_by_type["torus"]
    )
    # If cylinders exist and share dominant axis, include perpendicular planar endcaps
    if cylinders:
        dom_axis = cylinders[0].axis
        for p in planar_faces_info:
            norm = p["normal"]
            dot = abs(norm[0] * dom_axis[0] + norm[1] * dom_axis[1] + norm[2] * dom_axis[2])
            if dot > 0.98:  # perpendicular to cylindrical surface -> circular end cap
                rev_area += p["area"]

    rot_symmetry = round(min(1.0, rev_area / area_mm2), 4) if area_mm2 > 0.0 else 0.0

    # 5. Thin-plate and constant thickness
    thin_plate = obb_mm[2] <= 3.0 and (
        face_hist.get("plane", 0.0) >= 0.5 or (obb_mm[1] > 0 and obb_mm[2] <= 0.1 * obb_mm[1])
    )

    const_thickness_mm: float | None = None
    wall_sample_mm: list[float] = []

    # Check for parallel opposite planar face pairs
    dist_counts: dict[float, float] = {}
    n_planes = len(planar_faces_info)
    for i in range(n_planes):
        p1 = planar_faces_info[i]
        n1 = p1["normal"]
        loc1 = p1["point"]
        for j in range(i + 1, n_planes):
            p2 = planar_faces_info[j]
            n2 = p2["normal"]
            loc2 = p2["point"]
            # Opposite normals?
            dot = n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2]
            if dot < -0.98:
                # Distance along normal
                dx = loc1[0] - loc2[0]
                dy = loc1[1] - loc2[1]
                dz = loc1[2] - loc2[2]
                dist = round(abs(dx * n1[0] + dy * n1[1] + dz * n1[2]), 2)
                if 0.2 <= dist <= 50.0:
                    weight = min(p1["area"], p2["area"])
                    dist_counts[dist] = dist_counts.get(dist, 0.0) + weight

    if dist_counts and area_mm2 > 0.0:
        best_dist, best_area = max(dist_counts.items(), key=lambda item: item[1])
        if best_area / area_mm2 >= 0.25:
            const_thickness_mm = best_dist
            wall_sample_mm.append(best_dist)

    if const_thickness_mm is None and thin_plate and obb_mm[2] > 0.0:
        const_thickness_mm = round(obb_mm[2], 2)
        wall_sample_mm.append(const_thickness_mm)

    # 6. Topological closure
    exp_solid = TopExp_Explorer(shape, TopAbs_SOLID)
    is_solid = exp_solid.More()

    exp_shell = TopExp_Explorer(shape, TopAbs_FACE)
    has_faces = exp_shell.More()

    analyzer = BRepCheck_Analyzer(shape)
    closed = (is_solid or has_faces) and analyzer.IsValid()

    # 7. Signature hash
    canonical = (
        f"{round(volume_mm3, 2)}:{round(area_mm2, 2)}:"
        f"{obb_mm[0]:.2f}x{obb_mm[1]:.2f}x{obb_mm[2]:.2f}:"
        f"{sorted(face_hist.items())}"
    )
    signature = hashlib.sha256(canonical.encode()).hexdigest()

    return ShapeFeatures(
        shape_id=shape_id,
        signature=signature,
        volume_mm3=round(volume_mm3, 4),
        area_mm2=round(area_mm2, 4),
        obb_mm=obb_mm,
        face_hist=face_hist,
        cylinders=cylinders,
        rot_symmetry=rot_symmetry,
        const_thickness_mm=const_thickness_mm,
        wall_sample_mm=wall_sample_mm,
        thin_plate=thin_plate,
        helix_evidence=helix_evidence,
        closed=closed,
    )
