import requests
import json
import os
import config
from ai_client import parse_chat_text, text_generation_options

class TitleGenerator:
    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or config.API_KEY
        self.model = model or config.API_MODEL
        self.api_url = config.API_URL

    def check_api_key(self):
        if not self.api_key:
            print("[ERROR] 请配置 AI API Key")
            return False
        self.api_key = config.validate_api_key(self.api_key)
        return True

    def generate_titles(self, script_text, count=5):
        """
        根据脚本生成短视频标题
        """
        if not self.check_api_key():
            raise ValueError("AI API Key 未配置")

        prompt = f"""根据下面的短视频脚本生成{count}个标题

要求：
1. 20字以内
2. 有吸引力
3. 情绪化
4. 有冲突
5. 适合短视频平台（抖音、小红书、TikTok）
6. 每行一个标题

脚本：

{script_text}

输出："""

        return self._call_api(prompt)

    def generate_titles_from_transcript(self, transcript_text, count=5):
        """
        根据转写文本生成标题
        """
        if not self.check_api_key():
            raise ValueError("AI API Key 未配置")

        prompt = f"""根据下面的视频转写内容生成{count}个短视频标题

要求：
1. 20字以内
2. 有吸引力
3. 情绪化
4. 有冲突
5. 适合短视频平台（抖音、小红书、TikTok）
6. 每行一个标题

转写内容：

{transcript_text}

输出："""

        return self._call_api(prompt)

    def _call_api(self, prompt):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.9,
            **text_generation_options(self.model, 500)
        }

        print(f"[INFO] 正在生成短视频标题: {self.model}")

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                content = parse_chat_text(result)
                print(f"[INFO] 标题生成完成")
                titles = self._parse_titles(content)
                return titles
            elif response.status_code == 401:
                raise Exception("API Key 无效，请检查配置")
            elif response.status_code == 429:
                raise Exception("API 请求频率超限，请稍后再试")
            else:
                error_msg = f"API请求失败: {response.status_code}"
                try:
                    error_detail = response.json()
                    error_msg += f" - {error_detail}"
                except:
                    pass
                raise Exception(error_msg)

        except requests.exceptions.RequestException as e:
            print(f"[ERROR] 网络请求失败: {type(e).__name__}")
            raise

    def _parse_titles(self, content):
        titles = []
        lines = content.strip().split('\n')
        for line in lines:
            line = line.strip()
            line = line.lstrip('0123456789.、）)').strip()
            if line and len(line) > 0:
                titles.append(line)
        return titles

    def save_titles(self, titles, output_path):
        """
        保存标题到文件
        """
        with open(output_path, 'w', encoding='utf-8') as f:
            for title in titles:
                f.write(title + '\n')
        print(f"[INFO] 标题已保存: {output_path}")
        return output_path
