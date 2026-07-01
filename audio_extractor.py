import subprocess
import os
import config

try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = 'ffmpeg'

class AudioExtractor:
    def __init__(self, output_dir=None):
        self.output_dir = output_dir or config.OUTPUT_DIR
        os.makedirs(self.output_dir, exist_ok=True)

    def extract_audio(self, video_path, audio_filename=None):
        """
        从视频中提取音频
        """
        if audio_filename is None:
            audio_filename = config.AUDIO_FILENAME
        
        audio_path = os.path.join(self.output_dir, audio_filename)
        
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")
        
        print(f"[INFO] 正在提取音频...")
        
        cmd = [
            FFMPEG_PATH,
            '-i', video_path,
            '-vn',
            '-acodec', 'pcm_s16le',
            '-ar', '16000',
            '-ac', '1',
            '-y',
            audio_path
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            print(f"[INFO] 音频提取完成: {audio_path}")
            return audio_path
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] 音频提取失败: {e}")
            print(f"[ERROR] ffmpeg输出: {e.stderr}")
            raise
        except FileNotFoundError:
            print("[ERROR] ffmpeg未安装，请先安装ffmpeg")
            raise

    def extract_audio_mp3(self, video_path, audio_filename=None):
        """
        从视频中提取音频(MP3格式)
        """
        if audio_filename is None:
            audio_filename = "audio.mp3"
        
        audio_path = os.path.join(self.output_dir, audio_filename)
        
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")
        
        print(f"[INFO] 正在提取音频(MP3)...")
        
        cmd = [
            FFMPEG_PATH,
            '-i', video_path,
            '-vn',
            '-acodec', 'libmp3lame',
            '-q:a', '2',
            '-y',
            audio_path
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            print(f"[INFO] 音频提取完成: {audio_path}")
            return audio_path
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] 音频提取失败: {e}")
            raise
