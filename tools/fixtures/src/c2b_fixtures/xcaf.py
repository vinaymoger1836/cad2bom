"""Write and read STEP assemblies through OpenCascade XCAF.

CadQuery's own STEP writer flattens repeated parts. The golden cases need real instances (one
prototype shape, many placements) and nested sub-assemblies, so the fixtures are written here with
OCP directly. The reader is used by the end-to-end check to count what was actually written.
"""

from dataclasses import dataclass, field
from pathlib import Path

import cadquery as cq
from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_LabelSequence
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_DocumentTool


@dataclass(eq=False)
class Node:
    """A prototype: either a solid (`shape`) or an assembly (`children`).

    Add the same Node object to several parents, or several times to one parent, to create
    instances that share one prototype shape.
    """

    name: str
    shape: cq.Shape | None = None
    children: list[tuple["Node", str, cq.Location]] = field(default_factory=list)

    def add(self, child: "Node", instance_name: str | None = None, loc: cq.Location | None = None) -> "Node":
        self.children.append((child, instance_name or child.name, loc or cq.Location()))
        return self


def _name(label: TDF_Label, text: str) -> None:
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(text))


def write_step(root: Node, path: Path) -> None:
    """Write `root` as a STEP AP214 assembly (millimetres)."""
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    labels: dict[int, TDF_Label] = {}

    def build(node: Node) -> TDF_Label:
        cached = labels.get(id(node))
        if cached is not None:
            return cached
        if node.shape is not None:
            lab = shape_tool.AddShape(node.shape.wrapped, False)
        else:
            lab = shape_tool.NewShape()
            for child, instance_name, loc in node.children:
                comp = shape_tool.AddComponent(lab, build(child), loc.wrapped)
                _name(comp, instance_name)
        _name(lab, node.name)
        labels[id(node)] = lab
        return lab

    build(root)
    shape_tool.UpdateAssemblies()
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    writer.Transfer(doc, STEPControl_AsIs)
    path.parent.mkdir(parents=True, exist_ok=True)
    status = writer.Write(str(path))
    if int(status) != 1:  # IFSelect_RetDone
        raise RuntimeError(f"STEP write failed for {path} (status {status})")


# --- reader ------------------------------------------------------------------------------------


@dataclass
class StepNode:
    name: str
    is_assembly: bool
    count: int  # instances of this component under its parent
    children: list["StepNode"] = field(default_factory=list)
    volume_mm3: float | None = None


def _label_name(label: TDF_Label) -> str:
    attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        return str(attr.Get().ToExtString())
    return ""


def read_step_tree(path: Path) -> StepNode:
    """Read a STEP file and return its assembly tree with instance counts under each parent."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    XCAFApp_Application.GetApplication_s().NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if int(reader.ReadFile(str(path))) != 1:
        raise RuntimeError(f"cannot read {path}")
    if not reader.Transfer(doc):
        raise RuntimeError(f"cannot transfer {path}")
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    def volume(label: TDF_Label) -> float:
        shape = st.GetShape_s(label)
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        return float(props.Mass())

    def walk(label: TDF_Label, count: int) -> StepNode:
        is_asm = bool(st.IsAssembly_s(label))
        node = StepNode(name=_label_name(label), is_assembly=is_asm, count=count)
        if not is_asm:
            node.volume_mm3 = volume(label)
            return node
        comps = TDF_LabelSequence()
        st.GetComponents_s(label, comps)
        grouped: dict[str, tuple[TDF_Label, int, str]] = {}
        for i in range(1, comps.Length() + 1):
            comp = comps.Value(i)
            ref = TDF_Label()
            st.GetReferredShape_s(comp, ref)
            key = f"{ref.Tag()}"
            prev = grouped.get(key)
            grouped[key] = (ref, (prev[1] if prev else 0) + 1, prev[2] if prev else _label_name(comp))
        for ref, n, _inst in grouped.values():
            node.children.append(walk(ref, n))
        return node

    free = TDF_LabelSequence()
    st.GetFreeShapes(free)
    if free.Length() == 0:
        raise RuntimeError(f"{path} has no shapes")
    return walk(free.Value(1), 1)


def count_solids(node: StepNode) -> int:
    """Total solid instances below `node` (each instance counted)."""
    if not node.is_assembly:
        return 1
    return sum(c.count * count_solids(c) for c in node.children)
