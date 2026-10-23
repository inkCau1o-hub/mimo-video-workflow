"""Unified launcher end-to-end check on macOS or Linux, run with the project venv:

    .venv/bin/python tests/launcher_e2e.py [--animation-dir PATH] [--keep]

Starts the backend on spare ports with a throwaway config and key, then checks that AI 动画
starts on demand, receives the shared key only through its environment, picks up a changed
key after a restart, reports a crash without affecting 信息流剪辑, leaves no process or port
behind after stop or a backend SIGTERM, and that no log or response contains the key.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_VARIABLES = ("API_KEY", "OPENAI_API_KEY", "MIMO_API_KEY", "DEEPSEEK_API_KEY")


def free_port(taken: int = 0) -> int:
    while True:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port != taken:
            return port


def listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def fetch(url: str, method: str = "GET") -> tuple[int, str]:
    request = urllib.request.Request(url, method=method, data=b"{}" if method == "POST" else None,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - localhost only
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as error:
        return 0, str(error)


def wait_until(predicate, timeout: float, interval: float = 0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def listener_pid(port: int) -> int | None:
    found = subprocess.run(["lsof", "-t", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True)
    pids = [int(line) for line in found.stdout.split()]
    return pids[0] if pids else None


def environment_entries(pid: int) -> list[str]:
    if sys.platform.startswith("linux"):
        return Path(f"/proc/{pid}/environ").read_bytes().decode("utf-8", "replace").split("\0")
    found = subprocess.run(["ps", "-wwE", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    return found.stdout.split()


def working_directory(pid: int) -> str:
    found = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True)
    return next((line[1:] for line in found.stdout.splitlines() if line.startswith("n")), "")


def group_alive(pgid: int | None) -> bool:
    if not pgid:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unified launcher end-to-end check")
    parser.add_argument("--animation-dir", default="", help="AI 动画 folder; default is the launcher's own lookup")
    parser.add_argument("--keep", action="store_true", help="keep the temporary folder and its logs")
    args = parser.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="mimo_launcher_e2e_")).resolve()
    key_a, key_b = f"sk-e2e-{secrets.token_hex(12)}", f"sk-e2e-{secrets.token_hex(12)}"
    config_path = work / "config.json"
    config_path.write_text(json.dumps({"api_key": key_a}), encoding="utf-8")
    backend_port = free_port()
    animation_port = free_port(backend_port)
    env = {name: value for name, value in os.environ.items() if name not in KEY_VARIABLES}
    env.pop("MIMO_AI_ANIMATION_DIR", None)
    env.update({
        "VIDEO_SCRIPT_CONFIG_PATH": str(config_path),
        "VIDEO_SCRIPT_MEMORY_PATH": str(work / "memory.json"),
        "OUTPUT_DIR": str(work / "output"),
        "MODELS_DIR": str(work / "models"),
        "MIMO_AI_ANIMATION_PORT": str(animation_port),
    })
    if args.animation_dir:
        env["MIMO_AI_ANIMATION_DIR"] = str(Path(args.animation_dir).expanduser().resolve())

    results: list[tuple[str, bool]] = []
    bodies: list[str] = []
    base = f"http://127.0.0.1:{backend_port}"

    def check(name: str, ok: object, detail: str = "") -> bool:
        results.append((name, bool(ok)))
        print(("PASS  " if ok else "FAIL  ") + name + (f"：{detail}" if detail and not ok else ""), flush=True)
        return bool(ok)

    def api(method: str, path: str) -> tuple[int, dict]:
        status, body = fetch(base + path, method)
        bodies.append(body)
        try:
            return status, json.loads(body)
        except ValueError:
            return status, {}

    def animation() -> dict:
        modules = api("GET", "/modules")[1].get("modules", [])
        return next((item for item in modules if item.get("id") == "animation"), {})

    def wait_status(expected: set[str], timeout: float) -> dict:
        def reached():
            current = animation()
            return current if current.get("status") in expected else None
        return wait_until(reached, timeout) or animation()

    def settle() -> dict:
        return wait_status({"running", "failed"}, 120)

    def module_process() -> tuple[int | None, int | None]:
        pid = listener_pid(animation_port)
        return pid, (os.getpgid(pid) if pid else None)

    print(f"临时目录：{work}\n后端端口 {backend_port}，AI 动画端口 {animation_port}", flush=True)
    log = open(work / "backend.log", "wb")
    backend = subprocess.Popen(
        [sys.executable, "-m", "backend.server", "--port", str(backend_port), "--projects-dir", str(work / "projects")],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    pgid = None
    try:
        if not check("后端启动", wait_until(lambda: fetch(base + "/health")[0] == 200, 30)):
            return 1
        module = animation()
        check("首页列出 AI 动画且可用", module.get("available") and module.get("status") == "stopped",
              json.dumps(module, ensure_ascii=False))

        api("POST", "/modules/animation/start")
        module = settle()
        check("按需启动 AI 动画", module.get("status") == "running", module.get("error", ""))
        status, page = fetch(f"http://127.0.0.1:{animation_port}/")
        bodies.append(page)
        check("工作台页面可访问", status == 200, str(status))
        bodies.append(fetch(f"http://127.0.0.1:{animation_port}/@vite/env")[1])
        pid, pgid = module_process()
        directory = working_directory(pid) if pid else ""
        print(f"      AI 动画目录：{directory}", flush=True)
        check("AI 动画目录带 launcher.json", directory and (Path(directory) / "launcher.json").is_file(), directory)
        entries = environment_entries(pid) if pid else []
        check("AI 动画进程环境里有 MIMO_API_KEY", f"MIMO_API_KEY={key_a}" in entries)

        config_path.write_text(json.dumps({"api_key": key_b}), encoding="utf-8")
        check("改密钥后提示重启", animation().get("restart_required") is True)
        api("POST", "/modules/animation/restart")
        module = settle()
        pid, pgid = module_process()
        entries = environment_entries(pid) if pid else []
        check("重启后用上新密钥", module.get("status") == "running" and f"MIMO_API_KEY={key_b}" in entries
              and f"MIMO_API_KEY={key_a}" not in entries)
        check("重启后不再提示", animation().get("restart_required") is False)

        os.kill(pid, signal.SIGKILL)
        crashed = wait_status({"failed"}, 15)
        check("AI 动画崩溃后显示失败和原因", "进程已退出" in crashed.get("error", ""), json.dumps(crashed, ensure_ascii=False))
        check("AI 动画崩溃不影响信息流剪辑", fetch(base + "/health")[0] == 200 and api("GET", "/projects")[0] == 200)
        check("崩溃后无残留进程", wait_until(lambda: not group_alive(pgid), 10))
        api("POST", "/modules/animation/start")
        check("崩溃后可重新启动", settle().get("status") == "running")
        pid, pgid = module_process()

        api("POST", "/modules/animation/stop")
        check("停止后状态为已停止", animation().get("status") == "stopped")
        check("停止后无残留进程", wait_until(lambda: not group_alive(pgid), 10))
        check("停止后端口释放", wait_until(lambda: not listening(animation_port), 10))

        api("POST", "/modules/animation/start")
        check("再次启动", settle().get("status") == "running")
        pid, pgid = module_process()
        backend.send_signal(signal.SIGTERM)
        try:
            backend.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass
        check("后端收到 SIGTERM 后退出", backend.poll() is not None)
        check("后端退出后无残留 AI 动画进程", pid and wait_until(lambda: not group_alive(pgid), 10))
        check("后端退出后端口全部释放",
              wait_until(lambda: not listening(animation_port) and not listening(backend_port), 10))
    finally:
        if backend.poll() is None:
            backend.kill()
            backend.wait()
        log.close()
        if group_alive(pgid):
            os.killpg(pgid, signal.SIGKILL)

    module_log = work / "projects" / ".logs" / "animation.log"
    check("AI 动画日志已写出", module_log.is_file() and module_log.stat().st_size > 0, str(module_log))
    scanned = [path for path in work.rglob("*") if path.is_file() and path != config_path]
    leaks = [str(path) for path in scanned
             if key_a.encode() in path.read_bytes() or key_b.encode() in path.read_bytes()]
    check(f"日志和输出里搜不到密钥（扫描 {len(scanned)} 个文件）", not leaks, "、".join(leaks))
    check(f"接口响应和工作台页面里搜不到密钥（{len(bodies)} 个响应）",
          not any(key_a in body or key_b in body for body in bodies))

    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过" + (f"；失败：{'、'.join(failed)}" if failed else ""))
    if args.keep:
        print(f"日志保留在 {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
