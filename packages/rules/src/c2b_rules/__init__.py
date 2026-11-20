"""Deterministic BoM rules engine. Filled in from P3 onward.

Boundary: this package must never import an LLM client, `c2b_agents`, or anything that
reaches the network. The LLM is called only from `packages/agents` on the fallback ladder.
"""

__all__: list[str] = []
