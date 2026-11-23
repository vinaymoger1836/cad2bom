"""LiteLLM router: roles, rate limits, 429 backoff, fallback roles, budget (docs/02 §4)."""

from __future__ import annotations

import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from c2b_agents.cassettes import Cassette, CassetteStore
from c2b_agents.config import AgentConfig, Role, get_agent_config
from c2b_domain.bom import LlmCallRecord

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class BudgetExceededError(Exception):
    """Raised when an LLM call would exceed the per-job token budget."""

    def __init__(self, job_id: str, current: int, attempted: int, budget: int) -> None:
        super().__init__(
            f"Job {job_id} exceeded token budget: {current} + {attempted} > {budget} tokens"
        )
        self.job_id = job_id
        self.current = current
        self.budget = budget


class TokenBucket:
    """In-memory rate limiter tracking RPM and TPM per role."""

    def __init__(self, max_rpm: int, max_tpm: int) -> None:
        self.max_rpm = max_rpm
        self.max_tpm = max_tpm
        self.request_times: list[float] = []
        self.token_usage: list[tuple[float, int]] = []

    def check_and_record(self, tokens_estimate: int = 500) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0

        # Purge older than 1 minute
        self.request_times = [t for t in self.request_times if t > cutoff]
        self.token_usage = [(t, count) for (t, count) in self.token_usage if t > cutoff]

        current_rpm = len(self.request_times)
        current_tpm = sum(count for (_, count) in self.token_usage)

        if current_rpm >= self.max_rpm or (current_tpm + tokens_estimate) > self.max_tpm:
            return False

        self.request_times.append(now)
        self.token_usage.append((now, tokens_estimate))
        return True


class JobBudgetTracker:
    """Tracks cumulative token usage per job."""

    def __init__(self, default_budget: int = 40000) -> None:
        self.default_budget = default_budget
        self._usage: dict[str, int] = {}

    def get_usage(self, job_id: str) -> int:
        return self._usage.get(job_id, 0)

    def add_tokens(self, job_id: str, tokens: int) -> int:
        current = self._usage.get(job_id, 0)
        new_total = current + tokens
        self._usage[job_id] = new_total
        return new_total

    def verify_budget(self, job_id: str, estimated_tokens: int = 500) -> None:
        current = self._usage.get(job_id, 0)
        if current + estimated_tokens > self.default_budget:
            raise BudgetExceededError(job_id, current, estimated_tokens, self.default_budget)


@dataclass
class RouterResult:
    """Standardized output from an LLM call or cassette replay."""

    raw_text: str
    parsed: Any | None
    role: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    cassette_key: str | None = None
    from_cassette: bool = False
    fallback_reason: str = ""


class LlmRouter:
    """LiteLLM router with rate limiting, retries, role fallbacks, and cassette caching."""

    def __init__(
        self,
        config: AgentConfig | None = None,
        cassette_store: CassetteStore | None = None,
    ) -> None:
        self.config = config or get_agent_config()
        self.cassettes = cassette_store or CassetteStore(self.config.cassette_dir)
        self.budget_tracker = JobBudgetTracker(self.config.job_token_budget)
        self.rate_limiters: dict[str, TokenBucket] = {
            role: TokenBucket(
                self.config.max_rpm.get(role, 30),
                self.config.max_tpm.get(role, 6000),
            )
            for role in ["reasoner", "extractor", "vision", "agent"]
        }
        self.call_logs: list[LlmCallRecord] = []

    def get_model_for_role(self, role: Role) -> str:
        models = {
            "reasoner": self.config.model_reasoner,
            "extractor": self.config.model_extractor,
            "vision": self.config.model_vision,
            "agent": self.config.model_agent,
        }
        return models.get(role, self.config.model_reasoner)

    def get_fallback_model_for_role(self, role: Role) -> str | None:
        if role == "reasoner":
            return self.config.fallback_reasoner
        elif role == "extractor":
            fallback = self.config.fallback_extractor
            return self.get_model_for_role("reasoner") if fallback == "reasoner" else fallback
        elif role == "agent":
            fallback = self.config.fallback_agent
            return self.get_model_for_role("reasoner") if fallback == "reasoner" else fallback
        elif role == "vision":
            return self.config.fallback_vision
        return None

    async def complete(
        self,
        role: Role,
        prompt: str,
        schema: type[T] | None = None,
        job_id: str = "default",
        line_id: str | None = None,
        fallback_reason: str = "",
        system_prompt: str | None = None,
    ) -> RouterResult:
        """Execute completion with cassette check, rate limiting, retries, and fallback."""
        schema_name = schema.__name__ if schema else ""
        cassette_key = self.cassettes.make_key(role, prompt, schema_name)

        # 1. Cassette Replay Check
        if self.config.cassette_mode in ["auto", "replay"]:
            cached = self.cassettes.find(role, prompt, schema_name)
            if cached:
                self.budget_tracker.add_tokens(job_id, cached.total_tokens)
                parsed_val: Any = None
                raw_text = ""
                if isinstance(cached.response, dict):
                    raw_text = json.dumps(cached.response)
                    if schema:
                        parsed_val = schema.model_validate(cached.response)
                    else:
                        parsed_val = cached.response
                else:
                    raw_text = str(cached.response)
                    if schema:
                        parsed_val = schema.model_validate_json(raw_text)

                log_entry = LlmCallRecord(
                    job_id=job_id,
                    role=role,
                    model=cached.model,
                    prompt_tokens=cached.prompt_tokens,
                    completion_tokens=cached.completion_tokens,
                    total_tokens=cached.total_tokens,
                    latency_ms=cached.latency_ms,
                    fallback_reason=fallback_reason,
                    line_id=line_id,
                    cassette_key=cached.key,
                )
                self.call_logs.append(log_entry)

                return RouterResult(
                    raw_text=raw_text,
                    parsed=parsed_val,
                    role=role,
                    model=cached.model,
                    prompt_tokens=cached.prompt_tokens,
                    completion_tokens=cached.completion_tokens,
                    total_tokens=cached.total_tokens,
                    latency_ms=cached.latency_ms,
                    cassette_key=cached.key,
                    from_cassette=True,
                    fallback_reason=fallback_reason,
                )

        if self.config.cassette_mode == "replay":
            raise RuntimeError(
                f"Cassette missing for key {cassette_key} in strict replay mode (role={role})"
            )

        # 2. Check Budget
        self.budget_tracker.verify_budget(job_id, estimated_tokens=300)

        # 3. Check rate limit
        limiter = self.rate_limiters.get(role)
        if limiter and not limiter.check_and_record():
            log.warning("Rate limit bucket full for role %s; backing off", role)
            time.sleep(self.config.retry_backoff_s)

        # 4. Invoke Provider via litellm with fallback
        primary_model = self.get_model_for_role(role)
        fallback_model = self.get_fallback_model_for_role(role)

        result = await self._call_provider_with_retry(
            primary_model=primary_model,
            fallback_model=fallback_model,
            role=role,
            prompt=prompt,
            schema=schema,
            system_prompt=system_prompt,
        )

        result.cassette_key = cassette_key
        result.fallback_reason = fallback_reason

        # 5. Record Cassette if in record / auto mode
        if self.config.cassette_mode in ["auto", "record"]:
            response_payload = (
                result.parsed.model_dump()
                if isinstance(result.parsed, BaseModel)
                else (result.parsed if isinstance(result.parsed, dict) else result.raw_text)
            )
            new_cassette = Cassette(
                key=cassette_key,
                role=role,
                model=result.model,
                prompt_summary=prompt[:120].replace("\n", " "),
                response=response_payload,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                latency_ms=result.latency_ms,
            )
            self.cassettes.save(new_cassette)

        # 6. Update budget and log
        self.budget_tracker.add_tokens(job_id, result.total_tokens)
        log_entry = LlmCallRecord(
            job_id=job_id,
            role=role,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            latency_ms=result.latency_ms,
            fallback_reason=fallback_reason,
            line_id=line_id,
            cassette_key=cassette_key,
        )
        self.call_logs.append(log_entry)

        return result

    async def _call_provider_with_retry(
        self,
        primary_model: str,
        fallback_model: str | None,
        role: str,
        prompt: str,
        schema: type[T] | None = None,
        system_prompt: str | None = None,
    ) -> RouterResult:
        """Call litellm.acompletion with exponential backoff on 429 and failover to fallback."""
        import litellm

        models_to_try = [primary_model]
        if fallback_model and fallback_model != primary_model:
            models_to_try.append(fallback_model)

        last_exc: Exception | None = None

        for model in models_to_try:
            # Fail fast if provider API key is not configured
            if model.startswith("groq/") and not os.environ.get("GROQ_API_KEY"):
                log.info("GROQ_API_KEY not set; skipping live call to %s", model)
                continue
            if model.startswith("gemini/") and not os.environ.get("GEMINI_API_KEY"):
                log.info("GEMINI_API_KEY not set; skipping live call to %s", model)
                continue

            for attempt in range(self.config.retry_max):
                t0 = time.perf_counter()
                try:
                    messages = []
                    if system_prompt:
                        messages.append({"role": "system", "content": system_prompt})
                    messages.append({"role": "user", "content": prompt})

                    kwargs: dict[str, Any] = {
                        "model": model,
                        "messages": messages,
                        "temperature": 0.0,
                        "timeout": 10,
                    }

                    if schema:
                        # Request structured JSON output
                        kwargs["response_format"] = {
                            "type": "json_object",
                            "response_schema": schema.model_json_schema(),
                        }

                    resp = await litellm.acompletion(**kwargs)
                    dt_ms = int((time.perf_counter() - t0) * 1000)

                    content = resp.choices[0].message.content or ""
                    usage = resp.usage
                    p_tok = getattr(usage, "prompt_tokens", 0) or 0
                    c_tok = getattr(usage, "completion_tokens", 0) or 0
                    tot_tok = getattr(usage, "total_tokens", p_tok + c_tok) or (p_tok + c_tok)

                    parsed_val = None
                    if schema:
                        parsed_val = schema.model_validate_json(content)

                    return RouterResult(
                        raw_text=content,
                        parsed=parsed_val,
                        role=role,
                        model=model,
                        prompt_tokens=p_tok,
                        completion_tokens=c_tok,
                        total_tokens=tot_tok,
                        latency_ms=dt_ms,
                    )
                except Exception as exc:
                    last_exc = exc
                    err_str = str(exc).lower()
                    if "429" in err_str or "rate limit" in err_str:
                        wait = self.config.retry_backoff_s * (2**attempt)
                        log.warning(
                            "Got 429 from %s on attempt %d; sleeping %.1fs",
                            model,
                            attempt + 1,
                            wait,
                        )
                        time.sleep(wait)
                    else:
                        log.warning("Model %s failed on attempt %d: %s", model, attempt + 1, exc)
                        break  # Non-retryable error, try next model

        raise RuntimeError(f"All models failed for role {role}: {last_exc}") from last_exc
