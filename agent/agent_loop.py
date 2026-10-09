"""
Agent Loop — coração do HackerAI Offline
Implementa: doom loop detection, context compaction, tool execution, streaming SSE.
Inspirado na arquitetura do HackerAI (lib/api/agent-stream-runner.ts).
"""
from __future__ import annotations

import asyncio
import json
import time
import hashlib
from dataclasses import dataclass, field
from typing import AsyncIterator, Any, Callable, Awaitable
from collections import deque

import structlog
from models.lm_client import chat_stream, LLMUsage
from tools.terminal import run_terminal_cmd, TOOL_DEFINITION as TERMINAL_TOOL
from tools.file_ops import (
    create_file, view_file, list_files,
    CREATE_FILE_TOOL, VIEW_FILE_TOOL, LIST_FILES_TOOL,
)
from tools.web_search import web_search, open_url, WEB_SEARCH_TOOL, OPEN_URL_TOOL
from tools.todo import todo_write, todo_read, TODO_WRITE_TOOL, TODO_READ_TOOL

log = structlog.get_logger(__name__)

# ─── Doom Loop Detection ──────────────────────────────────────────────────────
DOOM_LOOP_WARNING_THRESHOLD = 3   # steps idênticos → injetar nudge
DOOM_LOOP_HALT_THRESHOLD    = 5   # steps idênticos → parar
COSMETIC_FIELDS = {"brief", "explanation"}  # ignorados no fingerprinting

# ─── Limites do loop ──────────────────────────────────────────────────────────
MAX_STEPS           = 50
MAX_TOKENS_BEFORE_COMPACTION = 6000   # tokens antes de compactar contexto
MAX_COMPACTIONS     = 3               # máximo de compactações por run
MAX_ELAPSED_SECONDS = 600             # 10 minutos

# ─── Ferramentas disponíveis ──────────────────────────────────────────────────
ALL_TOOLS = [
    TERMINAL_TOOL,
    CREATE_FILE_TOOL,
    VIEW_FILE_TOOL,
    LIST_FILES_TOOL,
    WEB_SEARCH_TOOL,
    OPEN_URL_TOOL,
    TODO_WRITE_TOOL,
    TODO_READ_TOOL,
]

TOOL_EXECUTORS: dict[str, Callable] = {
    "run_terminal_cmd": run_terminal_cmd,
    "create_file": create_file,
    "view_file": view_file,
    "list_files": list_files,
    "web_search": web_search,
    "open_url": open_url,
    "todo_write": todo_write,
    "todo_read": todo_read,
}

SYSTEM_PROMPT = """\
Você é HackerAI, um assistente especializado em segurança da informação e pesquisa de vulnerabilidades.
Você ajuda profissionais de segurança com:
- Análise de código para vulnerabilidades
- Pesquisa e documentação de CVEs
- Análise forense e investigação
- Automação de tarefas de segurança

Você tem acesso a um ambiente sandbox isolado com ferramentas pré-instaladas.
Use as ferramentas disponíveis para completar as tarefas de forma metódica.

Regras:
1. Sempre esclareça o escopo antes de iniciar análises
2. Documente suas descobertas de forma clara
3. Reporte vulnerabilidades de forma responsável
4. Use todo_write para rastrear tarefas complexas
"""


# ─── Tipos ────────────────────────────────────────────────────────────────────

@dataclass
class AgentEvent:
    type: str           # text | tool_call | tool_result | status | error | done
    data: dict = field(default_factory=dict)

    def to_sse(self) -> str:
        return f"data: {json.dumps({'type': self.type, **self.data})}\n\n"


@dataclass
class RunState:
    session_id: str
    messages: list[dict] = field(default_factory=list)
    step: int = 0
    start_time: float = field(default_factory=time.time)
    total_tokens: int = 0
    compactions: int = 0
    recent_fingerprints: deque = field(default_factory=lambda: deque(maxlen=10))
    stop_reason: str | None = None


# ─── Doom Loop Detection ──────────────────────────────────────────────────────

def _fingerprint_tool_calls(tool_calls: list[dict]) -> str:
    """Gera fingerprint dos tool calls ignorando campos cosméticos."""
    cleaned = []
    for tc in tool_calls:
        args = {}
        try:
            raw = json.loads(tc.get("arguments", "{}"))
            args = {k: v for k, v in raw.items() if k not in COSMETIC_FIELDS}
        except json.JSONDecodeError:
            args = {"_raw": tc.get("arguments", "")}
        cleaned.append({"name": tc.get("name", ""), "args": args})
    return hashlib.md5(json.dumps(cleaned, sort_keys=True).encode()).hexdigest()


def _check_doom_loop(state: RunState, tool_calls: list[dict]) -> str:
    """Retorna: 'none' | 'warning' | 'halt'"""
    if not tool_calls:
        return "none"
    fp = _fingerprint_tool_calls(tool_calls)
    state.recent_fingerprints.append(fp)

    recent = list(state.recent_fingerprints)
    if len(recent) < DOOM_LOOP_WARNING_THRESHOLD:
        return "none"

    last_n = recent[-DOOM_LOOP_WARNING_THRESHOLD:]
    if all(f == last_n[0] for f in last_n):
        if len(recent) >= DOOM_LOOP_HALT_THRESHOLD:
            last_halt = recent[-DOOM_LOOP_HALT_THRESHOLD:]
            if all(f == last_halt[0] for f in last_halt):
                return "halt"
        return "warning"
    return "none"


# ─── Context Compaction ───────────────────────────────────────────────────────

async def _compact_context(state: RunState) -> bool:
    """
    Sumariza o histórico quando tokens se esgotam.
    Retorna True se compactou com sucesso.
    """
    if state.compactions >= MAX_COMPACTIONS:
        return False

    log.info("context.compact", step=state.step, tokens=state.total_tokens)

    # Preservar system prompt + últimas 4 mensagens
    tail = state.messages[-4:] if len(state.messages) > 4 else state.messages[:]
    history_to_summarize = state.messages[:-4] if len(state.messages) > 4 else []

    if not history_to_summarize:
        return False

    history_text = "\n\n".join(
        f"[{m['role'].upper()}]: {m.get('content', '')[:500]}"
        for m in history_to_summarize
        if isinstance(m.get("content"), str)
    )

    summary_messages = [
        {
            "role": "user",
            "content": (
                f"Sumarize o histórico abaixo de forma concisa (máx 500 palavras), "
                f"preservando fatos importantes, erros encontrados e progresso atual:\n\n{history_text}"
            ),
        }
    ]

    try:
        result_chunks = []
        async for ev in chat_stream(summary_messages, temperature=0.3, max_tokens=600):
            if ev["type"] == "text":
                result_chunks.append(ev["delta"])

        summary = "".join(result_chunks)
        state.messages = [
            {"role": "assistant", "content": f"[Resumo do contexto anterior]\n{summary}"},
            *tail,
        ]
        state.compactions += 1
        state.total_tokens = 0  # reset após compactação
        return True
    except Exception as e:
        log.error("context.compact.error", error=str(e))
        return False


# ─── Tool Executor ────────────────────────────────────────────────────────────

async def _execute_tool(session_id: str, name: str, arguments_str: str) -> str:
    """Executa uma ferramenta e retorna o resultado como string JSON."""
    executor = TOOL_EXECUTORS.get(name)
    if not executor:
        return json.dumps({"error": f"Ferramenta desconhecida: {name}"})

    try:
        args = json.loads(arguments_str) if arguments_str else {}
    except json.JSONDecodeError:
        args = {}

    try:
        result = await executor(session_id=session_id, args=args)
        return json.dumps(result, ensure_ascii=False, default=str)
    except ValueError as e:
        # Blocked by security policy
        return json.dumps({"error": str(e)})
    except Exception as e:
        log.exception("tool.execute.error", tool=name, error=str(e))
        return json.dumps({"error": f"Erro ao executar {name}: {e}"})


# ─── Main Agent Loop ──────────────────────────────────────────────────────────

async def run_agent(
    session_id: str,
    user_message: str,
    history: list[dict] | None = None,
) -> AsyncIterator[AgentEvent]:
    """
    Loop principal do agente. Yields AgentEvent's.
    Suporte a: doom loop detection, context compaction, tool execution, streaming.
    """
    state = RunState(session_id=session_id)

    # Inicializar mensagens
    state.messages = list(history or [])
    state.messages.append({"role": "user", "content": user_message})

    system_messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    yield AgentEvent("status", {"text": "🤖 Iniciando agente...", "step": 0})

    while True:
        state.step += 1
        elapsed = time.time() - state.start_time

        # ── Stop conditions ──────────────────────────────────────────────────
        if state.step > MAX_STEPS:
            state.stop_reason = "step_limit"
            yield AgentEvent("status", {"text": f"Limite de {MAX_STEPS} steps atingido."})
            break

        if elapsed > MAX_ELAPSED_SECONDS:
            state.stop_reason = "timeout"
            yield AgentEvent("status", {"text": f"Timeout após {elapsed:.0f}s."})
            break

        if state.stop_reason:
            break

        # ── Context compaction check ─────────────────────────────────────────
        if state.total_tokens > MAX_TOKENS_BEFORE_COMPACTION:
            compacted = await _compact_context(state)
            if compacted:
                yield AgentEvent("status", {"text": "📦 Contexto compactado."})
            elif state.compactions >= MAX_COMPACTIONS:
                state.stop_reason = "context_exhausted"
                yield AgentEvent("status", {"text": "Contexto esgotado."})
                break

        # ── LLM call ─────────────────────────────────────────────────────────
        yield AgentEvent("status", {"text": f"💭 Pensando... (step {state.step})", "step": state.step})

        current_tool_calls: list[dict] = []
        text_buffer: list[str] = []

        try:
            async for ev in chat_stream(
                messages=system_messages + state.messages,
                tools=ALL_TOOLS,
                temperature=0.7,
                max_tokens=2048,
            ):
                if ev["type"] == "text":
                    text_buffer.append(ev["delta"])
                    yield AgentEvent("text", {"delta": ev["delta"]})

                elif ev["type"] == "tool_call":
                    current_tool_calls.append({
                        "id": ev["id"],
                        "name": ev["name"],
                        "arguments": "",
                    })
                    yield AgentEvent("tool_call", {
                        "id": ev["id"],
                        "name": ev["name"],
                        "step": state.step,
                    })

                elif ev["type"] == "tool_call_delta":
                    for tc in current_tool_calls:
                        if tc["id"] == ev["id"]:
                            tc["arguments"] += ev["arguments_delta"]

                elif ev["type"] == "finish":
                    usage: LLMUsage = ev.get("usage", LLMUsage())
                    state.total_tokens += usage.total_tokens
                    current_tool_calls = ev.get("tool_calls", current_tool_calls)

                    if ev["reason"] == "stop" and not current_tool_calls:
                        # Agente terminou — sem mais tool calls
                        if text_buffer:
                            state.messages.append({
                                "role": "assistant",
                                "content": "".join(text_buffer),
                            })
                        state.stop_reason = "done"

        except Exception as e:
            log.exception("llm.stream.error", error=str(e))
            yield AgentEvent("error", {"message": str(e)})
            state.stop_reason = "llm_error"
            break

        if state.stop_reason == "done":
            break

        # ── Doom loop check ──────────────────────────────────────────────────
        doom = _check_doom_loop(state, current_tool_calls)
        if doom == "halt":
            yield AgentEvent("status", {"text": "⛔ Doom loop detectado. Encerrando."})
            state.stop_reason = "doom_loop"
            break
        elif doom == "warning":
            nudge = "Você parece estar repetindo as mesmas ações. Tente uma abordagem diferente ou conclua a tarefa atual."
            state.messages.append({"role": "user", "content": nudge})
            yield AgentEvent("status", {"text": "⚠️ Possível loop detectado. Ajustando..."})

        # ── Execute tools ────────────────────────────────────────────────────
        if not current_tool_calls:
            # Sem tool calls e não terminou → forçar stop
            state.stop_reason = "no_tools_no_stop"
            break

        # Adicionar mensagem do assistente com tool calls
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(text_buffer) or None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    },
                }
                for tc in current_tool_calls
            ],
        }
        state.messages.append(assistant_msg)

        # Executar cada ferramenta
        for tc in current_tool_calls:
            yield AgentEvent("tool_call", {
                "id": tc["id"],
                "name": tc["name"],
                "arguments": tc["arguments"],
                "step": state.step,
            })

            result_str = await _execute_tool(session_id, tc["name"], tc["arguments"])

            yield AgentEvent("tool_result", {
                "id": tc["id"],
                "name": tc["name"],
                "result": result_str[:2000],  # preview no SSE
            })

            state.messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_str,
            })

    # ── Final ────────────────────────────────────────────────────────────────
    yield AgentEvent("done", {
        "steps": state.step,
        "elapsed_s": round(time.time() - state.start_time, 2),
        "total_tokens": state.total_tokens,
        "compactions": state.compactions,
        "stop_reason": state.stop_reason or "done",
    })
