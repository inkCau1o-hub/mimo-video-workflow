"""Extract a titled PNG from an existing export without modifying its video."""

from collections.abc import Mapping
from fractions import Fraction
import math
import os
from pathlib import Path
import re
import shutil
import tempfile

from media_render import (RenderError, _Runner, _probe, _stream_duration,
                          _select_ffmpeg, discover_executable, discover_font, publish_new, _write_subtitles)


DEFAULT_COVER_SETTINGS = {"time_seconds": 0., "title": "", "position": "top", "font_size": 64,
                          "color": "#FFFFFF", "background": True}


def validate_cover_settings(value):
    if not isinstance(value, Mapping) or set(value) - set(DEFAULT_COVER_SETTINGS):
        raise ValueError("封面设置格式无效。")
    result = {**DEFAULT_COVER_SETTINGS, **value}
    for key, low, high in (("time_seconds", 0, 1800000), ("font_size", 16, 96)):
        number = result[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not low <= number <= high:
            raise ValueError("封面时间或字号无效。")
    if not isinstance(result["title"], str) or len(result["title"]) > 80 or len(result["title"].splitlines()) > 3:
        raise ValueError("封面标题最多 80 字、3 行。")
    if result["position"] not in ("top", "center", "bottom") or not isinstance(result["background"], bool):
        raise ValueError("封面位置或背景无效。")
    if not isinstance(result["color"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", result["color"]):
        raise ValueError("封面颜色必须为 #RRGGBB。")
    return result


def render_cover(source, output, settings, *, cancel_check=None, progress_callback=None, process_timeout=120):
    try:
        return _render_cover(source, output, validate_cover_settings(settings), cancel_check, progress_callback, process_timeout)
    except (RenderError, ValueError):
        raise
    except Exception:
        raise RenderError("cover_failed", "封面生成失败，请检查成片文件、字体或可用磁盘空间。") from None


def _render_cover(source, output, settings, cancel, progress, timeout):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file() or source == output or output.suffix.lower() != ".png":
        raise ValueError("封面需要有效的本地成片和独立 PNG 输出。")
    if output.exists():
        raise ValueError("封面文件已存在，未覆盖。")
    runner = _Runner(cancel, progress, timeout)
    runner.check()
    runner.report(0)
    ffmpeg = _select_ffmpeg(runner, None, bool(settings["title"].strip()), source.parent)
    ffprobe = discover_executable("ffprobe", sibling=Path(ffmpeg))
    info = _probe(runner, ffprobe, source, source.parent)
    duration = _stream_duration(info, "video")
    if settings["time_seconds"] >= duration:
        raise ValueError("封面选帧时间必须早于成片结尾。")
    video = next(stream for stream in info["streams"] if stream.get("codec_type") == "video")
    try:
        # The export's average rate drifts slightly when its duration is rounded.
        fps = float(Fraction(video.get("r_frame_rate") or video.get("avg_frame_rate") or "0"))
    except (ValueError, ZeroDivisionError):
        raise ValueError("成片帧率无效。") from None
    if not 0 < fps <= 120 or any(not 1 <= video.get(key, 0) <= 4096 for key in ("width", "height")):
        raise ValueError("成片帧率或画面尺寸无效。")
    try:
        count = int(video.get("nb_frames") or "")
    except (ValueError, TypeError):
        count = math.ceil(duration * fps)
    frame = max(0, min(count - 1, math.floor(settings["time_seconds"] * fps + 1e-8)))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mimo-cover-", dir=output.parent) as temp:
        work = Path(temp)
        filters = [f"select=eq(n\\,{frame})", "setpts=PTS-STARTPTS"]
        if settings["title"].strip():
            font, family = discover_font()
            (work / "fonts").mkdir()
            shutil.copyfile(font, work / "fonts" / ("cover" + font.suffix))
            style = {key: settings[key] for key in ("position", "font_size", "color", "background")}
            _write_subtitles(work / "title.ass", settings["title"], 1, video["width"], video["height"], family, style, lines_per_page=100)
            filters.append("ass=filename=title.ass:fontsdir=fonts")
        runner.report(.2)
        runner.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-threads", "2",
                    "-filter_threads", "2", "-protocol_whitelist", "file,pipe", "-i", str(source),
                    "-map", "0:v:0", "-vf", ",".join(filters), "-frames:v", "1", "-an", "-c:v", "png",
                    "-update", "1", "-map_metadata", "-1", "cover.png"], work, "cover extraction")
        staged = work / "cover.png"
        check = _probe(runner, ffprobe, staged, work)
        stream = next(item for item in check["streams"] if item.get("codec_type") == "video")
        if stream.get("codec_name") != "png" or any(stream.get(key) != video[key] for key in ("width", "height")):
            raise RenderError("invalid_output", "封面尺寸或格式校验失败。")
        runner.report(.95)
        runner.check()
        publish_new(staged, output)
        result = {"image_path": str(output), "source_video_path": str(source), "settings": settings,
                  "frame_index": frame, "frame_time_seconds": frame / fps,
                  "width": video["width"], "height": video["height"], "size_bytes": output.stat().st_size}
        runner.report(1, committed=True)
        return result
