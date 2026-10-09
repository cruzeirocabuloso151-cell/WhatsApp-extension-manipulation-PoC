"""
Tool: run_terminal_cmd
Executa comandos no sandbox Docker da sessão.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sandbox.docker_sandbox import get_sandbox_manager

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "run_terminal_cmd",
        "description": (
            "Executa um comando shell no sandbox isolado da sessão. "
            "Use para rodar ferramentas de segurança, scripts, análises, etc. "
            "O sandbox tem Python, nmap, curl, git e outras ferramentas pré-instaladas."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Comando shell a executar",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout em segundos (padrão: 30, máx: 120)",
                    "default": 30,
                },
                "workdir": {
                    "type": "string",
                    "description": "Diretório de trabalho no sandbox",
                    "default": "/workspace",
                },
                "background": {
                    "type": "boolean",
                    "description": "Executar em background (não aguarda conclusão)",
                    "default": False,
                },
                "brief": {
                    "type": "string",
                    "description": "Descrição breve do que o comando faz",
                },
            },
            "required": ["command"],
        },
    },
}


async def run_terminal_cmd(
    session_id: str,
    args: dict[str, Any],
    approval_required: bool = False,
) -> dict:
    """
    Executa um comando terminal no sandbox Docker.
    Retorna dict com stdout, stderr, exit_code.
    """
    command: str = args.get("command", "").strip()
    timeout: int = min(int(args.get("timeout", 30)), 120)
    workdir: str = args.get("workdir", "/workspace")
    background: bool = args.get("background", False)

    if not command:
        return {"error": "Comando vazio", "exit_code": 1}

    # Sanitização básica: bloquear comandos críticos
    _check_dangerous(command)

    mgr = get_sandbox_manager()

    if background:
        # Fire-and-forget com nohup
        bg_cmd = f"nohup bash -c {json.dumps(command)} > /tmp/bg_last.log 2>&1 &"
        result = await mgr.exec(session_id, bg_cmd, timeout=5, workdir=workdir)
        return {
            "stdout": "[Executando em background]",
            "stderr": result.get("stderr", ""),
            "exit_code": 0,
            "background": True,
        }

    result = await mgr.exec(session_id, command, timeout=timeout, workdir=workdir)

    # Truncar outputs muito grandes
    MAX_OUTPUT = 8000
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")
    if len(stdout) > MAX_OUTPUT:
        stdout = stdout[:MAX_OUTPUT] + f"\n...[truncado, {len(stdout)} chars total]"
    if len(stderr) > MAX_OUTPUT:
        stderr = stderr[:MAX_OUTPUT] + f"\n...[truncado, {len(stderr)} chars total]"

    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": result.get("exit_code", 0),
        "timed_out": result.get("timed_out", False),
    }


_BLOCKED = re.compile(
    r"\b(rm\s+-rf\s*/|mkfs|dd\s+if=|chmod\s+777\s+/|>\s*/etc/passwd)\b",
    re.IGNORECASE,
)


def _check_dangerous(cmd: str) -> None:
    """Bloqueia os comandos mais destrutivos."""
    if _BLOCKED.search(cmd):
        raise ValueError(f"Comando bloqueado por política de segurança: {cmd[:100]}")
