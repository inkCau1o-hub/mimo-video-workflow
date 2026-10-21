"""Validated, portable audio settings and FFmpeg loudness/mixing filters."""

import json
import math
from collections.abc import Mapping


DEFAULT_AUDIO_SETTINGS = {
    "normalize_voice": False, "target_lufs": -16.0, "voice_gain_db": 0.0,
    "music_path": "", "music_gain_db": -20.0, "duck_music": True,
    "loop_music": True, "fade_in_seconds": 1.0, "fade_out_seconds": 1.5,
}


def validate_audio_settings(value=None):
    if value is None:
        value = {}
    if not isinstance(value, Mapping) or set(value) - set(DEFAULT_AUDIO_SETTINGS):
        raise ValueError("声音设置包含未知项目或格式无效。")
    result = {**DEFAULT_AUDIO_SETTINGS, **value}
    for key in ("normalize_voice", "duck_music", "loop_music"):
        if not isinstance(result[key], bool):
            raise ValueError(f"{key} 必须是开关值。")
    for key, minimum, maximum in (("target_lufs", -24, -12), ("voice_gain_db", -24, 12),
                                   ("music_gain_db", -48, 0), ("fade_in_seconds", 0, 10), ("fade_out_seconds", 0, 10)):
        number = result[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not minimum <= number <= maximum:
            raise ValueError(f"{key} 必须是 {minimum} 到 {maximum} 之间的数字。")
        result[key] = float(number)
    path = result["music_path"]
    if not isinstance(path, str) or len(path) > 4096 or any(ord(char) < 32 for char in path):
        raise ValueError("背景音乐路径无效。")
    result["music_path"] = path.strip()
    return result


def voice_processing(runner, ffmpeg, clip, settings, work):
    from media_render import RenderError

    filters = []
    metrics = {"normalized": False, "input_lufs": None, "silent_or_too_quiet": False}
    if settings["normalize_voice"]:
        target = settings["target_lufs"]
        raw = runner.run([
            ffmpeg, "-hide_banner", "-nostdin", "-v", "info", "-protocol_whitelist", "file,pipe",
            "-ss", str(clip["start"]), "-i", str(clip["path"]), "-map", "0:a:0", "-vn",
            "-t", str(clip["available"]), "-af",
            f"aformat=channel_layouts=stereo,loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json",
            "-f", "null", "-",
        ], work, "narration loudness measurement", capture_stderr=True)
        try:
            text = raw.decode("utf-8", errors="replace")
            measured, _ = json.JSONDecoder().raw_decode(text[text.rindex("{"):].lstrip())
            values = {key: float(measured[key]) for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
        except (ValueError, KeyError, TypeError):
            raise RenderError("invalid_audio_measurement", "Unable to measure narration loudness.") from None
        loudness = values["input_i"]
        if loudness == -math.inf or loudness < -70:
            metrics["silent_or_too_quiet"] = True
        elif not all(math.isfinite(value) for value in values.values()):
            raise RenderError("invalid_audio_measurement", "Narration loudness measurement was invalid.")
        else:
            metrics.update(normalized=True, input_lufs=loudness)
            filters += ["aformat=channel_layouts=stereo",
                        f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={values['input_i']}:"
                        f"measured_TP={values['input_tp']}:measured_LRA={values['input_lra']}:"
                        f"measured_thresh={values['input_thresh']}:offset={values['target_offset']}:linear=true"]
    if settings["voice_gain_db"]:
        filters.append(f"volume={settings['voice_gain_db']}dB")
    return filters, metrics


def assembly_audio_filters(settings, total, music_duration=None):
    """Inputs: concatenated narration at 0, optional music at 1. Output: [mixed]."""
    common = f"aresample=48000:async=1:first_pts=0,apad,atrim=duration={total:.9f},asetpts=PTS-STARTPTS"
    graph = [f"[0:a:0]{common}[voice]"]
    if music_duration is not None:
        audible = total if settings["loop_music"] else min(total, music_duration)
        fades = []
        for direction, requested in (("in", settings["fade_in_seconds"]), ("out", settings["fade_out_seconds"])):
            duration = min(audible, requested)
            if duration > 0:
                start = 0 if direction == "in" else max(0, audible - duration)
                fades.append(f"afade=t={direction}:st={start:.9f}:d={duration:.9f}")
        music = f"[1:a:0]{common},aformat=channel_layouts=stereo"
        if fades:
            music += "," + ",".join(fades)
        graph.append(music + f",volume={settings['music_gain_db']}dB[music]")
        if settings["duck_music"]:
            graph += ["[voice]asplit=2[narration][sidechain]",
                      "[music][sidechain]sidechaincompress=threshold=0.015:ratio=8:attack=20:release=350:makeup=1[ducked]",
                      "[narration][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[sum]"]
        else:
            graph.append("[voice][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[sum]")
        source = "sum"
    else:
        source = "voice"
    # Preserve legacy audio when all processing is off. Never auto-amplify the limiter output.
    if music_duration is not None or settings["normalize_voice"] or settings["voice_gain_db"]:
        graph.append(f"[{source}]alimiter=limit=0.891251:level=false:latency=true,aresample=48000[mixed]")
    else:
        graph.append(f"[{source}]anull[mixed]")
    return ";".join(graph)
