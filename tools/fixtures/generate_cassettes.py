"""Record the golden LLM cassettes (B4 L3, B6 L1/L4, chat agent) through the real flows.

Cassettes are keyed by role + prompt hash (CLAUDE.md rule 9). To get keys that really match, this
script runs the real pipeline code (`resolve_draft_with_ladder`, `ChatAgent`) with the router in
record mode and replaces ONLY the provider call with a scripted answer, so every prompt is built
by the production code and hashed by the production router. The answers are authored here, not
produced by a model: each cassette is stamped `model: "fixture/scripted"` so nobody mistakes it
for a real recording. Re-record against real models with GROQ_API_KEY / GEMINI_API_KEY set and
C2B_CASSETTE_MODE=record when keys are available.

Run from the repo root:  uv run python tools/fixtures/generate_cassettes.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from pydantic import BaseModel

from c2b_agents.agent import ChatAgent
from c2b_agents.config import get_agent_config
from c2b_agents.pipeline import resolve_draft_with_ladder
from c2b_agents.router import LlmRouter, RouterResult
from c2b_cad_worker.extract import extract_step
from c2b_library.loader import load_library
from c2b_rules.engine import build_ebom

CASSETTE_DIR = Path("cassettes")
MODEL = "fixture/scripted"

B6_CLASSES = {
    "Body1": ("moulded.housing.top", "Thin-wall open shell (1.8 mm): top housing"),
    "Body2": ("moulded.housing.bottom", "Thin-wall open shell (1.8 mm): bottom housing"),
    "Body3": ("electrical.pcb", "Flat plate with mounting holes: printed circuit board"),
    "Body4": ("moulded.knob", "Rotational part with a bore: rotary knob"),
    "Body5": ("moulded.button", "Small cylindrical solid: push button"),
    "Body6": ("spring.compression", "Helical wire: battery contact spring"),
    "Body7": ("sheetmetal.clip", "Formed sheet-metal strip: battery contact clip"),
    "Body8": ("seal.gasket.flat", "Flat perimeter frame: sealing gasket"),
    "Body9": ("electrical.battery.li_ion", "18 x 65 mm cylinder: 18650 Li-ion cell"),
}
B4_FIELDS = {
    "Pos": "find_no",
    "Teilenummer": "part_number",
    "Item": "description",
    "Menge": "quantity",
    "Unit": "uom",
    "Designator": "refdes",
    "Hersteller": "manufacturer",
    "MPN": "mpn",
    "Bemerkung": "notes",
}
L4_CANDIDATES = [
    ("label.rating", "Serial and rating label on the housing"),
    ("documentation.manual", "Operating and calibration instructions"),
    ("packaging.carton", "Retail carton for the handheld unit"),
    ("fastener.screw.socket_head", "Screws closing the two housing halves"),
]
CHAT_REPLY = (
    "I have prepared suggestions to change the material of the M4 screw lines to stainless steel "
    "(A2). Your BoM lines stay unchanged until you review and accept each suggestion."
)


def script(role: str, prompt: str, schema: type[BaseModel] | None) -> tuple[Any, int, int]:
    """The authored answer for one prompt: (response, prompt_tokens, completion_tokens)."""
    name = schema.__name__ if schema else ""
    if name == "L1Output":
        part = re.search(r"Part Name: (\S+)", prompt)
        cls, reason = B6_CLASSES[part.group(1)] if part else ("moulded.housing", "General part")
        return {"cls": cls, "confidence": 0.70, "reason": reason}, 220, 40
    if name == "L3Output":
        return (
            {
                "mappings": B4_FIELDS,
                "reason": "'Item' also looks like a find number, but its cells are long part texts",
            },
            180,
            65,
        )
    if name == "L4Output":
        return {"candidates": [{"cls": c, "reason": r} for c, r in L4_CANDIDATES]}, 240, 85
    if role == "agent":
        return CHAT_REPLY, 310, 55
    raise RuntimeError(f"No scripted answer for role={role} schema={name!r}")


class ScriptedRouter(LlmRouter):
    """The production router with only the provider call replaced by `script`."""

    async def _call_provider_with_retry(  # type: ignore[override]
        self,
        primary_model: str,
        fallback_model: str | None,
        role: str,
        prompt: str,
        schema: type[BaseModel] | None = None,
        system_prompt: str | None = None,
    ) -> RouterResult:
        response, p_tok, c_tok = script(role, prompt, schema)
        raw = json.dumps(response) if isinstance(response, dict) else str(response)
        parsed = schema.model_validate(response) if schema else None
        return RouterResult(
            raw_text=raw,
            parsed=parsed,
            role=role,
            model=MODEL,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            total_tokens=p_tok + c_tok,
            latency_ms=100,
        )


def new_router() -> ScriptedRouter:
    cfg = dataclasses.replace(get_agent_config(), cassette_mode="record", cassette_dir=CASSETTE_DIR)
    return ScriptedRouter(config=cfg)


async def record_all() -> None:
    library, _ = load_library(Path("library"))
    assert library is not None

    # B4: the ambiguous 'Item' column through L3
    b4 = build_ebom(
        None, library, "B4", bom_file_path=Path("golden/B4_power_bank/input/supplier_bom.xlsx")
    )
    await resolve_draft_with_ladder(
        b4,
        library,
        new_router(),
        extract=None,
        product_desc="Power bank assembly with Li-ion battery",
    )

    # B6: nine unknown parts through L1, then L4 gap candidates
    b6_dir = Path("golden/B6_unknown/input")
    desc = (b6_dir / "details.txt").read_text(encoding="utf-8").strip()
    cad = extract_step(b6_dir / "unknown_product.step")
    b6 = build_ebom(cad, library, "B6", details_path=b6_dir / "details.txt", assign_numbers=True)
    await resolve_draft_with_ladder(
        b6, library, new_router(), extract=cad, product_desc=desc, run_l4_gap=True
    )

    # Chat agent: "make all M4 screws stainless" on B2
    b2_cad = extract_step(Path("golden/B2_bracket/input/bracket_assembly.step"))
    b2 = build_ebom(b2_cad, library, "B2")
    await ChatAgent(new_router()).chat(
        job_id="B2", message="make all M4 screws stainless", draft=b2
    )


def main() -> None:
    # Keep the hand-made vision file read by the photo stub; everything else is re-recorded.
    for sub in ("b4", "b6", "agent"):
        shutil.rmtree(CASSETTE_DIR / sub, ignore_errors=True)
    for stale in CASSETTE_DIR.glob("*.json"):
        stale.unlink()
    asyncio.run(record_all())
    files = sorted(CASSETTE_DIR.glob("*.json"))
    print(f"Recorded {len(files)} cassettes under {CASSETTE_DIR}/ (model {MODEL!r}).")


if __name__ == "__main__":
    main()
