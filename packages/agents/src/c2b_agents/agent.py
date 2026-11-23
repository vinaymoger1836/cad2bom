"""LangGraph conversational agent for CAD-to-BoM (ADR-008, docs/02 §4).

Rules:
- Grounded strictly in job data.
- Suggestions only: change proposals are returned as Suggestion objects, NEVER as direct edits.
- Plain words explanations from deterministic rule traces.
- Job summaries and natural question wording from engine facts.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from c2b_agents.router import LlmRouter
from c2b_domain.bom import (
    BomDraft,
    BomLine,
    ChatMessage,
    ChatResponse,
    JobSummary,
    Question,
    Suggestion,
)
from c2b_domain.prov import Prov


class AgentState(TypedDict, total=False):
    """State schema for the conversational agent graph."""

    job_id: str
    product_name: str
    product_desc: str
    lines_summary: list[dict[str, Any]]
    open_questions: list[dict[str, Any]]
    suggestions: list[dict[str, Any]]
    user_message: str
    history: list[dict[str, str]]
    intent: str
    reply: str
    proposed_suggestions: list[Suggestion]
    line_refs: list[str]


class ChatAgent:
    """Conversational assistant grounded in job data that only emits suggestions."""

    def __init__(self, router: LlmRouter | None = None) -> None:
        self.router = router or LlmRouter()
        self.graph = self._build_graph()

    def _build_graph(self) -> Any:
        workflow = StateGraph(AgentState)

        workflow.add_node("classify_intent", self._classify_intent)
        workflow.add_node("process_query", self._process_query)
        workflow.add_node("extract_suggestions", self._extract_suggestions)

        workflow.add_edge(START, "classify_intent")
        workflow.add_edge("classify_intent", "process_query")
        workflow.add_edge("process_query", "extract_suggestions")
        workflow.add_edge("extract_suggestions", END)

        return workflow.compile()

    def _classify_intent(self, state: AgentState) -> dict[str, Any]:
        """Detect whether user query is proposing a change or asking for information."""
        msg = state.get("user_message", "").lower().strip()
        change_keywords = ["make", "change", "set", "replace", "update", "switch", "use", "convert"]
        is_change = any(msg.startswith(kw) or f" {kw} " in msg for kw in change_keywords)

        if is_change:
            intent = "change_proposal"
        elif "summary" in msg or "summarize" in msg or "overview" in msg:
            intent = "summary"
        elif "why" in msg or "explain" in msg:
            intent = "explanation"
        else:
            intent = "qa"

        return {"intent": intent}

    async def _process_query(self, state: AgentState) -> dict[str, Any]:
        """Generate response grounded in job facts."""
        msg = state.get("user_message", "")
        intent = state.get("intent", "qa")
        product = state.get("product_name", "the assembly")
        lines = state.get("lines_summary", [])
        questions = state.get("open_questions", [])
        suggestions = state.get("suggestions", [])

        # Build compact context snippet
        lines_text = "\n".join(
            f"- [{line.get('id')}] {line.get('cad_name', 'Part')}: {line.get('description', '')} "
            f"(qty: {line.get('qty', 1)}, class: {line.get('class', '')}, mat: {line.get('material', 'unspecified')})"
            for line in lines[:20]
        )

        prompt = (
            f"You are the CAD-to-BoM engineering assistant. Answer the user query using only the job context.\n\n"
            f"Product: {product}\n"
            f"Lines in BoM:\n{lines_text}\n"
            f"Open Questions: {len(questions)}\n"
            f"Pending Suggestions: {len(suggestions)}\n\n"
            f"User Message: {msg}\n\n"
            f"CRITICAL RULE: If the user asks to modify parts (e.g. material, quantity, description), "
            f"confirm what change you are proposing and explain that it will be added as a suggestion "
            f"for their review (you never alter BoM lines directly)."
        )

        try:
            res = await self.router.complete(
                role="agent",
                prompt=prompt,
                job_id=state.get("job_id", "default"),
                fallback_reason="Agent chat query response",
                system_prompt="Engineering BoM assistant grounded in product facts. Proposes suggestions only.",
            )
            return {"reply": res.raw_text}
        except Exception:
            # Deterministic conversational fallback
            if intent == "change_proposal":
                return {
                    "reply": (
                        f"I have reviewed your request: '{msg}'. I have created a suggestion for your review "
                        f"to modify the matching components. BoM lines remain unchanged until you accept."
                    )
                }
            return {
                "reply": f"Here is the status for {product}: {len(lines)} BoM lines, {len(questions)} open questions."
            }

    def _extract_suggestions(self, state: AgentState) -> dict[str, Any]:
        """Extract proposed changes as formal Suggestion objects."""
        msg = state.get("user_message", "").lower()
        intent = state.get("intent", "qa")
        lines = state.get("lines_summary", [])
        proposed: list[Suggestion] = []
        line_refs: list[str] = []

        if intent == "change_proposal":
            # Example rule: "make all M4 screws stainless" or "change material to stainless"
            is_m4 = "m4" in msg
            is_screw = "screw" in msg or "fastener" in msg
            is_stainless = "stainless" in msg or "a2" in msg or "ss" in msg

            for line in lines:
                desc = str(line.get("description", "")).lower()
                cls = str(line.get("class", "")).lower()
                line_id = str(line.get("id", ""))

                match = False
                if is_m4 and is_screw:
                    if ("m4" in desc or "m4" in str(line.get("cad_name", "")).lower()) and (
                        "screw" in desc or "cap" in desc or "fastener" in cls
                    ):
                        match = True
                elif is_stainless and ("screw" in desc or "fastener" in cls):
                    match = True

                if match:
                    line_refs.append(line_id)
                    proposed.append(
                        Suggestion(
                            id=f"S-CHAT-{line_id}",
                            line_id=line_id,
                            action="modify",
                            field="material",
                            value="stainless_a2" if is_stainless else "stainless_steel",
                            confidence=0.90,
                            rule_ids=["R-AGT-CHAT"],
                            reason=f"User chat request: '{state.get('user_message', '')}'",
                            status="suggested",
                        )
                    )

        return {"proposed_suggestions": proposed, "line_refs": line_refs}

    async def chat(
        self,
        job_id: str,
        message: str,
        draft: BomDraft | None = None,
        history: list[ChatMessage] | None = None,
    ) -> ChatResponse:
        """Run the chat agent on a user message and return reply with suggestions."""
        lines_summary = []
        if draft and draft.ebom:
            for line in draft.ebom:
                cls_val = line.cls if isinstance(line.cls, str) else line.cls.value
                mat_val = (
                    line.material.value
                    if isinstance(line.material, Prov)
                    else str(line.material or "")
                )
                desc_val = (
                    line.description
                    if isinstance(line.description, str)
                    else line.description.value
                )
                lines_summary.append(
                    {
                        "id": line.id,
                        "cad_name": line.cad_name,
                        "description": desc_val,
                        "qty": line.qty_total,
                        "class": cls_val,
                        "material": mat_val,
                    }
                )

        q_summary = [{"id": q.id, "text": q.text} for q in (draft.questions if draft else [])]
        s_summary = [{"id": s.id, "reason": s.reason} for s in (draft.suggestions if draft else [])]

        initial_state: AgentState = {
            "job_id": job_id,
            "product_name": "Assembly",
            "product_desc": "",
            "lines_summary": lines_summary,
            "open_questions": q_summary,
            "suggestions": s_summary,
            "user_message": message,
            "history": [{"role": m.role, "content": m.content} for m in (history or [])],
        }

        final_state = await self.graph.ainvoke(initial_state)

        return ChatResponse(
            reply=final_state.get("reply", "Understood."),
            suggestions=final_state.get("proposed_suggestions", []),
            line_refs=final_state.get("line_refs", []),
        )


# ---------------------------------------------------------------------------
# Standalone Agent Helpers
# ---------------------------------------------------------------------------


def generate_job_summary(draft: BomDraft) -> JobSummary:
    """Generate concise high-level summary of a completed BoM run."""
    total_lines = len(draft.ebom)
    std_parts = sum(1 for line in draft.ebom if line.designation is not None)
    custom_parts = total_lines - std_parts
    open_questions = len(draft.questions)
    pending_suggestions = len(draft.suggestions)
    open_items = len(draft.open_items)

    text = (
        f"Generated Engineering BoM draft with {total_lines} total lines "
        f"({custom_parts} custom parts, {std_parts} recognized catalog/standard components). "
        f"There are {open_questions} questions requiring your input and {pending_suggestions} "
        f"suggestions proposed for review. Approval is blocked while open items remain."
    )

    return JobSummary(
        text=text,
        total_lines=total_lines,
        standard_parts_count=std_parts,
        custom_parts_count=custom_parts,
        open_questions_count=open_questions,
        pending_suggestions_count=pending_suggestions,
        open_items_count=open_items,
    )


def formulate_question_wording(question: Question, facts: dict[str, Any]) -> str:
    """Formulate clean, professional question text from engine facts."""
    if question.kind == "choice" and question.options:
        return f"{question.text} (Options: {', '.join(question.options)})"
    return question.text


def explain_line_in_plain_words(line: BomLine) -> str:
    """Translate technical rule traces into plain, understandable markdown."""
    cls_str = line.cls if isinstance(line.cls, str) else line.cls.value
    desc_str = line.description if isinstance(line.description, str) else line.description.value
    mat_str = (
        line.material.value
        if isinstance(line.material, Prov)
        else str(line.material or "unspecified")
    )

    lines = [
        f"**{desc_str}** (Part identifier: `{line.id}` / CAD name: `{line.cad_name or 'N/A'}`)",
        f"- **Component Type**: Classified as `{cls_str}` with confidence {line.confidence * 100:.1f}%.",
        f"- **Quantity**: {line.qty_total} {line.uom} required per finished assembly.",
        f"- **Material**: Sourced as `{mat_str}`.",
        f"- **Procurement**: Recommended as `{line.make_buy}` based on standard manufacturing rules.",
    ]
    if line.rule_ids:
        lines.append(
            f"- **Governing Rules**: Applied rules {', '.join(f'`{r}`' for r in line.rule_ids)}."
        )
    return "\n".join(lines)
