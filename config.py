import json
import os
import sys
from pathlib import Path


APP_NAME = "短视频文案生成工具"


def validate_api_key(value):
    key = str(value or "").strip()
    if not key:
        raise ValueError("AI API Key 未配置")
    if not key.isascii() or any(char.isspace() or ord(char) < 32 for char in key):
        raise ValueError("API Key 格式无效，请只填写密钥，不要包含地址、说明或换行。")
    return key

WORKING_DIR = Path(__file__).resolve().parent

DEFAULT_API_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"

DEFAULT_API_MODEL = "mimo-v2.5-pro"

DEFAULT_VISION_API_MODEL = "mimo-v2.5"

DEFAULT_TTS_API_MODEL = "mimo-v2.5-tts"

DEFAULT_TTS_VOICE = "mimo_default"

API_PROVIDER_NAME = "MiMo Coding Plan"

AUDIO_FILENAME = "audio.wav"

TRANSCRIPT_FILENAME = "transcript.txt"

OUTPUT_FILENAME = "output.txt"

PROXY_URL = ""


def _default_config_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        appdata = os.getenv("APPDATA")
        return Path(appdata) / APP_NAME if appdata else Path.home() / APP_NAME
    return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def _default_output_dir():
    return Path.home() / "Documents" / "短视频文案输出"


CONFIG_DIR = _default_config_dir()
CONFIG_PATH = Path(os.getenv("VIDEO_SCRIPT_CONFIG_PATH", CONFIG_DIR / "config.json"))
MEMORY_PATH = Path(os.getenv("VIDEO_SCRIPT_MEMORY_PATH", CONFIG_DIR / "memory.json"))


def load_user_config():
    if not CONFIG_PATH.exists():
        return {}

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _chat_completions_url(base_url):
    base_url = str(base_url or DEFAULT_API_BASE_URL).rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def save_user_config(
    api_key=None,
    api_base_url=None,
    api_model=None,
    vision_api_model=None,
    tts_api_model=None,
    tts_voice=None,
    output_dir=None,
    proxy_url=None,
    models_dir=None,
):
    data = load_user_config()

    if api_key is not None:
        data["api_key"] = api_key
        data.pop("deepseek_api_key", None)
    if api_base_url is not None:
        data["api_base_url"] = api_base_url
    if api_model is not None:
        data["api_model"] = api_model
    if vision_api_model is not None:
        data["vision_api_model"] = vision_api_model
    if tts_api_model is not None:
        data["tts_api_model"] = tts_api_model
    if tts_voice is not None:
        data["tts_voice"] = tts_voice
    if output_dir is not None:
        data["output_dir"] = str(Path(output_dir).expanduser())
    if proxy_url is not None:
        data["proxy_url"] = proxy_url
    if models_dir is not None:
        data["models_dir"] = str(Path(models_dir).expanduser())

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return data


_USER_CONFIG = load_user_config()

API_KEY = (
    os.getenv("API_KEY")
    or os.getenv("OPENAI_API_KEY")
    or os.getenv("MIMO_API_KEY")
    or os.getenv("DEEPSEEK_API_KEY")
    or _USER_CONFIG.get("api_key")
    or _USER_CONFIG.get("deepseek_api_key")
    or ""
)

API_BASE_URL = (
    os.getenv("API_BASE_URL")
    or os.getenv("OPENAI_BASE_URL")
    or os.getenv("MIMO_BASE_URL")
    or _USER_CONFIG.get("api_base_url")
    or DEFAULT_API_BASE_URL
)

API_URL = _chat_completions_url(API_BASE_URL)

API_MODEL = (
    os.getenv("API_MODEL")
    or os.getenv("OPENAI_MODEL")
    or os.getenv("MIMO_MODEL")
    or _USER_CONFIG.get("api_model")
    or DEFAULT_API_MODEL
)

VISION_API_MODEL = (
    os.getenv("VISION_API_MODEL")
    or os.getenv("MIMO_VISION_MODEL")
    or _USER_CONFIG.get("vision_api_model")
    or DEFAULT_VISION_API_MODEL
)

TTS_API_MODEL = (
    os.getenv("TTS_API_MODEL")
    or os.getenv("MIMO_TTS_MODEL")
    or _USER_CONFIG.get("tts_api_model")
    or DEFAULT_TTS_API_MODEL
)

TTS_VOICE = (
    os.getenv("TTS_VOICE")
    or os.getenv("MIMO_TTS_VOICE")
    or _USER_CONFIG.get("tts_voice")
    or DEFAULT_TTS_VOICE
)

# Backward-compatible names for older modules and configs.
DEEPSEEK_API_KEY = API_KEY
DEEPSEEK_API_URL = API_URL
DEEPSEEK_MODEL = API_MODEL

OUTPUT_DIR = str(
    Path(
        os.getenv("OUTPUT_DIR")
        or _USER_CONFIG.get("output_dir")
        or _default_output_dir()
    ).expanduser()
)

MODELS_DIR = str(
    Path(
        os.getenv("MODELS_DIR")
        or _USER_CONFIG.get("models_dir")
        or WORKING_DIR / "models"
    ).expanduser()
)

PROXY_URL = os.getenv("PROXY_URL") or _USER_CONFIG.get("proxy_url") or ""

UI_FONT_FAMILY = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei"

Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
Path(MODELS_DIR).mkdir(parents=True, exist_ok=True)
