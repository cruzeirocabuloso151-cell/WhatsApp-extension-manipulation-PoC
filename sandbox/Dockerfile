# HackerAI Offline — Sandbox Image
# Container isolado para execução de comandos do agente
# Sem acesso direto à internet (controlado pelo network policy do docker-compose)

FROM kalilinux/kali-rolling:latest

LABEL maintainer="HackerAI Offline"
LABEL description="Sandbox isolado com ferramentas de segurança e análise"

# Variáveis
ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/root
ENV TERM=xterm-256color
ENV WORKSPACE=/workspace

# ── Sistema base ──────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Utilitários básicos
    bash \
    curl \
    wget \
    git \
    vim \
    nano \
    less \
    tree \
    jq \
    unzip \
    zip \
    tar \
    file \
    strings \
    xxd \
    # Python
    python3 \
    python3-pip \
    python3-venv \
    # Node.js
    nodejs \
    npm \
    # Rede
    nmap \
    netcat-openbsd \
    dnsutils \
    whois \
    traceroute \
    iputils-ping \
    tcpdump \
    # Web / HTTP
    httpie \
    nikto \
    dirb \
    # Análise de arquivos
    binutils \
    hexedit \
    # SSL/TLS
    openssl \
    ca-certificates \
    # Outros
    coreutils \
    findutils \
    grep \
    sed \
    awk \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Python packages ───────────────────────────────────────────────────────────
RUN pip3 install --no-cache-dir \
    requests \
    httpx \
    beautifulsoup4 \
    lxml \
    pyyaml \
    cryptography \
    scapy \
    paramiko \
    impacket \
    dnspython \
    shodan \
    ipwhois \
    semgrep \
    bandit \
    safety

# ── Node.js tools ─────────────────────────────────────────────────────────────
RUN npm install -g \
    retire \
    npm-audit-html \
    2>/dev/null || true

# ── Workspace ─────────────────────────────────────────────────────────────────
RUN mkdir -p /workspace /tmp/work

WORKDIR /workspace

# ── Entrypoint: mantém o container vivo ──────────────────────────────────────
CMD ["sleep", "infinity"]
