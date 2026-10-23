"""Optional sibling feature modules (such as AI 动画) started on demand by the launcher.

MiMo itself is the "feed" module and is always available. Other modules live in their
own repositories and describe how to serve themselves in a ``launcher.json`` file:

    {"name": "AI 动画", "command": ["node", "scripts/launcher-serve.mjs"],
     "health_path": "/", "port_env": "PORT", "default_port": 5174}

The backend only starts, health-checks and stops those processes; it never calls
their business APIs, so the two features stay independent.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
ANIMATION_DIR_ENV = "MIMO_AI_ANIMATION_DIR"
ANIMATION_PORT_ENV = "MIMO_AI_ANIMATION_PORT"
MANIFEST_NAME = "launcher.json"
STATUS_STOPPED = "stopped"
STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_FAILED = "failed"

_EXTRA_BIN_DIRS = (
    Path.home() / ".local" / "bin",
    Path.home() / ".npm-global" / "bin",
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
)


class ModuleError(ValueError):
    pass


def animation_candidates(env: Optional[Dict[str, str]] = None) -> List[Path]:
    env = os.environ if env is None else env
    explicit = str(env.get(ANIMATION_DIR_ENV) or "").strip()
    if explicit:
        return [Path(explicit).expanduser()]
    return [REPO_ROOT / "modules" / "ai-animation", REPO_ROOT.parent / "ai-animation"]


def load_manifest(directory: Path) -> Dict[str, Any]:
    path = directory / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ModuleError(f"缺少 {MANIFEST_NAME}") from None
    except (OSError, ValueError) as exc:
        raise ModuleError(f"{MANIFEST_NAME} 无法读取：{exc}") from None
    command = data.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ModuleError(f"{MANIFEST_NAME} 的 command 必须是非空字符串数组")
    port = data.get("default_port", 5174)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ModuleError(f"{MANIFEST_NAME} 的 default_port 无效")
    health = str(data.get("health_path") or "/")
    if not health.startswith("/"):
        raise ModuleError(f"{MANIFEST_NAME} 的 health_path 必须以 / 开头")
    return {
        "name": str(data.get("name") or "AI 动画"),
        "command": list(command),
        "health_path": health,
        "port_env": str(data.get("port_env") or "PORT"),
        "default_port": port,
        "requires": [str(item) for item in data.get("requires", ["node_modules"])],
    }


def find_executable(name: str) -> Optional[str]:
    if os.path.isabs(name):
        return name if os.access(name, os.X_OK) else None
    found = shutil.which(name)
    if found:
        return found
    for directory in _EXTRA_BIN_DIRS:
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


@dataclass
class ModuleProcess:
    module_id: str
    name: str
    directory: Optional[Path] = None
    manifest: Optional[Dict[str, Any]] = None
    reason: str = ""
    status: str = STATUS_STOPPED
    port: int = 0
    error: str = ""
    process: Optional[subprocess.Popen] = None
    started_at: float = 0.0
    log_path: Optional[Path] = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def available(self) -> bool:
        return self.manifest is not None and not self.reason

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/" if self.port else ""


class ModuleManager:
    """Starts optional modules on demand and stops their whole process tree on exit."""

    def __init__(
        self,
        log_dir: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
        health_timeout: float = 90.0,
        probe: Optional[Callable[[str], bool]] = None,
    ):
        self.env = dict(os.environ if env is None else env)
        self.log_dir = Path(log_dir) if log_dir else REPO_ROOT / "output" / "logs"
        self.health_timeout = float(health_timeout)
        self.probe = probe or _http_ok
        self.animation = self._discover_animation()

    # ---- discovery -------------------------------------------------------------
    def _discover_animation(self) -> ModuleProcess:
        module = ModuleProcess("animation", "AI 动画")
        candidates = animation_candidates(self.env)
        directory = next((path for path in candidates if path.is_dir()), None)
        if directory is None:
            module.reason = "未找到 AI 动画目录：" + "、".join(str(path) for path in candidates)
            return module
        module.directory = directory.resolve()
        try:
            manifest = load_manifest(module.directory)
        except ModuleError as exc:
            module.reason = str(exc)
            return module
        module.name = manifest["name"]
        module.manifest = manifest
        missing = [item for item in manifest["requires"] if not (module.directory / item).exists()]
        if missing:
            module.reason = "依赖未安装（缺少 " + "、".join(missing) + "），请运行安装环境脚本"
        elif find_executable(manifest["command"][0]) is None:
            module.reason = f"找不到命令 {manifest['command'][0]}，请先安装 Node.js"
        port_text = str(self.env.get(ANIMATION_PORT_ENV) or "").strip()
        module.port = int(port_text) if port_text.isdigit() else manifest["default_port"]
        return module

    # ---- public API ------------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        with self.animation.lock:
            self._refresh(self.animation)
            animation = self._describe(self.animation)
        return {"modules": [
            {"id": "feed", "name": "信息流剪辑", "available": True, "status": STATUS_RUNNING},
            animation,
        ]}

    def start(self, module_id: str) -> Dict[str, Any]:
        module = self._get(module_id)
        with module.lock:
            self._refresh(module)
            if not module.available:
                raise ModuleError(module.reason or f"{module.name} 不可用")
            if module.status in {STATUS_STARTING, STATUS_RUNNING}:
                return self._describe(module)
            if self.probe(self._health_url(module)):
                # Another copy (for example a developer's own dev server) already serves this port.
                module.status, module.error = STATUS_RUNNING, ""
                return self._describe(module)
            self.log_dir.mkdir(parents=True, exist_ok=True)
            module.log_path = self.log_dir / f"{module.module_id}.log"
            env = {**self.env, module.manifest["port_env"]: str(module.port)}
            env["PATH"] = os.pathsep.join([env.get("PATH", ""), *map(str, _EXTRA_BIN_DIRS)])
            command = [find_executable(module.manifest["command"][0]) or module.manifest["command"][0],
                       *module.manifest["command"][1:]]
            log = open(module.log_path, "ab")
            try:
                module.process = subprocess.Popen(
                    command, cwd=str(module.directory), env=env, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, **_group_kwargs(),
                )
            except OSError as exc:
                module.status, module.error = STATUS_FAILED, f"启动失败：{exc}"
                return self._describe(module)
            finally:
                log.close()
            module.status, module.error, module.started_at = STATUS_STARTING, "", time.time()
        threading.Thread(target=self._wait_healthy, args=(module,), daemon=True).start()
        return self._describe(module)

    def stop(self, module_id: str) -> Dict[str, Any]:
        module = self._get(module_id)
        with module.lock:
            _terminate_tree(module.process)
            module.process = None
            module.status = STATUS_STOPPED
        return self._describe(module)

    def shutdown(self) -> None:
        try:
            self.stop("animation")
        except ModuleError:
            pass

    # ---- internals -------------------------------------------------------------
    def _get(self, module_id: str) -> ModuleProcess:
        if module_id != "animation":
            raise ModuleError(f"未知模块：{module_id}")
        return self.animation

    def _health_url(self, module: ModuleProcess) -> str:
        return f"http://127.0.0.1:{module.port}{module.manifest['health_path']}"

    def _refresh(self, module: ModuleProcess) -> None:
        process = module.process
        if process is not None and process.poll() is not None and module.status in {STATUS_STARTING, STATUS_RUNNING}:
            _terminate_tree(process, grace=1.0)  # leftovers of a crashed leader
            module.status = STATUS_FAILED
            module.error = f"进程已退出（代码 {process.returncode}），详见日志 {module.log_path}"
            module.process = None

    def _wait_healthy(self, module: ModuleProcess) -> None:
        deadline = module.started_at + self.health_timeout
        while time.time() < deadline:
            process = module.process
            if process is None or process.poll() is not None:
                with module.lock:
                    self._refresh(module)
                return
            if self.probe(self._health_url(module)):
                with module.lock:
                    if module.process is process:
                        module.status = STATUS_RUNNING
                return
            time.sleep(0.5)
        with module.lock:
            if module.status == STATUS_STARTING:
                _terminate_tree(module.process)
                module.process = None
                module.status = STATUS_FAILED
                module.error = f"{int(self.health_timeout)} 秒内未就绪，详见日志 {module.log_path}"

    def _describe(self, module: ModuleProcess) -> Dict[str, Any]:
        return {
            "id": module.module_id,
            "name": module.name,
            "available": module.available,
            "reason": module.reason,
            "status": module.status if module.available else STATUS_STOPPED,
            "error": module.error,
            "url": module.url if module.status == STATUS_RUNNING else "",
            "log_path": str(module.log_path) if module.log_path else "",
        }


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:  # noqa: S310 - fixed localhost URL
            return 200 <= response.status < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _group_kwargs() -> Dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_tree(process: Optional[subprocess.Popen], grace: float = 5.0) -> None:
    """Stop the module and everything it spawned (Node, Vite, render browsers).

    The module runs as the leader of its own process group, so the group id equals
    its pid and children are reached even after the leader itself has exited.
    """
    if process is None:
        return
    if sys.platform == "win32":
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
        return
    pgid = process.pid
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        deadline = time.time() + grace
        while time.time() < deadline:
            process.poll()
            if not _group_alive(pgid):
                break
            time.sleep(0.1)
        if not _group_alive(pgid):
            break
    process.poll()
