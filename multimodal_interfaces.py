import json
import os
import subprocess
from pathlib import Path

try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = "ffmpeg"

from ai_client import OpenAICompatibleClient


MATERIAL_ANALYSIS_PROMPT = """你是短视频素材分析助手。
请基于抽帧图片判断素材类型，并输出简洁 JSON。

字段要求:
- material_type: 素材类型，例如 product_demo, talking_head, screen_recording, lifestyle, food, beauty, fitness, other
- scene_summary: 画面内容摘要
- visual_style: 画面风格
- useful_for_copywriting: 对文案生成有帮助的信息
- risks: 可能不能夸大的点或需要谨慎描述的点
"""


class FrameExtractor:
    def __init__(self, ffmpeg_path=None):
        self.ffmpeg_path = ffmpeg_path or FFMPEG_PATH

    def extract_frames(self, video_path, output_dir, max_frames=6, every_seconds=3):
        video = Path(video_path).expanduser()
        if not video.exists():
            raise FileNotFoundError(f"视频文件不存在: {video}")

        frame_dir = Path(output_dir).expanduser() / "frames" / self._safe_name(video.stem)
        frame_dir.mkdir(parents=True, exist_ok=True)
        output_pattern = frame_dir / "frame_%03d.jpg"

        fps_value = 1 / max(every_seconds, 1)
        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vf",
            f"fps={fps_value}",
            "-frames:v",
            str(max_frames),
            "-q:v",
            "2",
            "-y",
            str(output_pattern),
        ]
        subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)

        return sorted(str(path) for path in frame_dir.glob("frame_*.jpg"))

    def _safe_name(self, name):
        return "".join(ch if ch not in '/\\:*?"<>|' else "_" for ch in name)


class MaterialAnalyzer:
    def __init__(self, client=None, extractor=None):
        self.client = client or OpenAICompatibleClient()
        self.extractor = extractor or FrameExtractor()

    def extract_only(self, video_path, output_dir, max_frames=6, every_seconds=3):
        return self.extractor.extract_frames(video_path, output_dir, max_frames=max_frames, every_seconds=every_seconds)

    def analyze_video_material(self, video_path, output_dir, vision_model=None, max_frames=6, every_seconds=3):
        frames = self.extract_only(video_path, output_dir, max_frames=max_frames, every_seconds=every_seconds)
        if not frames:
            raise Exception("未能抽取视频帧")

        response = self.client.analyze_images(
            MATERIAL_ANALYSIS_PROMPT,
            frames,
            model=vision_model,
        )

        return {
            "video_path": str(Path(video_path).expanduser()),
            "frames": frames,
            "analysis": response,
        }

    def save_analysis(self, result, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return output_path
