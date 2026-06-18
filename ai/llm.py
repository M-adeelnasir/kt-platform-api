"""LLMProvider interface + the local Ollama adapter (plan §7).

Non-negotiable: business logic calls this interface only — never the model SDK directly.
The Ollama adapter sets `num_ctx` explicitly (Ollama's tiny default silently truncates RAG
context, ruining answers).
"""

from __future__ import annotations

from typing import Protocol

from ollama import Client

from ai.types import LLMMessage, LLMResult
from config import get_settings


class LLMProvider(Protocol):
    def generate(
        self,
        messages: list[LLMMessage],
        *,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> LLMResult: ...


class OllamaLLMProvider:
    """Local Ollama adapter. Swapping to OpenAI/Anthropic later (plan §17) = new adapter,
    no re-indexing.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        num_ctx: int,
    ) -> None:
        self._client = Client(host=base_url)
        self._model = model
        self._num_ctx = num_ctx

    def generate(
        self,
        messages: list[LLMMessage],
        *,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> LLMResult:
        options: dict[str, object] = {"num_ctx": self._num_ctx}
        if temperature is not None:
            options["temperature"] = temperature
        response = self._client.chat(
            model=self._model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            options=options,
            format="json" if json_mode else None,
        )
        return LLMResult(
            text=response["message"]["content"],
            model=self._model,
            prompt_eval_count=response.get("prompt_eval_count"),
            eval_count=response.get("eval_count"),
        )


def build_llm_provider() -> LLMProvider:
    s = get_settings()
    return OllamaLLMProvider(base_url=s.ollama_base_url, model=s.llm_model, num_ctx=s.llm_num_ctx)
