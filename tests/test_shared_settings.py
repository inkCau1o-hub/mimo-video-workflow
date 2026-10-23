import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402


class SharedModelSettingsTest(unittest.TestCase):
    def test_config_file_is_used_when_env_is_silent(self):
        values = config.shared_model_settings(env={}, user_config={"api_key": " sk-file ", "api_model": "m1"})
        self.assertEqual(values["MIMO_API_KEY"], "sk-file")
        self.assertEqual(values["MIMO_MODEL"], "m1")
        self.assertEqual(values["MIMO_BASE_URL"], config.DEFAULT_API_BASE_URL)
        self.assertEqual(values["MIMO_VISION_MODEL"], config.DEFAULT_VISION_API_MODEL)

    def test_environment_wins_with_the_same_order_as_mimo(self):
        env = {"MIMO_API_KEY": "sk-mimo", "OPENAI_API_KEY": "sk-openai", "MIMO_BASE_URL": "https://example.test/v1"}
        values = config.shared_model_settings(env=env, user_config={"api_key": "sk-file"})
        self.assertEqual(values["MIMO_API_KEY"], "sk-openai")
        self.assertEqual(values["MIMO_BASE_URL"], "https://example.test/v1")

    def test_missing_key_is_left_out_rather_than_empty(self):
        values = config.shared_model_settings(env={}, user_config={})
        self.assertNotIn("MIMO_API_KEY", values)
        self.assertEqual(set(values), {"MIMO_BASE_URL", "MIMO_MODEL", "MIMO_VISION_MODEL"})


class UnavailableFoldersTest(unittest.TestCase):
    def test_import_survives_folders_on_a_disconnected_drive(self):
        with tempfile.TemporaryDirectory() as root:
            blocker = Path(root) / "not-a-folder"
            blocker.write_text("", encoding="utf-8")  # nothing can be created below a file, not even by root
            settings = Path(root) / "config.json"
            settings.write_text(json.dumps({"output_dir": str(blocker / "output"), "models_dir": str(blocker / "models")}),
                                encoding="utf-8")
            env = {key: value for key, value in os.environ.items() if key not in ("OUTPUT_DIR", "MODELS_DIR")}
            env["VIDEO_SCRIPT_CONFIG_PATH"] = str(settings)
            done = subprocess.run([sys.executable, "-c", "import config; print(config.OUTPUT_DIR)"], cwd=ROOT, env=env,
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("not-a-folder", done.stdout)


if __name__ == "__main__":
    unittest.main()
