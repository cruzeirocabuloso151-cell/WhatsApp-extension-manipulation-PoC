"""
Docker Sandbox Manager
Substitui E2B: cada sessão ganha um container Docker isolado.
"""
from __future__ import annotations

import os
import asyncio
import uuid
import time
from dataclasses import dataclass, field
from typing import Optional

import docker
import docker.errors
import structlog

log = structlog.get_logger(__name__)

SANDBOX_IMAGE   = os.getenv("SANDBOX_IMAGE", "hackerai-sandbox:latest")
SANDBOX_NETWORK = os.getenv("SANDBOX_NETWORK", "hackerai_sandbox_net")
SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "300"))
MAX_SANDBOXES   = int(os.getenv("MAX_CONCURRENT_SANDBOXES", "4"))

# Limites de recursos por sandbox
SANDBOX_MEM_LIMIT = "512m"
SANDBOX_CPU_QUOTA = 50000  # 0.5 CPU (microseconds per 100ms period)


@dataclass
class SandboxInfo:
    sandbox_id: str
    container_id: str
    container_name: str
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)


class DockerSandboxManager:
    """
    Gerencia containers Docker como sandboxes de execução.
    Interface compatível com o conceito de SandboxManager do HackerAI.
    """

    def __init__(self):
        self._client = docker.from_env()
        self._sandboxes: dict[str, SandboxInfo] = {}  # session_id → SandboxInfo
        self._lock = asyncio.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def get_or_create(self, session_id: str) -> SandboxInfo:
        """Retorna sandbox existente ou cria um novo para a sessão."""
        async with self._lock:
            if session_id in self._sandboxes:
                info = self._sandboxes[session_id]
                # Verifica se container ainda está rodando
                if await self._is_running(info.container_id):
                    info.last_used = time.time()
                    return info
                else:
                    log.warning("sandbox.dead", session_id=session_id)
                    del self._sandboxes[session_id]

            if len(self._sandboxes) >= MAX_SANDBOXES:
                await self._evict_oldest()

            return await self._create(session_id)

    async def _create(self, session_id: str) -> SandboxInfo:
        sandbox_id = uuid.uuid4().hex[:12]
        container_name = f"hackerai-sandbox-{sandbox_id}"

        log.info("sandbox.create", session_id=session_id, name=container_name)

        loop = asyncio.get_event_loop()
        container = await loop.run_in_executor(
            None,
            lambda: self._client.containers.run(
                SANDBOX_IMAGE,
                name=container_name,
                detach=True,
                network=SANDBOX_NETWORK,
                mem_limit=SANDBOX_MEM_LIMIT,
                cpu_quota=SANDBOX_CPU_QUOTA,
                # Sem acesso à rede externa por padrão
                # environment para o sandbox
                environment={
                    "SANDBOX_SESSION": session_id,
                    "HOME": "/root",
                    "TERM": "xterm-256color",
                },
                working_dir="/workspace",
                # Segurança
                cap_drop=["ALL"],
                cap_add=["NET_BIND_SERVICE"],
                security_opt=["no-new-privileges:true"],
                # Sem volume no host (isolado)
                tmpfs={"/tmp": "size=100m"},
                labels={
                    "hackerai.session": session_id,
                    "hackerai.sandbox_id": sandbox_id,
                },
            ),
        )

        info = SandboxInfo(
            sandbox_id=sandbox_id,
            container_id=container.id,
            container_name=container_name,
            session_id=session_id,
        )
        self._sandboxes[session_id] = info
        return info

    async def destroy(self, session_id: str) -> None:
        """Remove o sandbox da sessão."""
        async with self._lock:
            if session_id not in self._sandboxes:
                return
            info = self._sandboxes.pop(session_id)
            await self._kill_container(info.container_id)

    async def destroy_all(self) -> None:
        """Remove todos os sandboxes (shutdown)."""
        async with self._lock:
            for info in list(self._sandboxes.values()):
                await self._kill_container(info.container_id)
            self._sandboxes.clear()

    # ── Execution ─────────────────────────────────────────────────────────────

    async def exec(
        self,
        session_id: str,
        command: str,
        timeout: int = 30,
        workdir: str = "/workspace",
        env: dict[str, str] | None = None,
    ) -> dict:
        """
        Executa comando no sandbox da sessão.
        Retorna {"stdout": str, "stderr": str, "exit_code": int, "timed_out": bool}
        """
        info = await self.get_or_create(session_id)
        info.last_used = time.time()

        loop = asyncio.get_event_loop()

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self._run_exec(
                        info.container_id, command, workdir, env or {}
                    ),
                ),
                timeout=timeout,
            )
            return result
        except asyncio.TimeoutError:
            log.warning("sandbox.exec.timeout", session_id=session_id, cmd=command[:80])
            return {
                "stdout": "",
                "stderr": f"[Timeout após {timeout}s]",
                "exit_code": 124,
                "timed_out": True,
            }
        except Exception as e:
            log.error("sandbox.exec.error", error=str(e))
            return {
                "stdout": "",
                "stderr": f"[Erro: {e}]",
                "exit_code": 1,
                "timed_out": False,
            }

    def _run_exec(
        self, container_id: str, command: str, workdir: str, env: dict
    ) -> dict:
        container = self._client.containers.get(container_id)
        exec_result = container.exec_run(
            cmd=["bash", "-c", command],
            workdir=workdir,
            environment=env,
            demux=True,
        )
        stdout_bytes, stderr_bytes = exec_result.output or (b"", b"")
        return {
            "stdout": (stdout_bytes or b"").decode("utf-8", errors="replace"),
            "stderr": (stderr_bytes or b"").decode("utf-8", errors="replace"),
            "exit_code": exec_result.exit_code or 0,
            "timed_out": False,
        }

    async def write_file(
        self, session_id: str, path: str, content: str
    ) -> None:
        """Escreve arquivo no sandbox."""
        import tarfile
        import io

        info = await self.get_or_create(session_id)
        loop = asyncio.get_event_loop()

        content_bytes = content.encode("utf-8")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info_obj = tarfile.TarInfo(name=os.path.basename(path))
            info_obj.size = len(content_bytes)
            tar.addfile(info_obj, io.BytesIO(content_bytes))
        buf.seek(0)

        dirpath = os.path.dirname(path) or "/workspace"

        await loop.run_in_executor(
            None,
            lambda: self._client.containers.get(info.container_id).put_archive(
                dirpath, buf
            ),
        )

    async def read_file(self, session_id: str, path: str) -> str:
        """Lê arquivo do sandbox."""
        result = await self.exec(session_id, f"cat {path}", timeout=10)
        if result["exit_code"] != 0:
            raise FileNotFoundError(f"Cannot read {path}: {result['stderr']}")
        return result["stdout"]

    async def list_files(self, session_id: str, path: str = "/workspace") -> list[str]:
        """Lista arquivos no diretório do sandbox."""
        result = await self.exec(session_id, f"find {path} -maxdepth 2 -type f", timeout=10)
        if result["exit_code"] != 0:
            return []
        return [l for l in result["stdout"].splitlines() if l.strip()]

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _is_running(self, container_id: str) -> bool:
        loop = asyncio.get_event_loop()
        try:
            container = await loop.run_in_executor(
                None, lambda: self._client.containers.get(container_id)
            )
            return container.status == "running"
        except docker.errors.NotFound:
            return False

    async def _kill_container(self, container_id: str) -> None:
        loop = asyncio.get_event_loop()
        try:
            container = await loop.run_in_executor(
                None, lambda: self._client.containers.get(container_id)
            )
            await loop.run_in_executor(None, lambda: container.remove(force=True))
            log.info("sandbox.removed", container_id=container_id[:12])
        except docker.errors.NotFound:
            pass
        except Exception as e:
            log.warning("sandbox.remove_error", error=str(e))

    async def _evict_oldest(self) -> None:
        """Remove o sandbox mais antigo quando atingir o limite."""
        if not self._sandboxes:
            return
        oldest_session = min(
            self._sandboxes, key=lambda s: self._sandboxes[s].last_used
        )
        log.info("sandbox.evict", session_id=oldest_session)
        info = self._sandboxes.pop(oldest_session)
        await self._kill_container(info.container_id)

    def list_sandboxes(self) -> list[dict]:
        return [
            {
                "session_id": info.session_id,
                "sandbox_id": info.sandbox_id,
                "container": info.container_name,
                "age_s": int(time.time() - info.created_at),
                "idle_s": int(time.time() - info.last_used),
            }
            for info in self._sandboxes.values()
        ]


# Singleton
_sandbox_manager: Optional[DockerSandboxManager] = None


def get_sandbox_manager() -> DockerSandboxManager:
    global _sandbox_manager
    if _sandbox_manager is None:
        _sandbox_manager = DockerSandboxManager()
    return _sandbox_manager
