import os
import sys
import time
import threading
import queue

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audio_extractor import AudioExtractor
from speech_to_text import SpeechToText

import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(
    description="测试语音识别流程：提取音频并用 base 模型转写",
    usage="python %(prog)s VIDEO_PATH [--output-dir OUTPUT_DIR]",
)
parser.add_argument("video_path", help="要测试的视频文件路径")
parser.add_argument(
    "--output-dir",
    default=os.path.join(SCRIPT_DIR, "output"),
    help="音频输出目录（默认：项目目录下的 output/）",
)
args = parser.parse_args()

video_path = args.video_path
output_dir = args.output_dir

print("=" * 50)
print("测试语音识别流程")
print("=" * 50)

print("\n[步骤1] 提取音频...")
start_time = time.time()
extractor = AudioExtractor(output_dir=output_dir)
audio_path = extractor.extract_audio(video_path)
print(f"音频提取完成，耗时: {time.time() - start_time:.2f}秒")

print("\n[步骤2] 直接测试 medium 模型...")

model_size = "base"
print(f"使用模型: {model_size}")

try:
    start_time = time.time()
    stt = SpeechToText(model_size=model_size, output_dir=output_dir)

    print("[步骤2.1] 开始加载模型...")
    stt.load_model()
    load_time = time.time() - start_time
    print(f"模型加载完成，耗时: {load_time:.2f}秒")

    print("[步骤2.2] 开始语音识别...")
    start_time = time.time()
    transcript_text = stt.transcribe(audio_path, language="zh")
    transcribe_time = time.time() - start_time
    print(f"语音识别完成，耗时: {transcribe_time:.2f}秒")
    print(f"识别文字长度: {len(transcript_text)} 字符")
    print(f"识别结果: {transcript_text[:200]}...")

except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
