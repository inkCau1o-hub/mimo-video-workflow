import os
import yt_dlp

class VideoDownloader:
    def __init__(self, output_dir=None):
        self.output_dir = output_dir or "output"
        os.makedirs(self.output_dir, exist_ok=True)

    def download_video(self, url, filename=None):
        """
        下载视频
        支持平台: YouTube, TikTok, 抖音, B站等
        """
        if filename is None:
            outtmpl = os.path.join(self.output_dir, '%(title)s.%(ext)s')
        else:
            outtmpl = os.path.join(self.output_dir, f'{filename}.%(ext)s')

        ydl_opts = {
            'format': 'best',
            'outtmpl': outtmpl,
            'quiet': False,
            'no_warnings': False,
            'progress_hooks': [self._progress_hook],
        }

        print(f"[INFO] 正在下载视频: {url}")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                video_title = info.get('title', 'unknown')
                video_ext = info.get('ext', 'unknown')
                video_path = os.path.join(self.output_dir, f"{video_title}.{video_ext}")

                if not os.path.exists(video_path):
                    video_path = outtmpl.replace('%(ext)s', video_ext)

                print(f"[INFO] 视频下载完成: {video_path}")
                return video_path

        except Exception as e:
            print(f"[ERROR] 视频下载失败: {e}")
            raise

    def _progress_hook(self, d):
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', '0%')
            speed = d.get('_speed_str', 'N/A')
            eta = d.get('_eta_str', 'N/A')
            print(f"\r[下载中] {percent} | 速度: {speed} | 剩余: {eta}", end='', flush=True)
        elif d['status'] == 'finished':
            print(f"\n[INFO] 下载完成: {d['filename']}")

    def get_video_info(self, url):
        """
        获取视频信息
        """
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return {
                    'title': info.get('title', ''),
                    'duration': info.get('duration', 0),
                    'uploader': info.get('uploader', ''),
                    'upload_date': info.get('upload_date', ''),
                    'description': info.get('description', ''),
                    'thumbnail': info.get('thumbnail', ''),
                    'view_count': info.get('view_count', 0),
                    'like_count': info.get('like_count', 0),
                }
        except Exception as e:
            print(f"[ERROR] 获取视频信息失败: {e}")
            raise

    def download_audio_only(self, url, filename=None):
        """
        仅下载音频
        """
        if filename is None:
            outtmpl = os.path.join(self.output_dir, '%(title)s.%(ext)s')
        else:
            outtmpl = os.path.join(self.output_dir, f'{filename}.%(ext)s')

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': outtmpl,
            'quiet': False,
            'no_warnings': False,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'wav',
            }],
        }

        print(f"[INFO] 正在下载音频: {url}")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                video_title = info.get('title', 'unknown')
                audio_path = os.path.join(self.output_dir, f"{video_title}.wav")

                print(f"[INFO] 音频下载完成: {audio_path}")
                return audio_path

        except Exception as e:
            print(f"[ERROR] 音频下载失败: {e}")
            raise

    def list_formats(self, url):
        """
        列出可用格式
        """
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'listformats': True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=False)
        except Exception as e:
            print(f"[ERROR] 获取格式列表失败: {e}")
