"""Shared timing contract for editable narration gaps and local subtitle cues."""

from copy import deepcopy
import math
from collections.abc import Mapping


def _seconds(value, label, maximum=3600):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}必须是有限数字。")
    if not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError(f"{label}必须在 0 到 {maximum} 秒之间。")
    return float(value)


def normalize_timing(edit):
    result = deepcopy(dict(edit))
    for key, label in (("lead_in_seconds", "配音前留白"), ("tail_out_seconds", "配音后留白")):
        if key in result:
            result[key] = _seconds(result[key], label, 10)
    subtitle = result.get("subtitle", {})
    if not isinstance(subtitle, Mapping):
        raise ValueError("字幕设置格式无效。")
    subtitle = dict(subtitle)
    if subtitle.get("timing_mode", "full") not in ("full", "narration", "cues"):
        raise ValueError("字幕时间模式无效。")
    if subtitle.get("timing_source", "manual") not in ("manual", "estimated"):
        raise ValueError("字幕时间来源无效。")
    cues = subtitle.get("cues", [])
    if not isinstance(cues, list) or len(cues) > 100:
        raise ValueError("每个镜头最多 100 条定时字幕。")
    previous_end, text_length = 0, 0
    normalized = []
    for cue in cues:
        if not isinstance(cue, Mapping):
            raise ValueError("定时字幕格式无效。")
        start = _seconds(cue.get("start"), "字幕开始")
        end = _seconds(cue.get("end"), "字幕结束")
        if end - start < .01 - 1e-9 or start < previous_end - 1e-9:
            raise ValueError("字幕需按时间排序、互不重叠，每条至少 0.01 秒。")
        text = cue.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("定时字幕正文不能为空。")
        text_length += len(text)
        if text_length > 10000:
            raise ValueError("每个镜头的定时字幕总计不能超过一万字。")
        normalized.append({"start": start, "end": end, "text": text})
        previous_end = end
    if "cues" in subtitle:
        subtitle["cues"] = normalized
    if "subtitle" in result:
        result["subtitle"] = subtitle
    return result


def shot_duration(edit, fallback, voice_duration=None):
    lead, tail = edit.get("lead_in_seconds", 0), edit.get("tail_out_seconds", 0)
    required = lead + (voice_duration or 0) + tail
    duration = required if voice_duration and edit.get("duration_mode", "narration") == "narration" else fallback
    if not math.isfinite(duration) or not 0 < duration <= 3600:
        raise ValueError("镜头总时长必须大于 0 且不超过一小时。")
    if duration + 1e-6 < required or (not voice_duration and lead + tail >= duration):
        raise ValueError("镜头时长不足以容纳完整配音和前后留白。")
    return duration


def validate_cue_bounds(subtitle, duration):
    if subtitle.get("timing_mode") == "cues" and any(cue["end"] > duration + 1e-6 for cue in subtitle.get("cues", [])):
        raise ValueError("定时字幕超出镜头总时长。")


def subtitle_events(text, edit, duration, voice_duration=None):
    subtitle = edit.get("subtitle", {})
    mode = subtitle.get("timing_mode", "full")
    validate_cue_bounds(subtitle, duration)
    if mode == "cues":
        return deepcopy(subtitle.get("cues", []))
    if not text.strip():
        return []
    start, end = 0, duration
    if mode == "narration":
        start = edit.get("lead_in_seconds", 0)
        end = min(duration, start + voice_duration) if voice_duration else duration - edit.get("tail_out_seconds", 0)
    return [{"start": start, "end": end, "text": text}]
