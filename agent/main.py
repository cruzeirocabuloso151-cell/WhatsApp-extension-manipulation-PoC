"""
HackerAI Offline — FastAPI Backend
Endpoints:
  POST /api/chat      → SSE stream do agente
  GET  /api/health    → health check
  GET  /api/sandboxes → lista sandboxes ativos
  DELETE /api/sandbox/{session_id} → destroi sandbox
  GET  /api/models    → lista modelos disponíveis
  GET  /api/mcp/tools → lista tools (BonzAI MCP integration)
  POST /api/mcp/call  → executa tool (BonzAI MCP integration)
"""
from __future__ import annotations

import os
import uuid
import json
from typing import AsyncIterator

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import structlog

from agent_loop import run_agent, AgentEvent, ALL_TOOLS, TOOL_EXECUTORS
from sandbox.docker_sandbox import get_sandbox_manager
from models.lm_client import health_check as llm_health

log = structlog.get_logger(__name__)

app = FastAPI(
    title="HackerAI Offline",
    description="Agente de segurança offline com LM Studio/Qwen + Docker sandboxes",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Ajuste para produção
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Models ───────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    history: list[dict] | None = None


class MCPCallRequest(BaseModel):
    tool: str
    arguments: dict
    session_id: str | None = None


# ─── SSE Helpers ─────────────────────────────────────────────────────────────

async def _sse_stream(session_id: str, message: str, history: list[dict]) -> AsyncIterator[str]:
    """Wrapper que converte AgentEvent → SSE bytes."""
    try:
        async for event in run_agent(session_id, message, history):
            yield event.to_sse()
    except Exception as e:
        log.exception("sse.error", error=str(e))
        yield AgentEvent("error", {"message": str(e)}).to_sse()
    finally:
        # Keep-alive no final
        yield "data: {\"type\": \"stream_end\"}\n\n"


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    llm = await llm_health()
    mgr = get_sandbox_manager()
    return {
        "status": "ok",
        "llm": llm,
        "sandboxes": mgr.list_sandboxes(),
    }


@app.get("/api/models")
async def list_models():
    llm = await llm_health()
    return llm


@app.post("/api/chat")
async def chat(req: ChatRequest):
    session_id = req.session_id or uuid.uuid4().hex
    history = req.history or []

    log.info("chat.start", session_id=session_id, msg_len=len(req.message))

    return StreamingResponse(
        _sse_stream(session_id, req.message, history),
        media_type="text/event-stream",
        headers={
            "X-Session-ID": session_id,
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/sandboxes")
async def list_sandboxes():
    mgr = get_sandbox_manager()
    return {"sandboxes": mgr.list_sandboxes()}


@app.delete("/api/sandbox/{session_id}")
async def destroy_sandbox(session_id: str):
    mgr = get_sandbox_manager()
    await mgr.destroy(session_id)
    return {"destroyed": session_id}


# ─── BonzAI MCP Integration ───────────────────────────────────────────────────

@app.get("/api/mcp/tools")
async def mcp_list_tools():
    """
    Lista tools no formato MCP para integração com BonzAI.
    BonzAI pode conectar a este endpoint como MCP server.
    """
    tools = []
    for tool_def in ALL_TOOLS:
        fn = tool_def["function"]
        tools.append({
            "name": fn["name"],
            "description": fn["description"],
            "inputSchema": fn["parameters"],
        })
    return {"tools": tools}


@app.post("/api/mcp/call")
async def mcp_call_tool(req: MCPCallRequest):
    """
    Executa uma tool via MCP.
    BonzAI chama este endpoint quando o usuário usa uma tool.
    """
    session_id = req.session_id or "mcp-default"
    executor = TOOL_EXECUTORS.get(req.tool)

    if not executor:
        raise HTTPException(404, f"Tool '{req.tool}' não encontrada")

    try:
        result = await executor(session_id=session_id, args=req.arguments)
        return {"result": result}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/mcp")
async def mcp_manifest():
    """
    Manifesto MCP para BonzAI descobrir automaticamente as tools.
    Configure no BonzAI: MCP Server URL = http://localhost:8000/mcp
    """
    return {
        "name": "HackerAI Offline",
        "version": "1.0.0",
        "description": "Ferramentas de segurança offline com sandboxes Docker",
        "tools_endpoint": "/api/mcp/tools",
        "call_endpoint": "/api/mcp/call",
    }


# ─── Static UI fallback ───────────────────────────────────────────────────────

@app.get("/")
async def root():
    return JSONResponse({"message": "HackerAI Offline API", "ui": "http://localhost:3000"})


# ─── Startup / Shutdown ───────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    log.info("hackerai.start", backend=os.getenv("LLM_BACKEND", "ollama"))


@app.on_event("shutdown")
async def shutdown():
    mgr = get_sandbox_manager()
    await mgr.destroy_all()
    log.info("hackerai.shutdown", msg="Todos os sandboxes removidos")
