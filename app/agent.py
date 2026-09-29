from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import metrics
from .mock_llm import FakeLLM, FakeResponse
from .mock_rag import retrieve
from .pii import hash_user_id, scrub_text, summarize_text
from .prompt_management import ResolvedPrompt, resolve_prompt
from .tracing import get_langfuse_client, observe, propagate_attributes, tracing_enabled

# Đơn giá giả lập (USD / 1M tokens) cho claude-sonnet-4-5.
INPUT_PRICE_PER_MTOK = 3.0
OUTPUT_PRICE_PER_MTOK = 15.0


@dataclass
class AgentResult:
    answer: str
    latency_ms: int
    ttft_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    quality_score: float
    retrieval_ms: int = 0
    prompt_ms: int = 0
    llm_ms: int = 0
    doc_count: int = 0
    prompt_version: str = ""
    trace_id: str | None = None


class LabAgent:
    def __init__(self, model: str = "claude-sonnet-4-5") -> None:
        self.model = model
        self.llm = FakeLLM(model=model)

    @observe(name="lab-agent-run", as_type="agent", capture_input=False, capture_output=False)
    def run(
        self,
        user_id: str,
        feature: str,
        session_id: str,
        message: str,
        correlation_id: str,
    ) -> AgentResult:
        langfuse_client = get_langfuse_client()
        with propagate_attributes(
            user_id=hash_user_id(user_id),
            session_id=session_id,
            tags=["lab", feature, self.model],
            trace_name="day13-agent-request",
            environment=os.getenv("APP_ENV", "dev"),
            metadata={
                "feature": feature,
                "model": self.model,
                "correlation_id": correlation_id,
            },
        ):
            started = time.perf_counter()

            # Child observation #1: retrieval (as_type="retriever").
            retrieval_started = time.perf_counter()
            docs = self._retrieve(message)
            retrieval_ms = int((time.perf_counter() - retrieval_started) * 1000)

            prompt_started = time.perf_counter()
            prompt = self._resolve_prompt(langfuse_client, feature=feature, docs=docs, message=message)
            prompt_ms = int((time.perf_counter() - prompt_started) * 1000)
            langfuse_client.update_current_span(
                metadata={
                    "doc_count": len(docs),
                    "query_preview": summarize_text(message),
                    "prompt_name": prompt.name,
                    "prompt_label": prompt.label,
                    "prompt_version": prompt.version,
                    "prompt_source": prompt.source,
                    "prompt_fetch_error": prompt.fetch_error or "",
                },
                version=prompt.version,
            )

            # Child observation #2: LLM call (as_type="generation") gắn prompt managed,
            # model, usage và cost.
            llm_started = time.perf_counter()
            with propagate_attributes(prompt=prompt.managed_prompt):
                response = self._generate(prompt)
            llm_ms = int((time.perf_counter() - llm_started) * 1000)

            quality_score = self._heuristic_quality(message, response.text, docs)
            latency_ms = int((time.perf_counter() - started) * 1000)
            cost_usd = self._estimate_cost(response.usage.input_tokens, response.usage.output_tokens)
            trace_id = self._current_trace_id(langfuse_client)

        metrics.record_request(
            latency_ms=latency_ms,
            ttft_ms=response.ttft_ms,
            cost_usd=cost_usd,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            quality_score=quality_score,
        )

        return AgentResult(
            answer=response.text,
            latency_ms=latency_ms,
            ttft_ms=response.ttft_ms,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            cost_usd=cost_usd,
            quality_score=quality_score,
            retrieval_ms=retrieval_ms,
            prompt_ms=prompt_ms,
            llm_ms=llm_ms,
            doc_count=len(docs),
            prompt_version=prompt.version,
            trace_id=trace_id,
        )

    @observe(name="prompt-resolve", as_type="span", capture_input=False, capture_output=False)
    def _resolve_prompt(self, client, *, feature: str, docs: list[str], message: str) -> ResolvedPrompt:
        """Span riêng cho bước lấy prompt từ Langfuse (lần đầu/cache miss có thể mất ~1–2 s)."""
        prompt = resolve_prompt(client, feature=feature, docs=docs, message=message, enabled=tracing_enabled())
        client.update_current_span(
            metadata={"prompt_source": prompt.source, "prompt_version": prompt.version, "prompt_label": prompt.label},
        )
        return prompt

    @observe(name="retrieval", as_type="retriever", capture_input=False, capture_output=False)
    def _retrieve(self, message: str) -> list[str]:
        """Bọc mock RAG thành observation riêng; lỗi sẽ được SDK đánh dấu level=ERROR."""
        docs = retrieve(message)
        get_langfuse_client().update_current_span(
            input={"query_preview": summarize_text(message)},
            output={"doc_count": len(docs), "docs_preview": [summarize_text(d, 60) for d in docs]},
            metadata={"doc_count": len(docs), "retriever": "mock_rag.keyword"},
        )
        return docs

    @observe(name="llm-generation", as_type="generation", capture_input=False, capture_output=False)
    def _generate(self, prompt: ResolvedPrompt) -> FakeResponse:
        call_started_at = datetime.now(timezone.utc)
        response = self.llm.generate(prompt.text)
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        input_cost = self._round_cost((tokens_in / 1_000_000) * INPUT_PRICE_PER_MTOK)
        output_cost = self._round_cost((tokens_out / 1_000_000) * OUTPUT_PRICE_PER_MTOK)
        get_langfuse_client().update_current_generation(
            model=response.model,
            # Chỉ gửi prompt đã scrub PII; không bao giờ gửi raw input của user.
            input=scrub_text(prompt.text),
            output=summarize_text(response.text, 200),
            usage_details={
                "input": tokens_in,
                "output": tokens_out,
                "total": tokens_in + tokens_out,
            },
            cost_details={
                "input": input_cost,
                "output": output_cost,
                "total": self._round_cost(input_cost + output_cost),
            },
            completion_start_time=call_started_at + timedelta(milliseconds=response.ttft_ms),
            prompt=prompt.managed_prompt,
            metadata={
                "ttft_ms": response.ttft_ms,
                "prompt_name": prompt.name,
                "prompt_label": prompt.label,
                "prompt_version": prompt.version,
                "prompt_source": prompt.source,
            },
        )
        return response

    @staticmethod
    def _current_trace_id(client: object) -> str | None:
        getter = getattr(client, "get_current_trace_id", None)
        if not callable(getter) or not tracing_enabled():
            return None
        try:
            return getter()
        except Exception:  # tracing không được làm hỏng request
            return None

    @staticmethod
    def _round_cost(value: float) -> float:
        return round(value, 6)

    def _estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        input_cost = (tokens_in / 1_000_000) * INPUT_PRICE_PER_MTOK
        output_cost = (tokens_out / 1_000_000) * OUTPUT_PRICE_PER_MTOK
        return round(input_cost + output_cost, 6)

    def _heuristic_quality(self, question: str, answer: str, docs: list[str]) -> float:
        score = 0.5
        if docs:
            score += 0.2
        if len(answer) > 40:
            score += 0.1
        if question.lower().split()[0:1] and any(token in answer.lower() for token in question.lower().split()[:3]):
            score += 0.1
        if "[REDACTED" in answer:
            score -= 0.2
        return round(max(0.0, min(1.0, score)), 2)
