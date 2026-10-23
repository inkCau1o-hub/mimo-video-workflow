import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


if __name__ == "__main__":
    unittest.main()
