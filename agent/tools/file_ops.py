"""
Tools: create_file, view_file, edit_file
Operações de arquivo no sandbox Docker.
"""
from __future__ import annotations

from typing import Any
from sandbox.docker_sandbox import get_sandbox_manager

CREATE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "create_file",
        "description": "Cria ou sobrescreve um arquivo no sandbox com o conteúdo fornecido.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Caminho do arquivo no sandbox"},
                "content": {"type": "string", "description": "Conteúdo do arquivo"},
                "brief": {"type": "string", "description": "Descrição do arquivo"},
            },
            "required": ["path", "content"],
        },
    },
}

VIEW_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "view_file",
        "description": "Lê o conteúdo de um arquivo do sandbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Caminho do arquivo"},
                "start_line": {"type": "integer", "description": "Linha inicial (opcional)"},
                "end_line": {"type": "integer", "description": "Linha final (opcional)"},
            },
            "required": ["path"],
        },
    },
}

LIST_FILES_TOOL = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": "Lista arquivos no diretório do sandbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Diretório a listar", "default": "/workspace"},
            },
        },
    },
}


async def create_file(session_id: str, args: dict[str, Any]) -> dict:
    path: str = args.get("path", "")
    content: str = args.get("content", "")
    if not path:
        return {"error": "path obrigatório"}

    # Normalizar path
    if not path.startswith("/"):
        path = f"/workspace/{path}"

    mgr = get_sandbox_manager()
    # Criar diretório pai
    await mgr.exec(session_id, f"mkdir -p {_dirname(path)}", timeout=5)
    await mgr.write_file(session_id, path, content)
    return {"success": True, "path": path, "bytes": len(content.encode())}


async def view_file(session_id: str, args: dict[str, Any]) -> dict:
    path: str = args.get("path", "")
    start: int | None = args.get("start_line")
    end: int | None = args.get("end_line")

    if not path.startswith("/"):
        path = f"/workspace/{path}"

    mgr = get_sandbox_manager()

    if start or end:
        s = start or 1
        e = end or 9999
        result = await mgr.exec(session_id, f"sed -n '{s},{e}p' {path}", timeout=10)
    else:
        result = await mgr.exec(session_id, f"cat {path}", timeout=10)

    if result["exit_code"] != 0:
        return {"error": result["stderr"] or f"Arquivo não encontrado: {path}"}

    content = result["stdout"]
    MAX = 10000
    if len(content) > MAX:
        content = content[:MAX] + f"\n...[truncado]"

    return {"content": content, "path": path}


async def list_files(session_id: str, args: dict[str, Any]) -> dict:
    path: str = args.get("path", "/workspace")
    mgr = get_sandbox_manager()
    files = await mgr.list_files(session_id, path)
    return {"files": files, "count": len(files)}


def _dirname(path: str) -> str:
    parts = path.rsplit("/", 1)
    return parts[0] if len(parts) > 1 else "/workspace"
