"""
Tool: todo_write / todo_read
Gerencia lista de tarefas do agente (persiste em memória por sessão).
"""
from __future__ import annotations

from typing import Any
from collections import defaultdict

# Estado em memória (substituível por Redis/SQLite)
_todos: dict[str, list[dict]] = defaultdict(list)

TODO_WRITE_TOOL = {
    "type": "function",
    "function": {
        "name": "todo_write",
        "description": (
            "Atualiza a lista de tarefas do agente. "
            "Use para rastrear progresso em tarefas complexas. "
            "Substitui a lista completa a cada chamada."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "Lista de tarefas",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done", "blocked"],
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                        },
                        "required": ["id", "content", "status"],
                    },
                }
            },
            "required": ["todos"],
        },
    },
}

TODO_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "todo_read",
        "description": "Lê a lista de tarefas atual do agente.",
        "parameters": {"type": "object", "properties": {}},
    },
}


async def todo_write(session_id: str, args: dict[str, Any]) -> dict:
    todos = args.get("todos", [])
    if not todos:
        return {"warning": "Lista de todos vazia — nenhuma alteração"}

    _todos[session_id] = todos
    counts = {
        "total": len(todos),
        "pending": sum(1 for t in todos if t.get("status") == "pending"),
        "in_progress": sum(1 for t in todos if t.get("status") == "in_progress"),
        "done": sum(1 for t in todos if t.get("status") == "done"),
    }
    return {"success": True, **counts}


async def todo_read(session_id: str, args: dict[str, Any]) -> dict:
    todos = _todos.get(session_id, [])
    return {"todos": todos, "count": len(todos)}
