"""One description of an assembly drives both the STEP file and the expected BoM JSON, so the two
cannot drift (docs/10 §4: the generator builds files with known expected outputs)."""

from dataclasses import dataclass, field
from typing import Any

import cadquery as cq

from c2b_fixtures.xcaf import Node


@dataclass(eq=False)
class Spec:
    """A part or an assembly in a golden case.

    `name` is the name written into the STEP file (deliberately poor for standard parts).
    `qty` is the instance count under the parent. Children of an assembly are listed once; the
    assembly itself is instanced `qty` times.
    """

    name: str
    cls: str
    shape: cq.Shape | None = None
    qty: int = 1
    children: list["Spec"] = field(default_factory=list)
    # standard part identity (None for custom parts)
    designation: dict[str, Any] | None = None
    size_text: str | None = None
    standard_text: str | None = None
    designation_text: str | None = None  # e.g. bearing "6201"
    dims_text: str | None = None
    material_text: str | None = None  # custom parts: wording of the assumed material
    phantom: bool = False
    note: str | None = None
    locs: list[cq.Location] | None = None  # explicit placements, len == qty
    acceptable_classes: list[str] | None = None  # B6: semantic truth set instead of one class

    @property
    def is_assembly(self) -> bool:
        return self.shape is None

    @property
    def is_standard(self) -> bool:
        return self.designation is not None


def _auto_loc(index: int) -> cq.Location:
    return cq.Location(cq.Vector(45.0 * (index % 8), 45.0 * (index // 8), 0.0))


def to_node(spec: Spec, _cache: dict[int, Node] | None = None, _counter: list[int] | None = None) -> Node:
    """Build the XCAF node tree: one prototype per Spec, `qty` placements under its parent."""
    cache = _cache if _cache is not None else {}
    counter = _counter if _counter is not None else [0]
    if id(spec) in cache:
        return cache[id(spec)]
    if spec.shape is not None:
        node = Node(spec.name, shape=spec.shape)
    else:
        node = Node(spec.name)
        for child in spec.children:
            cnode = to_node(child, cache, counter)
            for i in range(child.qty):
                loc = child.locs[i] if child.locs else _auto_loc(counter[0])
                counter[0] += 1
                node.add(cnode, f"{child.name}:{i + 1}" if child.qty > 1 else child.name, loc)
    cache[id(spec)] = node
    return node
