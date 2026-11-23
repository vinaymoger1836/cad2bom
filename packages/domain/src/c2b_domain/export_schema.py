"""Write JSON Schemas for the API models; `pnpm gen:types` turns them into TypeScript.

Output (git-ignored): packages/ts-types/schema/*.json, plus package.json and index.d.ts.
"""

import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from c2b_domain.bom import (
    BomDraft,
    BomLine,
    BomVersion,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ColumnMapping,
    DatasheetSpec,
    Designation,
    JobSummary,
    LlmCallRecord,
    MbomLine,
    PhotoFinding,
    Question,
    Suggestion,
    TableMapping,
    UserBomRow,
    ValidationIssue,
    ValidationReport,
)
from c2b_domain.cad import (
    CadExtractResult,
    CadNode,
    CadNotice,
    ShapeFeatures,
)
from c2b_domain.jobs import (
    JobCreate,
    JobCreated,
    JobEvent,
    JobOut,
    JobUpdate,
    ProblemDetail,
    ProductInfo,
    UploadOut,
)

MODELS: list[type[BaseModel]] = [
    ProductInfo,
    JobCreate,
    JobCreated,
    JobUpdate,
    JobOut,
    UploadOut,
    JobEvent,
    ProblemDetail,
    CadNode,
    ShapeFeatures,
    CadNotice,
    CadExtractResult,
    Designation,
    BomLine,
    MbomLine,
    BomVersion,
    ValidationIssue,
    ValidationReport,
    BomDraft,
    Question,
    Suggestion,
    ColumnMapping,
    TableMapping,
    UserBomRow,
    DatasheetSpec,
    PhotoFinding,
    LlmCallRecord,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    JobSummary,
]


REQUEST_MODELS: set[type[BaseModel]] = {ProductInfo, JobCreate, JobUpdate, ChatRequest}


def _repo_root() -> Path:
    # packages/domain/src/c2b_domain/export_schema.py -> repo root is 4 levels up
    return Path(__file__).resolve().parents[4]


def main(out_dir: str | None = None) -> None:
    target = Path(out_dir) if out_dir else _repo_root() / "packages" / "ts-types"
    schema_dir = target / "schema"
    schema_dir.mkdir(parents=True, exist_ok=True)
    for old in schema_dir.glob("*.json"):
        old.unlink()
    for model in MODELS:
        # Request bodies keep optional fields optional; responses list every field as present.
        mode: Literal["validation", "serialization"] = (
            "validation" if model in REQUEST_MODELS else "serialization"
        )
        schema = model.model_json_schema(mode=mode)
        (schema_dir / f"{model.__name__}.json").write_text(
            json.dumps(schema, indent=2) + "\n", encoding="utf-8"
        )
    # Named exports (not `export *`): nested models are emitted in several files and would clash.
    index = "".join(f'export type {{ {m.__name__} }} from "./src/{m.__name__}";\n' for m in MODELS)
    (target / "index.d.ts").write_text(index, encoding="utf-8")
    (target / "package.json").write_text(
        json.dumps(
            {
                "name": "@c2b/ts-types",
                "version": "0.0.0",
                "private": True,
                "description": "GENERATED from Pydantic models. Do not edit.",
                "types": "index.d.ts",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(MODELS)} schemas to {schema_dir}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
