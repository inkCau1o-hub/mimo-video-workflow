import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audio_extractor import AudioExtractor
from speech_to_text import SpeechToText

video_path = r"C:\Users\Administrator\Desktop\e1c5e3c96df1af3e7978521e8df9c427_raw.mp4"
output_dir = r"C:\Users\Administrator\Desktop\12\output"

print("=" * 50)
print("测试语音识别流程 (GPU模式)")
print("=" * 50)

print("\n[步骤1] 提取音频...")
start_time = time.time()
extractor = AudioExtractor(output_dir=output_dir)
audio_path = extractor.extract_audio(video_path)
print(f"音频提取完成，耗时: {time.time() - start_time:.2f}秒")

print("\n[步骤2] 加载Whisper模型...")
start_time = time.time()
stt = SpeechToText(model_size="large-v3", output_dir=output_dir)
stt.load_model()
print(f"模型加载耗时: {time.time() - start_time:.2f}秒")

print("\n[步骤3] 语音识别...")
start_time = time.time()
transcript_text = stt.transcribe(audio_path, language="zh")
print(f"语音识别耗时: {time.time() - start_time:.2f}秒")
print(f"识别文字长度: {len(transcript_text)} 字符")
print(f"\n识别结果:\n{transcript_text}")
