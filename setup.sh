#!/usr/bin/env bash
# HackerAI Offline — Setup Script
# Executa no host (não dentro do Docker)
set -e

echo "🔐 HackerAI Offline — Setup"
echo "═══════════════════════════════════════"

# ── Verificar dependências ────────────────────────────────────────────────────
check_dep() {
  if ! command -v "$1" &>/dev/null; then
    echo "❌ Dependência não encontrada: $1"
    echo "   Instale: $2"
    exit 1
  fi
  echo "✓ $1"
}

echo ""
echo "Verificando dependências..."
check_dep docker "https://docs.docker.com/get-docker/"
check_dep "docker" "docker compose plugin"

# Verificar docker compose v2
if docker compose version &>/dev/null; then
  COMPOSE="docker compose"
elif docker-compose version &>/dev/null; then
  COMPOSE="docker-compose"
else
  echo "❌ docker compose não encontrado"
  exit 1
fi
echo "✓ docker compose"

# ── Criar .env se não existir ─────────────────────────────────────────────────
echo ""
if [ ! -f .env ]; then
  cp .env.example .env
  # Gerar secret key aleatório
  SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32)
  sed -i "s/change-me-to-a-random-32-char-string/$SECRET/" .env
  echo "✓ .env criado com secret key aleatório"
else
  echo "✓ .env já existe"
fi

# ── Detectar LM Studio ────────────────────────────────────────────────────────
echo ""
echo "Verificando LM Studio..."
if curl -s --connect-timeout 2 http://localhost:1234/v1/models &>/dev/null; then
  echo "✓ LM Studio detectado em localhost:1234"
  sed -i 's/LLM_BACKEND=ollama/LLM_BACKEND=lm_studio/' .env 2>/dev/null || true
  echo "  → Configurado para usar LM Studio"
else
  echo "ℹ️  LM Studio não detectado. Usando Ollama+Qwen no Docker."
  echo "   Para usar LM Studio: inicie-o e rode este script novamente"
fi

# ── Build e start ─────────────────────────────────────────────────────────────
echo ""
echo "Construindo imagens Docker..."
$COMPOSE build --parallel

echo ""
echo "Iniciando serviços..."
$COMPOSE up -d

# ── Aguardar serviços ─────────────────────────────────────────────────────────
echo ""
echo "Aguardando serviços iniciarem..."
sleep 5

# Aguardar agent
for i in $(seq 1 30); do
  if curl -s http://localhost:8000/api/health &>/dev/null; then
    echo "✓ Agent online"
    break
  fi
  echo "  Aguardando agent... ($i/30)"
  sleep 3
done

# ── Pull Qwen se usando Ollama ────────────────────────────────────────────────
if grep -q "LLM_BACKEND=ollama" .env; then
  echo ""
  echo "Baixando modelo Qwen (pode demorar na primeira vez)..."
  $COMPOSE logs -f ollama-init &
  LOG_PID=$!
  # Aguardar init completar
  $COMPOSE wait ollama-init 2>/dev/null || true
  kill $LOG_PID 2>/dev/null || true
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "✅ HackerAI Offline está rodando!"
echo ""
echo "🌐 UI:        http://localhost:3000"
echo "🔌 API:       http://localhost:8000"
echo "🔍 SearXNG:   http://localhost:8888"
echo "🤖 Ollama:    http://localhost:11434"
echo ""
echo "🔗 BonzAI MCP: http://localhost:8000/mcp"
echo "   Configure no BonzAI → Settings → MCP Servers"
echo ""
echo "Para parar: docker compose down"
echo "Para logs:  docker compose logs -f agent"
