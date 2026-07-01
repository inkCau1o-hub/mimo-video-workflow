import requests
import json
import os
import config

class AIRewriter:
    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or config.API_KEY
        self.model = model or config.API_MODEL
        self.api_url = config.API_URL

    def check_api_key(self):
        """
        检查API Key是否配置
        """
        if not self.api_key:
            print("[ERROR] 请配置 AI API Key")
            return False
        return True

    def generate_prompt(self, transcript_text, keywords):
        """
        生成Prompt
        """
        keywords_str = ", ".join(keywords) if isinstance(keywords, list) else keywords
        
        prompt = f"""以下是一个视频内容转写文本：

{transcript_text}

请围绕以下产品关键词重新改写内容：
{keywords_str}

要求：
1. 保留核心信息
2. 改写表达方式
3. 更适合短视频口播
4. 控制在300字以内
5. 更口语化、更有营销感

请直接输出改写后的文案，不需要其他说明。"""

        return prompt

    def rewrite(self, transcript_text, keywords):
        """
        调用 OpenAI 兼容 API 改写内容
        """
        if not self.check_api_key():
            raise ValueError("AI API Key 未配置")
        
        prompt = self.generate_prompt(transcript_text, keywords)
        
        return self._call_api(prompt)

    def rewrite_with_prompt(self, prompt):
        """
        使用自定义Prompt调用 OpenAI 兼容 API 改写内容
        """
        if not self.check_api_key():
            raise ValueError("AI API Key 未配置")
        
        return self._call_api(prompt)
    
    def _call_api(self, prompt):
        """
        调用 OpenAI 兼容 API
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 2000
        }
        
        print(f"[INFO] 正在调用 AI API 改写文案: {self.model}")
        
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
                print(f"[INFO] AI改写完成，输出长度: {len(content)} 字符")
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

    def generate_script(self, transcript_text, keywords):
        """
        生成短视频脚本(带结构)
        """
        if not self.check_api_key():
            raise ValueError("AI API Key 未配置")
        
        keywords_str = ", ".join(keywords) if isinstance(keywords, list) else keywords
        
        prompt = f"""以下是一个视频内容转写文本：

{transcript_text}

请围绕以下产品关键词生成一个短视频脚本：
{keywords_str}

要求：
1. 生成开场白(3-5秒)
2. 核心内容描述(30-60秒)
3. 产品植入(10-20秒)
4. 引导互动(3-5秒)
5. 总时长控制在1分钟左右
6. 口语化、有感染力

请直接输出脚本内容。"""

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 2000
        }
        
        print("[INFO] 正在生成短视频脚本...")
        
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
                print(f"[INFO] 脚本生成完成")
                return content
            else:
                raise Exception(f"API请求失败: {response.status_code}")
                
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] 网络请求失败: {e}")
            raise

    def generate_title(self, content, keywords):
        """
        生成标题
        """
        if not self.check_api_key():
            raise ValueError("AI API Key 未配置")
        
        keywords_str = ", ".join(keywords) if isinstance(keywords, list) else keywords
        
        prompt = f"""基于以下内容生成5个吸引人的短视频标题：

内容：{content}

产品关键词：{keywords_str}

要求：
1. 每个标题要吸引眼球
2. 体现产品卖点
3. 适合短视频平台风格
4. 格式：每行一个标题"""

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
            "max_tokens": 500
        }
        
        print("[INFO] 正在生成标题...")
        
        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content']
                return content
            else:
                raise Exception(f"API请求失败: {response.status_code}")
                
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] 网络请求失败: {e}")
            raise
