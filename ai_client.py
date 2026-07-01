import base64
import mimetypes
from pathlib import Path

import requests

import config


class OpenAICompatibleClient:
    def __init__(self, api_key=None, api_url=None, model=None):
        self.api_key = api_key or config.API_KEY
        self.api_url = api_url or config.API_URL
        self.model = model or config.API_MODEL

    def check_api_key(self):
        if not self.api_key:
            raise ValueError("AI API Key 未配置")

    def chat(self, messages, model=None, temperature=0.7, max_tokens=2000, timeout=60):
        self.check_api_key()
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        response = requests.post(
            self.api_url,
            headers=self._headers(),
            json=payload,
            timeout=timeout,
        )
        return self._parse_response(response)

    def analyze_images(self, prompt, image_paths, model=None, temperature=0.2, max_tokens=1200, timeout=90):
        """
        Reserved multimodal entrypoint for MiMo/OpenAI-compatible vision models.
        The current app does not assume which MiMo model will be used for vision;
        callers can pass a vision-capable model when available.
        """
        self.check_api_key()
        content = [{"type": "text", "text": prompt}]
        for image_path in image_paths:
            content.append({"type": "image_url", "image_url": {"url": self._image_data_url(image_path)}})

        return self.chat(
            [{"role": "user", "content": content}],
            model=model or self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _parse_response(self, response):
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        if response.status_code == 401:
            raise Exception("API Key 无效，请检查配置")
        if response.status_code == 429:
            raise Exception("API 请求频率超限，请稍后再试")

        message = f"API请求失败: {response.status_code}"
        try:
            message += f" - {response.json()}"
        except Exception:
            message += f" - {response.text[:300]}"
        raise Exception(message)

    def _image_data_url(self, image_path):
        path = Path(image_path).expanduser()
        mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"
