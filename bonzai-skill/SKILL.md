# HackerAI Offline — BonzAI Skill

## Descrição
Ferramentas de segurança offline com sandboxes Docker isolados.
Conecta ao servidor HackerAI Offline via MCP.

## Configuração no BonzAI
1. Inicie o stack: `docker compose up -d`
2. No BonzAI, adicione MCP Server:
   - **URL**: `http://localhost:8000/mcp`
   - **Nome**: `HackerAI Offline`
3. As ferramentas estarão disponíveis automaticamente.

## Ferramentas disponíveis

### `run_terminal_cmd`
Executa comandos shell em sandbox Docker isolado.
- Python, nmap, curl, git, ferramentas de análise pré-instalados
- Timeout configurável (padrão: 30s, máx: 120s)
- Suporte a execução em background

### `web_search`
Pesquisa na web via SearXNG (offline, sem API key).
- Categorias: general, it, news, science
- Retorna snippets com URLs

### `open_url`
Lê conteúdo de URLs em formato Markdown.
- Extrai texto limpo, remove scripts/ads
- Útil para documentação, CVEs, writeups

### `create_file` / `view_file` / `list_files`
Operações de arquivo no sandbox.

### `todo_write` / `todo_read`
Gerencia lista de tarefas do agente para tarefas complexas.

## Casos de uso

```
Analise o código em /workspace/app para vulnerabilidades SQL injection
```
```
Faça varredura de portas em 192.168.1.0/24 e documente os resultados
```
```
Pesquise CVEs recentes para nginx 1.24 e liste as críticas
```
```
Instale e rode semgrep no projeto Python no sandbox
```

## Modelo recomendado
- **Qwen2.5-Coder:7b** — ótimo para análise de código (via Ollama no Docker)
- **Qwen2.5:7b** — bom para tarefas gerais de segurança
- Ou qualquer modelo no LM Studio via `http://localhost:1234`

## Notas de segurança
- Sandboxes são containers isolados sem acesso direto à internet
- Cada sessão tem seu próprio container
- Containers são destruídos automaticamente ao encerrar
- Operações destrutivas críticas são bloqueadas por política
