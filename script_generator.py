import requests
import json
import os
import config

class ScriptGenerator:
    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or config.API_KEY
        self.model = model or config.API_MODEL
        self.api_url = config.API_URL

    def check_api_key(self):
        if not self.api_key:
            print("[ERROR] 请配置 AI API Key")
            return False
        return True

    def generate_script(self, transcript_text, product_name="", product_keywords=""):
        """
        生成观点型短视频口播脚本
        """
        if not self.check_api_key():
            raise ValueError("AI API Key 未配置")

        keywords_str = product_keywords if product_keywords else "产品相关"

        prompt = f"""你是一名短视频口播脚本专家。

以下是视频转写内容：

{transcript_text}

产品名称: {product_name}
产品关键词: {keywords_str}

请完成以下任务：
1. 提取视频中的核心观点
2. 重写为短视频口播脚本

脚本必须包含以下结构：

【开头钩子】
用一句有冲突或反常识的话吸引注意

【核心观点】

【解释】

【产品植入】

【总结】

要求：
字数300字以内
语言口语化
适合短视频口播
更有感染力和说服力

请直接输出脚本内容，不需要其他说明。"""

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
            "temperature": 0.8,
            "max_tokens": 2000
        }

        print(f"[INFO] 正在生成观点型短视频脚本: {self.model}")

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=60
            )

            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content']
                print(f"[INFO] 脚本生成完成，输出长度: {len(content)} 字符")
                return content
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
            print(f"[ERROR] 网络请求失败: {e}")
            raise

    def generate_script_with_custom_prompt(self, transcript_text, custom_prompt):
        """
        使用自定义Prompt生成脚本
        """
        if not self.check_api_key():
            raise ValueError("AI API Key 未配置")

        prompt = f"""以下是视频转写内容：

{transcript_text}

{custom_prompt}"""

        return self._call_api(prompt)

    def generate_multiple_scripts(self, transcript_text, product_name="", product_keywords="", count=3):
        """
        生成多个版本的脚本
        """
        if not self.check_api_key():
            raise ValueError("AI API Key 未配置")

        keywords_str = product_keywords if product_keywords else "产品相关"

        prompt = f"""你是一名短视频口播脚本专家。

以下是视频转写内容：

{transcript_text}

产品名称: {product_name}
产品关键词: {keywords_str}

请生成{count}个不同风格的短视频口播脚本。

每个脚本必须包含以下结构：

【开头钩子】
用一句有冲突或反常识的话吸引注意

【核心观点】

【解释】

【产品植入】

【总结】

要求：
每个脚本300字以内
语言口语化
适合短视频口播
风格可以有变化（亲和、激进、知识感等）

请直接输出{count}个脚本，用分隔线分开。"""

        return self._call_api(prompt)
