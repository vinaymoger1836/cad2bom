"""c2b_agents: LLM router, fallback ladder L1-L6, LangGraph agent, and cassettes.

Strictly adheres to:
- Non-negotiable Rule 1: Rules first, LLM last.
- Non-negotiable Rule 2: The LLM never produces numbers that end up in the BoM.
- ADR-001: Constrained fallback only.
- ADR-008: LangGraph only for the chat agent.
- ADR-009: LiteLLM router on Groq and Gemini free tiers.
"""

import os

# Prevent LiteLLM from making slow/failing remote requests on import
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from c2b_agents.agent import ChatAgent
from c2b_agents.cassettes import Cassette, CassetteStore
from c2b_agents.config import AgentConfig, get_agent_config
from c2b_agents.ladder import (
    run_l1_classify,
    run_l2_material_family,
    run_l3_header_mapping,
    run_l4_gap_candidates,
    run_l5_description_wording,
    run_l6_datasheet_field,
)
from c2b_agents.pipeline import resolve_draft_with_ladder
from c2b_agents.router import LlmRouter

__all__ = [
    "AgentConfig",
    "Cassette",
    "CassetteStore",
    "ChatAgent",
    "LlmRouter",
    "get_agent_config",
    "resolve_draft_with_ladder",
    "run_l1_classify",
    "run_l2_material_family",
    "run_l3_header_mapping",
    "run_l4_gap_candidates",
    "run_l5_description_wording",
    "run_l6_datasheet_field",
]
