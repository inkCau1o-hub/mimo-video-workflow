import os
import sys

print("=== 开始诊断 ===")

print("\n[1] 检查 faster-whisper...")
from faster_whisper import WhisperModel
print("    faster-whisper 导入成功")

print("\n[2] 尝试加载模型...")
import time
start = time.time()

try:
    print("    尝试加载 base 模型 (CPU, int8)...")
    model = WhisperModel("base", device="cpu", compute_type="int8")
    print(f"    模型加载成功! 耗时: {time.time()-start:.2f}秒")

    print("\n[3] 尝试转录...")
    audio_path = r"c:\Users\Administrator\Desktop\12\output\audio.wav"
    segments, info = model.transcribe(audio_path, language="zh")
    print(f"    检测语言: {info.language}")

    text_segments = []
    for segment in segments:
        text_segments.append(segment.text.strip())

    full_text = " ".join(text_segments)
    print(f"    转录完成! 文字长度: {len(full_text)}")
    print(f"    结果: {full_text[:200]}...")

except Exception as e:
    print(f"    错误: {e}")
    import traceback
    traceback.print_exc()

print("\n=== 诊断结束 ===")
