import base64
from pathlib import Path

import requests

import config


class MimoTTSClient:
    """MiMo speech synthesis adapter using the OpenAI-compatible chat endpoint."""

    def __init__(self, api_key=None, api_url=None, model=None, voice=None):
        self.api_key = api_key or config.API_KEY
        self.api_url = api_url or config.API_URL
        self.model = model or config.TTS_API_MODEL
        self.voice = voice or config.TTS_VOICE

    def check_api_key(self):
        if not self.api_key:
            raise ValueError("AI API Key 未配置")

    def synthesize(
        self,
        text,
        output_path,
        style_prompt="用自然、清晰、适合短视频口播的语气朗读。",
        voice=None,
        audio_format="mp3",
        timeout=120,
    ):
        self.check_api_key()
        if not str(text or "").strip():
            raise ValueError("待合成文本不能为空")

        output = Path(output_path).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": style_prompt or "自然朗读。"},
                {"role": "assistant", "content": str(text).strip()},
            ],
            "audio": {"voice": voice or self.voice, "format": audio_format},
            "stream": False,
        }
        response = requests.post(
            self.api_url,
            headers=self._headers(),
            json=payload,
            timeout=timeout,
        )
        audio_data = self._parse_audio_response(response)
        output.write_bytes(base64.b64decode(audio_data))
        return output

    def synthesize_segments(
        self,
        segments,
        output_dir,
        style_prompt="用自然、清晰、适合短视频口播的语气朗读。",
        voice=None,
        audio_format="mp3",
    ):
        output_dir = Path(output_dir).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for index, segment in enumerate(segments, start=1):
            if isinstance(segment, dict):
                segment_id = segment.get("segment_id") or segment.get("shot_id") or f"segment_{index:03d}"
                text = segment.get("text") or segment.get("tts_text") or segment.get("script_text")
            else:
                segment_id = getattr(segment, "segment_id", None) or getattr(segment, "shot_id", None) or f"segment_{index:03d}"
                text = getattr(segment, "text", None) or getattr(segment, "tts_text", None) or getattr(segment, "script_text", None)

            safe_id = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(segment_id))
            output_path = output_dir / f"{safe_id}.{audio_format}"
            audio_path = self.synthesize(
                text=text,
                output_path=output_path,
                style_prompt=style_prompt,
                voice=voice,
                audio_format=audio_format,
            )
            results.append({"segment_id": str(segment_id), "audio_path": str(audio_path)})
        return results

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "api-key": self.api_key,
        }

    def _parse_audio_response(self, response):
        if response.status_code != 200:
            message = f"TTS API 请求失败: {response.status_code}"
            try:
                message += f" - {response.json()}"
            except Exception:
                message += f" - {response.text[:300]}"
            raise Exception(message)

        result = response.json()
        message = result.get("choices", [{}])[0].get("message", {})
        audio = message.get("audio") or {}
        audio_data = audio.get("data")
        if not audio_data:
            raise Exception(f"TTS API 未返回音频数据，响应字段: {sorted(message.keys())}")
        return audio_data
