"""Generate golden cassette recordings for B3, B4, B6, and agent tests."""

import json
from pathlib import Path

from c2b_agents.cassettes import Cassette, CassetteStore
from c2b_agents.schemas import (
    get_taxonomy_codes,
)
from c2b_cad_worker.extract import extract_step
from c2b_library.loader import load_library


def generate_all_cassettes() -> None:
    store = CassetteStore("cassettes")
    library, _ = load_library(Path("library"))
    valid_classes = get_taxonomy_codes(library)

    # -------------------------------------------------------------------------
    # 1. B3 Vision Cassette
    # -------------------------------------------------------------------------
    b3_dir = Path("cassettes/b3")
    b3_dir.mkdir(parents=True, exist_ok=True)
    b3_vision_data = {
        "findings": [
            {
                "description": "Ceiling fan motor housing and 3 blades visible in photo",
                "class": "electrical.motor.ac_induction",
                "count": 1,
                "detected_text": ["230V AC", "75W"],
                "confidence": 0.35,
            }
        ]
    }
    (b3_dir / "vision_photo.json").write_text(
        json.dumps(b3_vision_data, indent=2) + "\n", encoding="utf-8"
    )
    print("Recorded B3 vision cassette.")

    # -------------------------------------------------------------------------
    # 2. B4 L3 Header Mapping Cassette
    # -------------------------------------------------------------------------
    b4_dir = Path("cassettes/b4")
    b4_dir.mkdir(parents=True, exist_ok=True)

    header_row = [
        "Pos",
        "Teilenummer",
        "Item",
        "Menge",
        "Unit",
        "Designator",
        "Hersteller",
        "MPN",
        "Bemerkung",
    ]
    sample_rows = [
        [
            "1",
            "PB-0001",
            "Li-ion Battery Pack 10000mAh",
            "1",
            "pcs",
            "BT1",
            "PowerMax",
            "PM-10000",
            "Main battery",
        ],
        [
            "2",
            "PB-0002",
            "Top Enclosure Shell ABS",
            "1",
            "pcs",
            "",
            "PlastiTech",
            "PT-TOP-01",
            "UL94-V0",
        ],
        [
            "3",
            "PB-0003",
            "Bottom Enclosure Shell ABS",
            "1",
            "pcs",
            "",
            "PlastiTech",
            "PT-BOT-01",
            "UL94-V0",
        ],
        [
            "4",
            "PB-0004",
            "Main Controller PCBA",
            "1",
            "pcs",
            "PCB1",
            "TechDesign",
            "TD-PB-V2",
            "SMT",
        ],
        [
            "5",
            "PB-0005",
            "USB-C Interface Port",
            "2",
            "pcs",
            "J1, J2",
            "Amphenol",
            "12401610E402A",
            "Dual port",
        ],
    ]
    ambiguous_column = "Item"
    valid_fields = [
        "find_no",
        "part_number",
        "description",
        "qty",
        "uom",
        "level",
        "refdes",
        "material",
        "manufacturer",
        "mpn",
        "notes",
        "unmapped",
    ]

    samples_fmt = "\n".join(
        f"Row {i + 1}: " + " | ".join(str(cell) for cell in row)
        for i, row in enumerate(sample_rows[:5])
    )
    b4_prompt = (
        f"You are a tabular BoM mapping specialist. Map the spreadsheet columns to canonical BoM fields.\n\n"
        f"Header Row: {' | '.join(header_row)}\n"
        f"Sample Data Rows:\n{samples_fmt}\n\n"
        f"Ambiguous Column to Resolve: '{ambiguous_column}'\n"
        f"Allowed Canonical Fields: {', '.join(valid_fields)}\n\n"
        f"Return JSON conforming to schema with 'mappings' (dict of col -> field) and 'reason'."
    )
    b4_key = store.make_key("extractor", b4_prompt, "L3Output")
    b4_cassette = Cassette(
        key=b4_key,
        role="extractor",
        model="groq/llama-3.1-8b-instant",
        prompt_summary="B4 header mapping for ambiguous column 'Item'",
        response={
            "mappings": {
                "Item": "description",
                "Pos": "find_no",
                "Teilenummer": "part_number",
                "Menge": "quantity",
                "Unit": "uom",
                "Designator": "refdes",
                "Hersteller": "manufacturer",
                "MPN": "mpn",
                "Bemerkung": "notes",
            },
            "reason": "header 'Item' scores for find_no (also claimed by 'Pos'); values are long text descriptions",
        },
        prompt_tokens=180,
        completion_tokens=65,
        total_tokens=245,
        latency_ms=120,
    )
    store.save(b4_cassette, subdir="b4")
    print(f"Recorded B4 L3 cassette ({b4_key}).")

    # -------------------------------------------------------------------------
    # 3. B6 Unknown Product: L1 Reasoner Cassettes for Body1..Body9
    # -------------------------------------------------------------------------
    b6_dir = Path("cassettes/b6")
    b6_dir.mkdir(parents=True, exist_ok=True)

    b6_step = Path("golden/B6_unknown/input/unknown_product.step")
    product_desc = (
        "Handheld soil-moisture controller with a rotary knob, one button and a rechargeable cell."
    )
    cad = extract_step(b6_step)

    expected_b6_classes = {
        "Body1": (
            "moulded.housing.top",
            "Thin-wall open shell geometry (1.8mm) representing top housing of handheld controller",
        ),
        "Body2": (
            "moulded.housing.bottom",
            "Thin-wall open shell geometry (1.8mm) representing bottom housing of handheld controller",
        ),
        "Body3": (
            "electrical.pcb",
            "Flat plate with mounting holes matching printed circuit board",
        ),
        "Body4": ("moulded.knob", "Rotational component with bore matching controller rotary knob"),
        "Body5": ("moulded.button", "Small cylindrical solid matching push button actuator"),
        "Body6": (
            "spring.compression",
            "Helical wire geometry matching battery contact compression spring",
        ),
        "Body7": ("sheetmetal.clip", "Formed sheet metal strip matching battery contact clip"),
        "Body8": ("seal.gasket.flat", "Flat perimeter frame matching moisture sealing gasket"),
        "Body9": (
            "electrical.battery.li_ion",
            "18x65mm cylindrical volume matching 18650 Li-ion rechargeable battery cell",
        ),
    }

    sibling_classes = [cls for (cls, _) in expected_b6_classes.values()]

    for node in cad.nodes:
        if node.kind != "part":
            continue
        feat = cad.shapes.get(node.shape_id) if node.shape_id else None
        feat_summary = ""
        if feat:
            feat_summary = (
                f"size=({feat.obb_mm[0]:.1f}, {feat.obb_mm[1]:.1f}, {feat.obb_mm[2]:.1f}) mm, "
                f"vol={feat.volume_mm3:.1f} mm3, rot_sym={feat.rot_symmetry:.2f}, "
                f"const_thick={feat.const_thickness_mm}, thin_plate={feat.thin_plate}, "
                f"helix={feat.helix_evidence}"
            )

        chosen_cls, reason_text = expected_b6_classes.get(
            node.name, ("moulded.housing", "General enclosure part")
        )

        l1_prompt = (
            f"You are a CAD classification reasoner. Classify this mechanical part into exactly one "
            f"taxonomy class code from the allowed taxonomy.\n\n"
            f"Part Name: {node.name}\n"
            f"Parent Assemblies: Root\n"
            f"Sibling Classes: {', '.join(sibling_classes)}\n"
            f"Geometric Features: {feat_summary}\n"
            f"Product Context: {product_desc}\n"
            f"Candidate Classes from Rules: None\n\n"
            f"Allowed Taxonomy Codes: {', '.join(valid_classes)}\n\n"
            f"Return JSON strictly conforming to schema with fields 'cls', 'confidence' (0.0-1.0), and 'reason'."
        )

        key = store.make_key("reasoner", l1_prompt, "L1Output")
        cassette = Cassette(
            key=key,
            role="reasoner",
            model="groq/llama-3.3-70b-versatile",
            prompt_summary=f"B6 L1 classification for {node.name}",
            response={
                "cls": chosen_cls,
                "confidence": 0.70,
                "reason": reason_text,
            },
            prompt_tokens=220,
            completion_tokens=40,
            total_tokens=260,
            latency_ms=250,
        )
        store.save(cassette, subdir="b6")
        print(f"Recorded B6 L1 cassette for {node.name} ({key} -> {chosen_cls}).")

    # -------------------------------------------------------------------------
    # 4. B6 L4 Gap Candidates Cassette
    # -------------------------------------------------------------------------
    l4_prompt = (
        f"You are a design-for-manufacturing expert. Identify missing components commonly required "
        f"for this product that are not yet in the BoM.\n\n"
        f"Product Description: {product_desc}\n"
        f"Current BoM Component Classes: {', '.join(sibling_classes)}\n\n"
        f"Allowed Taxonomy Classes: {', '.join(valid_classes)}\n\n"
        f"Return JSON strictly conforming to schema with at most 8 'candidates' (each having 'cls' and 'reason')."
    )
    l4_key = store.make_key("reasoner", l4_prompt, "L4Output")
    l4_cassette = Cassette(
        key=l4_key,
        role="reasoner",
        model="groq/llama-3.3-70b-versatile",
        prompt_summary="B6 L4 gap candidates for handheld soil-moisture controller",
        response={
            "candidates": [
                {
                    "cls": "label.rating",
                    "reason": "Serial and rating label required on handheld controller housing",
                },
                {
                    "cls": "documentation.manual",
                    "reason": "Operating instructions and calibration guide for soil moisture meter",
                },
                {
                    "cls": "packaging.carton",
                    "reason": "Retail protective carton for packaging the handheld unit",
                },
                {
                    "cls": "fastener.screw.socket_head",
                    "reason": "Assembly fasteners to secure top and bottom enclosure halves",
                },
            ]
        },
        prompt_tokens=240,
        completion_tokens=85,
        total_tokens=325,
        latency_ms=280,
    )
    store.save(l4_cassette, subdir="b6")
    print(f"Recorded B6 L4 gap cassette ({l4_key}).")

    # -------------------------------------------------------------------------
    # 5. Agent Chat Cassette ("make all M4 screws stainless")
    # -------------------------------------------------------------------------
    agent_dir = Path("cassettes/agent")
    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_cassette = Cassette(
        key="agent_m4_stainless",
        role="agent",
        model="groq/llama-3.3-70b-versatile",
        prompt_summary="Chat agent response for make all M4 screws stainless",
        response=(
            "I have processed your request to make all M4 screws stainless. I have prepared suggestions "
            "for the 4 matching M4 screw lines to update their material to stainless steel (A2). "
            "In accordance with our governance rules, your BoM lines remain unchanged until you "
            "review and accept these suggestions."
        ),
        prompt_tokens=310,
        completion_tokens=55,
        total_tokens=365,
        latency_ms=190,
    )
    store.save(agent_cassette, subdir="agent")
    print("Recorded Agent chat cassette.")


if __name__ == "__main__":
    generate_all_cassettes()
