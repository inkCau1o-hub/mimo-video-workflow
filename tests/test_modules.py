import json
import os
import socket
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.modules import (  # noqa: E402
    ANIMATION_DIR_ENV,
    ANIMATION_PORT_ENV,
    ModuleError,
    ModuleManager,
    load_manifest,
    pick_animation_dir,
)

SERVER = textwrap.dedent(
    """
    import http.server, os, subprocess, sys
    if os.environ.get("CRASH"):
        sys.exit(3)
    # A grandchild that must die with the module.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    open("child.pid", "w").write(str(child.pid))
    open("seen_key.txt", "w").write(os.environ.get("MIMO_API_KEY", ""))
    port = int(os.environ["PORT"])
    http.server.ThreadingHTTPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler).serve_forever()
    """
)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); treat it as gone.
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().split()[2] != "Z"
    except OSError:
        return True


class ModuleManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "ai-animation"
        self.dir.mkdir()
        (self.dir / "serve.py").write_text(SERVER, encoding="utf-8")
        self.manifest = {"name": "AI 动画", "command": [sys.executable, "serve.py"], "health_path": "/",
                         "port_env": "PORT", "default_port": 5174, "requires": []}
        self.write_manifest()
        self.port = free_port()
        self.env = {**os.environ, ANIMATION_DIR_ENV: str(self.dir), ANIMATION_PORT_ENV: str(self.port)}

    def tearDown(self):
        self.tmp.cleanup()

    def write_manifest(self, data=None):
        (self.dir / "launcher.json").write_text(json.dumps(data or self.manifest), encoding="utf-8")

    def manager(self, **kwargs):
        kwargs.setdefault("settings", lambda: {})
        return ModuleManager(log_dir=Path(self.tmp.name) / "logs", env=kwargs.pop("env", self.env), **kwargs)

    def wait_for(self, manager, status, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            item = manager.describe()["modules"][1]
            if item["status"] == status:
                return item
            time.sleep(0.2)
        self.fail(f"module did not reach {status}: {manager.describe()}")

    def test_feed_is_always_available_and_animation_missing_is_explained(self):
        env = {**os.environ, ANIMATION_DIR_ENV: str(Path(self.tmp.name) / "nowhere")}
        modules = self.manager(env=env).describe()["modules"]
        self.assertEqual(modules[0], {"id": "feed", "name": "信息流剪辑", "available": True, "status": "running"})
        self.assertFalse(modules[1]["available"])
        self.assertIn("未找到 AI 动画目录", modules[1]["reason"])
        self.assertIn(ANIMATION_DIR_ENV, modules[1]["reason"])
        with self.assertRaises(ModuleError):
            self.manager(env=env).start("animation")

    def test_empty_submodule_folder_falls_back_to_the_sibling_checkout(self):
        empty = Path(self.tmp.name) / "modules" / "ai-animation"
        empty.mkdir(parents=True)
        self.assertEqual(pick_animation_dir([empty, self.dir]), self.dir)
        self.assertEqual(pick_animation_dir([empty]), empty)  # still reported, as a submodule not checked out
        self.assertIsNone(pick_animation_dir([Path(self.tmp.name) / "nowhere"]))

    def test_empty_submodule_folder_alone_explains_how_to_get_it(self):
        empty = Path(self.tmp.name) / "modules" / "ai-animation"
        empty.mkdir(parents=True)
        item = self.manager(env={**os.environ, ANIMATION_DIR_ENV: str(empty)}).describe()["modules"][1]
        self.assertFalse(item["available"])
        self.assertIn("尚未取出", item["reason"])
        self.assertIn("安装环境脚本", item["reason"])

    def test_manifest_validation(self):
        for bad in ({"command": []}, {"command": "node x"}, {"command": ["node"], "default_port": 0},
                    {"command": ["node"], "health_path": "health"}):
            self.write_manifest(bad)
            with self.assertRaises(ModuleError):
                load_manifest(self.dir)
        (self.dir / "launcher.json").unlink()
        with self.assertRaisesRegex(ModuleError, "缺少"):
            load_manifest(self.dir)

    def test_missing_dependencies_block_start(self):
        self.write_manifest({**self.manifest, "requires": ["node_modules"]})
        item = self.manager().describe()["modules"][1]
        self.assertFalse(item["available"])
        self.assertIn("node_modules", item["reason"])

    def test_unknown_module_is_rejected(self):
        with self.assertRaises(ModuleError):
            self.manager().start("feed")

    def test_start_reports_running_url_and_stop_kills_the_whole_tree(self):
        manager = self.manager()
        first = manager.start("animation")
        self.assertEqual(first["status"], "starting")
        item = self.wait_for(manager, "running")
        self.assertEqual(item["url"], f"http://127.0.0.1:{self.port}/")
        self.assertEqual(manager.start("animation")["status"], "running")  # idempotent
        child_pid = int((self.dir / "child.pid").read_text())
        leader = manager.animation.process
        self.assertTrue(alive(child_pid))
        stopped = manager.stop("animation")
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["url"], "")
        deadline = time.time() + 5
        while alive(child_pid) and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(alive(child_pid), "grandchild survived stop()")
        self.assertIsNotNone(leader.poll())

    def test_shared_settings_reach_the_module_but_never_the_status_or_log(self):
        settings = {"MIMO_API_KEY": "sk-shared-123456", "MIMO_MODEL": "mimo-v2.5-pro"}
        manager = self.manager(settings=lambda: dict(settings))
        manager.start("animation")
        item = self.wait_for(manager, "running")
        try:
            self.assertEqual((self.dir / "seen_key.txt").read_text(), "sk-shared-123456")
            self.assertFalse(item["restart_required"])
            self.assertNotIn("sk-shared-123456", json.dumps(manager.describe(), ensure_ascii=False))
            settings["MIMO_API_KEY"] = "sk-rotated-654321"
            self.assertTrue(manager.describe()["modules"][1]["restart_required"])
            manager.restart("animation")
            self.wait_for(manager, "running")
            self.assertEqual((self.dir / "seen_key.txt").read_text(), "sk-rotated-654321")
            self.assertFalse(manager.describe()["modules"][1]["restart_required"])
        finally:
            manager.shutdown()
        log = Path(manager.animation.log_path).read_text(encoding="utf-8", errors="replace")
        self.assertNotIn("sk-", log)

    def test_crash_is_reported_as_failed_with_log(self):
        manager = self.manager(env={**self.env, "CRASH": "1"})
        manager.start("animation")
        item = self.wait_for(manager, "failed")
        self.assertIn("进程已退出", item["error"])
        self.assertTrue(item["log_path"].endswith("animation.log"))

    def test_existing_server_on_the_port_is_reused(self):
        manager = self.manager(probe=lambda url: True)
        self.assertEqual(manager.start("animation")["status"], "running")
        self.assertIsNone(manager.animation.process)

    def test_health_timeout_stops_the_process(self):
        manager = self.manager(probe=lambda url: False, health_timeout=1.5)
        manager.start("animation")
        item = self.wait_for(manager, "failed", timeout=10)
        self.assertIn("未就绪", item["error"])
        self.assertIsNone(manager.animation.process)

    def test_shutdown_is_safe_when_nothing_runs(self):
        self.manager().shutdown()


if __name__ == "__main__":
    unittest.main()


class ModuleRoutesTest(unittest.TestCase):
    def test_modules_routes(self):
        import threading
        import urllib.error
        import urllib.request

        from backend.server import create_server

        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get(ANIMATION_DIR_ENV)
            os.environ[ANIMATION_DIR_ENV] = str(Path(tmp) / "missing")
            try:
                server = create_server(port=0, projects_dir=tmp, quiet=True)
            finally:
                if previous is None:
                    os.environ.pop(ANIMATION_DIR_ENV, None)
                else:
                    os.environ[ANIMATION_DIR_ENV] = previous
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(f"{base}/modules") as response:
                    modules = json.loads(response.read())["modules"]
                self.assertEqual([item["id"] for item in modules], ["feed", "animation"])
                self.assertFalse(modules[1]["available"])
                request = urllib.request.Request(f"{base}/modules/animation/start", data=b"{}", method="POST",
                                                 headers={"Content-Type": "application/json"})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request)
                self.assertEqual(caught.exception.code, 400)
                self.assertIn("未找到", json.loads(caught.exception.read())["error"])
            finally:
                server.shutdown()
                server.modules.shutdown()
                server.server_close()
