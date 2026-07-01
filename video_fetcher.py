import yt_dlp
import json
import re
import config

class VideoFetcher:
    def __init__(self):
        self.ydl_opts = {
            'quiet': True,
            'ignoreerrors': True,
            'no_warnings': False,
            'extract_flat': False,
        }
        
        if config.PROXY_URL:
            self.ydl_opts['proxy'] = config.PROXY_URL

    def detect_url_type(self, url):
        """
        检测URL类型：单个视频 or 频道/播放列表
        """
        video_patterns = [
            r'youtube\.com/watch\?v=',
            r'youtu\.be/',
            r'bilibili\.com/video/',
            r'b23\.tv/',
            r'tiktok\.com/@',
            r'douyin\.com/video/',
            r'xiaohongshu\.com/discovery',
            r'weibo\.com/',
            r'twitter\.com/',
        ]
        
        for pattern in video_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return 'video'
        
        channel_patterns = [
            r'youtube\.com/@',
            r'youtube\.com/channel/',
            r'youtube\.com/c/',
            r'bilibili\.com/(?:space|channel)/',
            r'douyin\.com/user/',
            r'xiaohongshu\.com/user',
        ]
        
        for pattern in channel_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return 'channel'
        
        return 'unknown'

    def fetch_videos(self, url, limit=10):
        """
        获取页面视频列表
        """
        print(f"[INFO] 正在抓取页面视频列表: {url}")
        
        if config.PROXY_URL:
            print(f"[INFO] 使用代理: {config.PROXY_URL}")
        
        url_type = self.detect_url_type(url)
        print(f"[INFO] 检测到URL类型: {url_type}")
        
        opts = self.ydl_opts.copy()
        opts['extract_flat'] = 'in_playlist'
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    raise Exception("无法获取视频信息，请检查网址是否正确")
                
                entries = []
                
                if 'entries' in info:
                    entries = info['entries']
                elif 'videos' in info:
                    entries = info['videos']
                
                if not entries:
                    if info.get('url') or info.get('webpage_url'):
                        entries = [info]
                    else:
                        raise Exception("未找到任何视频，请确保网址是视频页面或频道页")
                
                videos = []
                for entry in entries:
                    if entry:
                        video_info = self._extract_video_info(entry)
                        if video_info:
                            videos.append(video_info)
                
                if not videos:
                    raise Exception("未能解析出视频信息")
                
                videos = videos[:limit]
                print(f"[INFO] 共抓取到 {len(videos)} 个视频")
                return videos
                
            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e)
                if 'HTTP Error 403' in error_msg:
                    raise Exception(f"访问被拒绝(HTTP 403)，可能原因：\n1. 需要使用代理/VPN访问该平台\n2. IP被平台限制\n3. 请尝试使用Bilibili等国内平台")
                elif 'Unsupported URL' in error_msg:
                    raise Exception(f"不支持的网址平台，当前支持: YouTube, Bilibili, 抖音(TikTok), 小红书, 快手, 微博, Twitter 等主流视频平台")
                elif 'HTTP Error' in error_msg:
                    raise Exception(f"网络请求失败，请检查网络连接或稍后重试: {error_msg}")
                else:
                    raise Exception(f"抓取失败: {error_msg}")
            except Exception as e:
                print(f"[ERROR] 抓取视频列表失败: {e}")
                raise

    def get_single_video(self, url):
        """
        获取单个视频信息
        """
        print(f"[INFO] 正在获取视频信息: {url}")
        
        if config.PROXY_URL:
            print(f"[INFO] 使用代理: {config.PROXY_URL}")
        
        opts = self.ydl_opts.copy()
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    raise Exception("无法获取视频信息，请检查网址是否正确")
                
                video_info = self._extract_video_info(info)
                
                if not video_info:
                    raise Exception("未能解析出视频信息")
                
                print(f"[INFO] 视频标题: {video_info['title']}")
                print(f"[INFO] 播放量: {video_info.get('view_count', 'N/A')}")
                
                return video_info
                
            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e)
                if 'HTTP Error 403' in error_msg:
                    raise Exception(f"访问被拒绝(HTTP 403)，可能原因：\n1. 需要使用代理/VPN访问该平台\n2. IP被平台限制\n3. 请尝试使用Bilibili等国内平台")
                elif 'Unsupported URL' in error_msg:
                    raise Exception(f"不支持的网址平台，当前支持: YouTube, Bilibili, 抖音(TikTok), 小红书, 快手, 微博, Twitter 等主流视频平台")
                elif 'HTTP Error' in error_msg:
                    raise Exception(f"网络请求失败，请检查网络连接或稍后重试")
                else:
                    raise Exception(f"获取视频失败: {error_msg}")
            except Exception as e:
                print(f"[ERROR] 获取视频信息失败: {e}")
                raise

    def _extract_video_info(self, entry):
        """
        提取单个视频信息
        """
        try:
            video_id = entry.get('id', '')
            title = entry.get('title', 'Unknown')
            
            view_count = entry.get('view_count', 0) or 0
            like_count = entry.get('like_count', 0) or 0
            comment_count = entry.get('comment_count', 0) or 0
            
            duration = entry.get('duration', 0) or 0
            
            url = entry.get('url', '')
            if not url:
                url = entry.get('webpage_url', '')
            
            if not url:
                return None
            
            return {
                'id': video_id,
                'title': title,
                'url': url,
                'view_count': view_count,
                'like_count': like_count,
                'comment_count': comment_count,
                'duration': duration
            }
            
        except Exception as e:
            print(f"[WARNING] 解析视频信息失败: {e}")
            return None

    def get_hottest_video(self, url):
        """
        获取最热视频 - 支持单个视频和频道
        """
        url_type = self.detect_url_type(url)
        
        if url_type == 'video':
            print("[INFO] 检测到单个视频链接，将直接获取该视频")
            return self.get_single_video(url)
        
        videos = self.fetch_videos(url, limit=20)
        
        if not videos:
            raise Exception("未找到任何视频")
        
        valid_videos = [v for v in videos if v.get('view_count', 0) and v.get('view_count', 0) > 0]
        
        if valid_videos:
            videos_sorted = sorted(
                valid_videos, 
                key=lambda x: x.get('view_count', 0), 
                reverse=True
            )
        else:
            videos_sorted = videos
        
        hottest = videos_sorted[0]
        print(f"[INFO] 最热视频: {hottest['title']}")
        print(f"[INFO] 播放量: {hottest.get('view_count', 'N/A')}")
        
        return hottest

    def get_video_info(self, url):
        """
        获取单个视频的详细信息
        """
        opts = self.ydl_opts.copy()
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                if info:
                    return self._extract_video_info(info)
                return None
            except Exception as e:
                print(f"[ERROR] 获取视频信息失败: {e}")
                return None
