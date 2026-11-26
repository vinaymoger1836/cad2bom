"""Pydantic schemas for every file under `library/` (docs/06).

Closed vocabularies that the engine implements in code (condition primitives, actions, stages) are
Literals here; everything a domain expert edits (classes, materials, tables, thresholds) is data.
"""

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MakeBuy = Literal["buy", "make", "supplier_made"]
Lang = Literal["en", "de", "hi", "any"]
Stage = Literal["classify", "structure", "gap", "mbom", "makebuy", "validate"]

CLASS_CODE = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){0,2}$"
RULE_ID = r"^R-[A-Z]{3}-\d{3}$"
VERSION_RE = r"^\d{4}\.\d{2}\.\d+$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --- VERSION.yaml -----------------------------------------------------------------------------


class VersionFile(Strict):
    version: Annotated[str, Field(pattern=VERSION_RE)]
    date: date
    changelog: list[str] = Field(min_length=1)


# --- taxonomy.yaml ----------------------------------------------------------------------------


class TaxonomyDefaults(Strict):
    uom: str
    make_buy_default: MakeBuy
    usually_modelled: bool
    buy_as_assembly: bool


class ClassDef(Strict):
    code: Annotated[str, Field(pattern=CLASS_CODE)]
    noun: str = Field(min_length=1)
    modifiers: list[str] = Field(default_factory=list)
    uom: str | None = None
    make_buy_default: MakeBuy | None = None
    usually_modelled: bool | None = None
    buy_as_assembly: bool | None = None
    description_template: str | None = None
    standard_families: list[str] = Field(default_factory=list)
    material_default_profile_key: str | None = None
    unspsc: Annotated[str | None, Field(pattern=r"^\d{8}$")] = None
    note: str | None = None


class ResolvedClass(Strict):
    """A class with the taxonomy defaults applied. This is what is stored and searched."""

    code: str
    noun: str
    modifiers: list[str]
    uom: str
    make_buy_default: MakeBuy
    usually_modelled: bool
    buy_as_assembly: bool
    description_template: str
    standard_families: list[str]
    material_default_profile_key: str | None
    unspsc: str | None
    note: str | None
    parent: str | None


class Taxonomy(Strict):
    template_fields: list[str] = Field(min_length=1)
    default_template: str
    defaults: TaxonomyDefaults
    classes: list[ClassDef] = Field(min_length=1)

    def resolved(self) -> list[ResolvedClass]:
        out: list[ResolvedClass] = []
        for c in self.classes:
            parent = c.code.rsplit(".", 1)[0] if "." in c.code else None
            out.append(
                ResolvedClass(
                    code=c.code,
                    noun=c.noun,
                    modifiers=c.modifiers,
                    uom=c.uom or self.defaults.uom,
                    make_buy_default=c.make_buy_default or self.defaults.make_buy_default,
                    usually_modelled=(
                        self.defaults.usually_modelled
                        if c.usually_modelled is None
                        else c.usually_modelled
                    ),
                    buy_as_assembly=(
                        self.defaults.buy_as_assembly
                        if c.buy_as_assembly is None
                        else c.buy_as_assembly
                    ),
                    description_template=c.description_template or self.default_template,
                    standard_families=c.standard_families,
                    material_default_profile_key=c.material_default_profile_key,
                    unspsc=c.unspsc,
                    note=c.note,
                    parent=parent,
                )
            )
        return out


# --- materials.yaml ---------------------------------------------------------------------------


class Material(Strict):
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    family: str
    display: str
    designations: dict[str, list[str]] = Field(default_factory=dict)
    density_g_cm3: Annotated[float, Field(gt=0.0, lt=25.0)]
    density_source: str = Field(min_length=3)
    uses: list[str] = Field(default_factory=list)
    finishes: list[str] = Field(default_factory=list)
    verify: bool


class MaterialsFile(Strict):
    families: list[str] = Field(min_length=1)
    designation_systems: list[str] = Field(min_length=1)
    materials: list[Material] = Field(min_length=1)


# --- standards/*.yaml -------------------------------------------------------------------------

Cell = str | float | int


class StandardTable(Strict):
    standard: str
    key: Annotated[str, Field(pattern=r"^[A-Z0-9_]+$")]
    aliases: list[str] = Field(default_factory=list)
    family: str
    class_: Annotated[str, Field(alias="class", pattern=CLASS_CODE)]
    edition: str | None = None
    tolerance_mm: dict[str, float]
    dims: list[str] = Field(min_length=1)  # numeric columns every row must have; dims[0] = nominal
    monotonic: list[str] = Field(default_factory=list)  # non-decreasing with the nominal
    group_by: str | None = None  # monotonicity is checked inside each group
    expected_sizes: list[str] = Field(default_factory=list)  # completeness check
    lengths_mm: list[float] = Field(default_factory=list)
    sizes: list[dict[str, Cell]] = Field(min_length=1)
    source: str = Field(min_length=10)
    verify: bool
    note: str | None = None


# --- synonyms/*.yaml --------------------------------------------------------------------------


class SynonymEntry(Strict):
    tokens: list[str] = Field(min_length=1)
    class_: Annotated[str, Field(alias="class", pattern=CLASS_CODE)]
    weight: Annotated[float, Field(ge=0.0, le=1.0)]
    kind: Literal["noun", "abbreviation", "vendor", "phrase"] = "noun"
    context: dict[str, list[str]] | None = None  # e.g. {parent_class_is: [...]}


class SizePattern(Strict):
    id: str
    regex: str
    yields: dict[str, int]  # name -> capture group index
    example: str

    @field_validator("regex")
    @classmethod
    def _compiles(cls, v: str) -> str:
        re.compile(v)
        return v


class VendorPrefix(Strict):
    vendor: str
    regex: str
    example: str
    note: str

    @field_validator("regex")
    @classmethod
    def _compiles(cls, v: str) -> str:
        re.compile(v)
        return v


class SynonymFile(Strict):
    lang: Lang
    domain: str
    entries: list[SynonymEntry] = Field(default_factory=list)
    patterns: list[SizePattern] = Field(default_factory=list)
    prefixes: list[VendorPrefix] = Field(default_factory=list)


# --- units.yaml -------------------------------------------------------------------------------


class UnitDef(Strict):
    code: str
    dimension: Literal["count", "length", "mass", "volume", "area"]
    aliases: list[str]
    to_base: float  # factor to the dimension's base unit (EA, m, kg, l, m2)


class UnitsFile(Strict):
    units: list[UnitDef] = Field(min_length=1)


# --- headers.yaml -----------------------------------------------------------------------------

ValueKind = Literal[
    "positive_number",
    "positive_integer",
    "text",
    "long_text",
    "refdes_list",
    "part_number_like",
    "unit_token",
    "level_dotted",
    "any",
]


class HeaderPattern(Strict):
    lang: Lang
    regex: str

    @field_validator("regex")
    @classmethod
    def _compiles(cls, v: str) -> str:
        re.compile(v)
        return v


class HeaderField(Strict):
    required: bool = False
    header_patterns: list[HeaderPattern]
    value_kind: ValueKind
    value_min_share: Annotated[float, Field(ge=0.0, le=1.0)] = 0.8
    note: str | None = None


class HeaderScoring(Strict):
    header_match_weight: float
    value_match_weight: float
    accept_threshold: float
    ambiguity_margin: float


class HeadersFile(Strict):
    header_row_min_cells: int
    scoring: HeaderScoring
    fields: dict[str, HeaderField]


# --- profiles/default.yaml --------------------------------------------------------------------


class Numbering(Strict):
    scheme: str
    prefixes: dict[str, str]
    revision_start: str


class Descriptions(Strict):
    case: Literal["upper", "lower", "title", "as_is"]
    style: Literal["noun_first", "free"]
    separator: str


class FindNumbers(Strict):
    start: int
    step: int
    standard_parts_last: bool


class ProfileDefault(Strict):
    material: str
    finish: str | None = None
    label: str | None = None


class Profile(Strict):
    name: str
    numbering: Numbering
    descriptions: Descriptions
    find_numbers: FindNumbers
    defaults: dict[str, ProfileDefault]
    export: dict[str, list[str]]
    standards_preference: list[str]


# --- confidence.yaml --------------------------------------------------------------------------


class ConfidenceFile(Strict):
    source_base: dict[str, Annotated[float, Field(ge=0.0, le=1.0)]]
    thresholds: dict[str, Annotated[float, Field(ge=0.0, le=1.0)]]
    bands: dict[str, Annotated[float, Field(ge=0.0, le=1.0)]]
    classification_ladder: dict[str, Annotated[float, Field(ge=0.0, le=1.0)]]
    llm: dict[str, float]
    tolerances: dict[str, float]
    scoring: dict[str, str]
    # Shapes measured from meshes (converted SolidWorks files) are less certain than exact solids.
    mesh: dict[str, Annotated[float, Field(ge=0.0, le=1.0)]] = Field(default_factory=dict)


# --- cad_props.yaml ---------------------------------------------------------------------------


class CadPropsFile(Strict):
    """Which property names in native CAD files mean what (docs/03 §2).

    `canonical` maps our key (part_number, description, material, class) to the property names
    CAD tools use for it, in any language. Names are compared after lower-casing and dropping
    spaces and punctuation, and a `Group.Name` form is matched on its last part.
    """

    canonical: dict[str, list[str]] = Field(default_factory=dict)
    ignore_values: list[str] = Field(default_factory=list)


# --- rules/*.yaml -----------------------------------------------------------------------------

Primitive = Literal[
    "name_matches",
    "meta_equals",
    "feature_between",
    "feature_ratio",
    "std_designation_family",
    "class_is",
    "parent_class_is",
    "product_has_class",
    "not_has_class",
    "count_between",
    "material_in",
    "process_hint_in",
    "source_is",
    "expr",
    "all",
    "any",
    "not",
]
Action = Literal[
    "class",
    "material",
    "designation",
    "flag",
    "question",
    "suggest",
    "exclude",
    "group_as",
    "make_buy",
    "description_part",
]


class Rule(Strict):
    id: Annotated[str, Field(pattern=RULE_ID)]
    stage: Stage
    when: dict[str, object]
    then: dict[str, object]
    weight: Annotated[float, Field(ge=0.0, le=1.0)]
    note: str = Field(min_length=5)

    @model_validator(mode="after")
    def _closed_vocab(self) -> "Rule":
        bad = sorted(k for k in _walk_keys(self.when) if k not in _PRIMS)
        if bad:
            raise ValueError(f"unknown condition primitive(s): {bad}")
        bad_actions = sorted(k for k in self.then if k not in _ACTIONS)
        if bad_actions:
            raise ValueError(f"unknown action(s): {bad_actions}")
        return self


_PRIMS = set(Primitive.__args__)  # type: ignore[attr-defined]
_ACTIONS = set(Action.__args__)  # type: ignore[attr-defined]


def _walk_keys(cond: object) -> list[str]:
    """Primitive names used in a (possibly nested all/any/not) condition."""
    keys: list[str] = []
    if isinstance(cond, dict):
        for k, v in cond.items():
            keys.append(str(k))
            if k in ("all", "any", "not"):
                keys.extend(_walk_keys(v))
    elif isinstance(cond, list):
        for item in cond:
            keys.extend(_walk_keys(item))
    return keys


class RuleFile(Strict):
    stage: Stage
    id_prefix: Annotated[str, Field(pattern=r"^R-[A-Z]{3}$")]
    rules: list[Rule] = Field(default_factory=list)


# --- archetypes/*.yaml ------------------------------------------------------------------------


class ArchetypeItem(Strict):
    class_: Annotated[str, Field(alias="class", pattern=CLASS_CODE)]
    min_count: int = Field(ge=1)
    note: str


class Archetype(Strict):
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    display: str
    match_keywords: list[str] = Field(min_length=1)
    expected: list[ArchetypeItem] = Field(min_length=1)
    source: str
    verify: bool
