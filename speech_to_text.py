import os
import config

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = getattr(config, "MODELS_DIR", os.path.join(SCRIPT_DIR, "models"))

class SpeechToText:
    def __init__(self, model_size="base", output_dir=None):
        self.model_size = model_size
        self.output_dir = output_dir or config.OUTPUT_DIR
        self.model = None

    def load_model(self, progress_callback=None):
        """
        加载Whisper模型（仅CPU模式，仅从本地加载）
        """
        import time
        from faster_whisper import WhisperModel

        local_model_path = os.path.join(MODELS_DIR, f"faster-whisper-{self.model_size}")

        if progress_callback:
            progress_callback(10, "检查本地模型...")

        if os.path.exists(local_model_path) and os.path.isdir(local_model_path):
            if progress_callback:
                progress_callback(30, f"正在加载本地模型: {self.model_size}")
            print(f"[INFO] 正在从本地加载 faster-whisper 模型: {self.model_size}")
            start_time = time.time()
            try:
                self.model = WhisperModel(
                    local_model_path,
                    device="cpu",
                    compute_type="int8"
                )
                load_time = time.time() - start_time
                if progress_callback:
                    progress_callback(100, "模型加载完成")
                print(f"[INFO] 模型加载成功 (CPU模式, int8)，加载耗时: {load_time:.2f}秒")
                return True
            except Exception as e:
                print(f"[ERROR] 本地模型加载失败: {e}")
                if progress_callback:
                    progress_callback(0, f"加载失败: {e}")
                raise Exception(f"本地模型加载失败: {e}")

        available_models = []
        if os.path.exists(MODELS_DIR):
            for item in os.listdir(MODELS_DIR):
                item_path = os.path.join(MODELS_DIR, item)
                if os.path.isdir(item_path) and item.startswith("faster-whisper-"):
                    available_models.append(item.replace("faster-whisper-", ""))

        if progress_callback:
            progress_callback(0, f"模型 {self.model_size} 不存在。本地可用模型: {', '.join(available_models) if available_models else '无'}")

        raise Exception(f"本地模型 {self.model_size} 不存在，请先下载模型到 models/ 文件夹")

    def transcribe(self, audio_path, language="zh", progress_callback=None):
        """
        语音转文字
        """
        if self.model is None:
            self.load_model(progress_callback=progress_callback)

        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")

        if progress_callback:
            progress_callback(50, "正在识别语音...")

        print(f"[INFO] 正在识别音频: {audio_path}")

        try:
            import time
            start_time = time.time()

            segments, info = self.model.transcribe(
                audio_path,
                language=language,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500)
            )

            print(f"[INFO] 检测到语言: {info.language} (概率: {info.language_probability:.2f})")

            text_segments = []
            for segment in segments:
                text_segments.append(segment.text.strip())

            full_text = " ".join(text_segments)
            transcribe_time = time.time() - start_time

            if progress_callback:
                progress_callback(100, f"识别完成: {len(full_text)} 字符")

            print(f"[INFO] 语音识别完成，文字长度: {len(full_text)} 字符，耗时: {transcribe_time:.2f}秒")

            return full_text

        except Exception as e:
            print(f"[ERROR] 语音识别失败: {e}")
            raise

    def transcribe_with_timestamps(self, audio_path, language="zh"):
        """
        语音转文字(带时间戳)
        """
        if self.model is None:
            self.load_model()

        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")

        print(f"[INFO] 正在识别音频(带时间戳): {audio_path}")

        try:
            segments, info = self.model.transcribe(
                audio_path,
                language=language,
                beam_size=5,
                vad_filter=True
            )

            result_segments = []
            for segment in segments:
                result_segments.append({
                    'start': segment.start,
                    'end': segment.end,
                    'text': segment.text.strip()
                })

            print(f"[INFO] 语音识别完成，共 {len(result_segments)} 个片段")
            return result_segments

        except Exception as e:
            print(f"[ERROR] 语音识别失败: {e}")
            raise

    def save_transcript(self, text, filename=None):
        """
        保存转写文本
        """
        if filename is None:
            filename = config.TRANSCRIPT_FILENAME

        filepath = os.path.join(self.output_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(text)

        print(f"[INFO] 转写文本已保存: {filepath}")
        return filepath
