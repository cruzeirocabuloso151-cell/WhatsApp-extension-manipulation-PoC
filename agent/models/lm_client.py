"""
LLM Client — suporta LM Studio e Ollama (ambos com API OpenAI-compatible)
Selecionado via env LLM_BACKEND=ollama|lm_studio
"""
from __future__ import annotations

import os
import asyncio
from typing import AsyncIterator, Any
from dataclasses import dataclass

from openai import AsyncOpenAI
import structlog

log = structlog.get_logger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────

BACKEND = os.getenv("LLM_BACKEND", "ollama")

LM_STUDIO_URL   = os.getenv("LM_STUDIO_URL", "http://host.docker.internal:1234")
LM_STUDIO_MODEL = os.getenv("LM_STUDIO_MODEL", "local-model")

OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")

# ─── Client factory ───────────────────────────────────────────────────────────

def _make_client() -> tuple[AsyncOpenAI, str]:
    """Retorna (client, model_name) dependendo do backend configurado."""
    if BACKEND == "lm_studio":
        client = AsyncOpenAI(
            base_url=f"{LM_STUDIO_URL}/v1",
            api_key="lm-studio",  # LM Studio não valida a key
            timeout=120,
        )
        return client, LM_STUDIO_MODEL

    # default: ollama
    client = AsyncOpenAI(
        base_url=f"{OLLAMA_URL}/v1",
        api_key="ollama",  # Ollama também não valida
        timeout=180,
    )
    return client, OLLAMA_MODEL


# Singleton (recriado se backend mudar em runtime)
_client, _model = _make_client()


# ─── Public API ───────────────────────────────────────────────────────────────

@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


async def chat_stream(
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    model: str | None = None,
) -> AsyncIterator[dict]:
    """
    Streaming chat completion.
    Yields dicts:
      {"type": "text", "delta": str}
      {"type": "tool_call", "id": str, "name": str, "arguments": str}
      {"type": "tool_call_delta", "id": str, "arguments_delta": str}
      {"type": "finish", "reason": str, "usage": LLMUsage}
    """
    chosen_model = model or _model
    log.debug("llm.chat_stream", backend=BACKEND, model=chosen_model, n_messages=len(messages))

    kwargs: dict[str, Any] = {
        "model": chosen_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    tool_calls_buf: dict[int, dict] = {}

    async with _client.chat.completions.stream(**kwargs) as stream:
        async for event in stream:
            choice = event.choices[0] if event.choices else None
            if not choice:
                continue

            delta = choice.delta

            # Text delta
            if delta.content:
                yield {"type": "text", "delta": delta.content}

            # Tool call deltas
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_buf:
                        tool_calls_buf[idx] = {
                            "id": tc.id or "",
                            "name": tc.function.name or "" if tc.function else "",
                            "arguments": "",
                        }
                        yield {
                            "type": "tool_call",
                            "id": tool_calls_buf[idx]["id"],
                            "name": tool_calls_buf[idx]["name"],
                            "arguments": "",
                        }
                    if tc.function and tc.function.arguments:
                        tool_calls_buf[idx]["arguments"] += tc.function.arguments
                        yield {
                            "type": "tool_call_delta",
                            "id": tool_calls_buf[idx]["id"],
                            "arguments_delta": tc.function.arguments,
                        }

            # Finish
            if choice.finish_reason:
                usage = LLMUsage()
                if event.usage:
                    usage = LLMUsage(
                        prompt_tokens=event.usage.prompt_tokens or 0,
                        completion_tokens=event.usage.completion_tokens or 0,
                        total_tokens=event.usage.total_tokens or 0,
                    )
                yield {
                    "type": "finish",
                    "reason": choice.finish_reason,
                    "usage": usage,
                    "tool_calls": list(tool_calls_buf.values()),
                }


async def chat_once(
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    model: str | None = None,
) -> dict:
    """Non-streaming chat — retorna resposta completa."""
    chosen_model = model or _model
    kwargs: dict[str, Any] = {
        "model": chosen_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    resp = await _client.chat.completions.create(**kwargs)
    choice = resp.choices[0]
    return {
        "content": choice.message.content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            }
            for tc in (choice.message.tool_calls or [])
        ],
        "finish_reason": choice.finish_reason,
        "usage": LLMUsage(
            prompt_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            completion_tokens=resp.usage.completion_tokens if resp.usage else 0,
            total_tokens=resp.usage.total_tokens if resp.usage else 0,
        ),
    }


async def health_check() -> dict:
    """Verifica se o backend LLM está disponível."""
    try:
        models = await _client.models.list()
        model_ids = [m.id for m in models.data]
        return {"ok": True, "backend": BACKEND, "models": model_ids}
    except Exception as e:
        return {"ok": False, "backend": BACKEND, "error": str(e)}
