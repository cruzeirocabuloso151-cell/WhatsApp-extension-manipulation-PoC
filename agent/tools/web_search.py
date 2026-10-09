"""
Tools: web_search, open_url
web_search usa SearXNG (offline, sem API key)
open_url usa httpx + BeautifulSoup → markdown (como Jina AI Reader)
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080")

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Pesquisa na web via SearXNG (motor de busca offline). "
            "Retorna snippets relevantes sobre o tema."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query de busca"},
                "num_results": {
                    "type": "integer",
                    "description": "Número de resultados (padrão: 5)",
                    "default": 5,
                },
                "category": {
                    "type": "string",
                    "enum": ["general", "it", "news", "science"],
                    "default": "general",
                },
            },
            "required": ["query"],
        },
    },
}

OPEN_URL_TOOL = {
    "type": "function",
    "function": {
        "name": "open_url",
        "description": (
            "Lê o conteúdo de uma URL e retorna em formato Markdown. "
            "Útil para acessar documentação, writeups, CVEs, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL a acessar"},
                "max_chars": {
                    "type": "integer",
                    "description": "Máximo de caracteres a retornar",
                    "default": 8000,
                },
            },
            "required": ["url"],
        },
    },
}


async def web_search(session_id: str, args: dict[str, Any]) -> dict:
    query: str = args.get("query", "").strip()
    num_results: int = min(int(args.get("num_results", 5)), 10)
    category: str = args.get("category", "general")

    if not query:
        return {"error": "Query vazia"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{SEARXNG_URL}/search",
                params={
                    "q": query,
                    "format": "json",
                    "categories": category,
                    "language": "pt-BR,en",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return {"error": f"SearXNG indisponível: {e}"}
    except Exception as e:
        return {"error": str(e)}

    results = data.get("results", [])[:num_results]
    formatted = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", "")[:400],
        }
        for r in results
    ]

    return {
        "query": query,
        "results": formatted,
        "count": len(formatted),
    }


async def open_url(session_id: str, args: dict[str, Any]) -> dict:
    url: str = args.get("url", "").strip()
    max_chars: int = int(args.get("max_chars", 8000))

    if not url:
        return {"error": "URL vazia"}

    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; HackerAI-bot/1.0)"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {url}"}
    except Exception as e:
        return {"error": f"Falha ao acessar URL: {e}"}

    # Extrair texto relevante
    soup = BeautifulSoup(html, "html.parser")

    # Remover scripts, styles, nav
    for tag in soup(["script", "style", "nav", "footer", "aside", "header"]):
        tag.decompose()

    # Converter para markdown
    content = md(str(soup), heading_style="ATX", bullets="-")

    # Limpar linhas em branco excessivas
    import re
    content = re.sub(r"\n{3,}", "\n\n", content).strip()

    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n...[conteúdo truncado]"

    return {
        "url": url,
        "content": content,
        "chars": len(content),
    }
