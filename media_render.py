"""Private FFmpeg execution helpers; public entry point is media_assembler.render."""

from __future__ import annotations

import json
import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from audio_mix import validate_audio_settings, voice_processing, assembly_audio_filters
from shot_timing import normalize_timing, shot_duration, subtitle_events
from visual_edit import image_motion, validate_visual_edit, resolve_framing, transition_windows


class RenderError(RuntimeError):
    """A redacted, user-facing rendering failure with a stable machine code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class RenderCancelled(RenderError):
    def __init__(self):
        super().__init__("cancelled", "Rendering cancelled; no output was published.")


def discover_executable(name: str, requested: str | None = None, sibling: Path | None = None) -> str:
    """Find native binaries even when the process inherited an old Windows PATH."""
    candidates = []
    if requested:
        found = shutil.which(str(requested))
        if found:
            candidates.append(Path(found))
        candidates.append(Path(requested).expanduser())
    suffix = ".exe" if os.name == "nt" else ""
    if sibling:
        candidates.append(sibling.parent / (name + suffix))
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    if os.name == "nt":
        for variable, default in (("ProgramFiles", "C:/Program Files"), ("LOCALAPPDATA", "")):
            root = Path(os.environ.get(variable, default))
            candidates.append(root / "WinGet" / "Links" / (name + suffix))
            candidates.append(root / "Microsoft" / "WinGet" / "Links" / (name + suffix))
            for package_dir in (root / "WinGet" / "Packages", root / "Microsoft" / "WinGet" / "Packages"):
                if package_dir.is_dir():
                    for package in sorted(package_dir.glob("*FFmpeg*")):
                        candidates.extend(sorted(package.glob("**/bin/" + name + suffix)))
        candidates.extend([
            Path("C:/ffmpeg/bin") / (name + suffix),
            Path(os.environ.get("ProgramData", "C:/ProgramData")) / "chocolatey/bin" / (name + suffix),
            Path.home() / "scoop/apps/ffmpeg/current/bin" / (name + suffix),
        ])
    else:
        candidates.extend(Path(root) / name for root in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"))
    for candidate in candidates:
        if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return str(candidate.resolve())
    raise RenderError("missing_tool", "FFmpeg and FFprobe must be installed for real rendering.")


def _local_path(value: Any, base: Path) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise RenderError("invalid_path", "A required local media path is missing.")
    text = str(value)
    if any(ord(char) < 32 for char in text):
        raise RenderError("invalid_path", "A media path contains unsupported control characters.")
    if text.lower().startswith("file:"):
        parts = urlsplit(text)
        text = unquote(parts.path)
        if parts.netloc and parts.netloc.lower() != "localhost":
            text = "//" + parts.netloc + text
        elif os.name == "nt" and re.match(r"^/[A-Za-z]:", text):
            text = text[1:]
    elif re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text) and not re.match(r"^[A-Za-z]:[/\\]", text):
        raise RenderError("invalid_path", "Only local media files can be rendered.")
    path = Path(text).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def _number(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise RenderError("invalid_plan", "Media timing must contain finite numbers.") from None
    if not math.isfinite(result) or result < 0:
        raise RenderError("invalid_plan", "Media timing must contain finite nonnegative numbers.")
    return result


def _validated_edit(value: Any) -> dict:
    """Validate the editor contract without interpolating unchecked filter syntax."""
    if not isinstance(value, Mapping):
        raise RenderError("invalid_plan", "metadata.edit must be an object.")
    edit = dict(source_in=0.0, source_out=None, fit="cover", crop_x=.5, crop_y=.5,
                shortage="freeze", duration_mode="narration")
    edit.update(value)
    for key in ("source_in", "source_out", "crop_x", "crop_y"):
        number = edit[key]
        if key == "source_out" and number is None:
            continue
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise RenderError("invalid_plan", f"edit.{key} must be a finite number.")
        edit[key] = _number(number)
    if edit["source_out"] is not None and edit["source_out"] <= edit["source_in"]:
        raise RenderError("invalid_plan", "edit.source_out must be greater than source_in.")
    if any(edit[key] > 1 for key in ("crop_x", "crop_y")):
        raise RenderError("invalid_plan", "edit.crop_x and crop_y must be between zero and one.")
    for key, choices in (("fit", ("cover", "contain")), ("shortage", ("freeze", "error")),
                         ("duration_mode", ("narration", "manual"))):
        if edit[key] not in choices:
            raise RenderError("invalid_plan", f"edit.{key} has an unsupported value.")
    subtitle = value.get("subtitle", {})
    if not isinstance(subtitle, Mapping):
        raise RenderError("invalid_plan", "edit.subtitle must be an object.")
    style = dict(position="bottom", font_size=48, color="#FFFFFF", background=False)
    style.update(subtitle)
    if "text" in style and not isinstance(style["text"], str):
        raise RenderError("invalid_plan", "edit.subtitle.text must be a string.")
    if style["position"] not in ("top", "center", "bottom"):
        raise RenderError("invalid_plan", "edit.subtitle.position must be top, center or bottom.")
    size = style["font_size"]
    if isinstance(size, bool) or not isinstance(size, (int, float)) or not 16 <= size <= 96:
        raise RenderError("invalid_plan", "edit.subtitle.font_size must be between 16 and 96.")
    if not isinstance(style["color"], str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", style["color"]):
        raise RenderError("invalid_plan", "edit.subtitle.color must be #RRGGBB.")
    if not isinstance(style["background"], bool):
        raise RenderError("invalid_plan", "edit.subtitle.background must be a boolean.")
    edit["subtitle"] = style
    try:
        return validate_visual_edit(normalize_timing(edit))
    except ValueError as exc:
        raise RenderError("invalid_plan", str(exc)) from None


def _plan_rows(plan: Mapping) -> tuple[list[dict], Mapping]:
    if not isinstance(plan, Mapping):
        raise RenderError("invalid_plan", "An assembly plan is required.")
    metadata = plan.get("metadata") or {}
    nested = metadata.get("dict_plan") or metadata.get("assembly_plan") or {}
    timeline = plan.get("timeline") or metadata.get("timeline") or nested.get("timeline") or {}
    rows = []
    # Schema items are authoritative, so edits are not shadowed by dict_plan snapshots.
    if plan.get("items"):
        original = {str(s.get("shot_id") or s.get("segment_id")): s for s in nested.get("segments", [])}
        assets = metadata.get("assets") or nested.get("assets") or []
        voices = metadata.get("voiceovers") or nested.get("voiceovers") or []
        for item in plan["items"]:
            meta = item.get("metadata") or {}
            old = original.get(str(item.get("shot_id")), {})
            video = meta.get("video_clip")
            if not video:
                asset = next((a for a in assets if a.get("asset_id") == item.get("material_asset_id")), {})
                video = {"source_path": asset.get("path") or asset.get("file_path")}
            audio = meta.get("voiceover_clip")
            if not audio and item.get("voiceover_segment_id"):
                voice = next((a for a in voices if (a.get("voiceover_id") or a.get("segment_id")) == item["voiceover_segment_id"]), {})
                audio = {"source_path": voice.get("audio_path"), "text": voice.get("text", "")}
            rows.append({"video": video, "audio": audio, "duration": item.get("duration_seconds"),
                         "text": meta.get("script_text", old.get("script_text", "")),
                         "tts_text": meta.get("tts_text", old.get("tts_text", "")),
                         "edit": meta.get("edit", (video or {}).get("edit"))})
    elif plan.get("segments"):
        for segment in plan["segments"]:
            rows.append({"video": segment.get("video_clip"), "audio": segment.get("voiceover_clip"),
                         "duration": segment.get("duration"), "text": segment.get("script_text", ""),
                         "tts_text": segment.get("tts_text", ""),
                         "edit": segment.get("edit", (segment.get("video_clip") or {}).get("edit"))})
    elif plan.get("tracks") or metadata.get("tracks"):
        tracks = plan.get("tracks") or metadata["tracks"]
        videos = [c for t in tracks if t.get("kind") == "video" for c in t.get("clips", [])]
        audios = [c for t in tracks if t.get("kind") == "audio" for c in t.get("clips", [])]
        videos.sort(key=lambda c: _number(c.get("timeline_start")))
        used = set()
        for video in videos:
            start = _number(video.get("timeline_start"))
            video_edit = _validated_edit(video["edit"]) if video.get("edit") is not None else {}
            lead = video_edit.get("lead_in_seconds", 0)
            index = next((i for i, a in enumerate(audios) if i not in used and abs(_number(a.get("timeline_start")) - start - lead) < .001), None)
            audio = audios[index] if index is not None else None
            if index is not None:
                used.add(index)
            rows.append({"video": video, "audio": audio, "duration": video.get("duration"),
                         "text": video.get("script_text", ""), "tts_text": video.get("tts_text", ""),
                         "edit": video.get("edit")})
    elif nested:
        return _plan_rows(nested)
    if not rows or any(not row["video"] for row in rows):
        raise RenderError("invalid_plan", "Every shot must have a selected video clip.")
    for row in rows:
        edit = row["edit"]
        if edit is not None:
            edit = row["edit"] = _validated_edit(edit)
            row["video"] = dict(row["video"], **{key: edit[key] for key in (
                "source_in", "source_out", "fit", "crop_x", "crop_y", "shortage")})
            row["text"] = edit["subtitle"].get("text", row["tts_text"] or row["text"])
        else:
            row["text"] = (row["audio"] or {}).get("text") or row["tts_text"] or row["text"]
        row["text"] = str(row["text"])
    return rows, timeline


class _Runner:
    def __init__(self, cancel, progress, timeout):
        self.cancel = cancel
        self.progress = progress
        self.timeout = timeout
        self.last = -1.0

    def check(self):
        if self.cancel:
            try:
                cancelled = self.cancel()
            except Exception:
                raise RenderError("callback_failed", "The cancellation callback failed.") from None
            if cancelled:
                raise RenderCancelled()

    def report(self, value, committed=False):
        value = min(1.0, max(self.last, value))
        if value <= self.last:
            return
        self.last = value
        if self.progress:
            try:
                self.progress(value)
            except Exception:
                if not committed:
                    raise RenderError("callback_failed", "The progress callback failed.") from None

    def run(self, argv, cwd, phase, tick=None, *, capture_stderr=False):
        self.check()
        messages = queue.Queue()
        chunks = []
        size = 0
        proc = subprocess.Popen(
            argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if capture_stderr else subprocess.DEVNULL, shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        def read_stdout():
            for line in iter(proc.stdout.readline, b""):
                messages.put(line)

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()
        started = time.monotonic()
        try:
            while proc.poll() is None or reader.is_alive() or not messages.empty():
                self.check()
                if time.monotonic() - started > self.timeout:
                    raise RenderError("timeout", "A media process exceeded the rendering timeout.")
                try:
                    line = messages.get(timeout=.05)
                except queue.Empty:
                    continue
                size += len(line)
                if size > 2 * 1024 * 1024:
                    raise RenderError("process_failed", "Unexpectedly large media process response.")
                chunks.append(line)
                if tick and line.startswith(b"out_time_us="):
                    try:
                        seconds = max(0.0, int(line.split(b"=", 1)[1]) / 1000000)
                    except ValueError:
                        continue
                    tick(seconds)
            if proc.wait() != 0:
                raise RenderError("process_failed", f"FFmpeg {phase} failed; check media and codec support.")
            self.check()
            return b"".join(chunks)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            reader.join()
            proc.stdout.close()


def _probe(runner, executable, path, cwd):
    raw = runner.run([
        executable, "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries",
        "stream=codec_type,codec_name,duration,width,height,r_frame_rate,avg_frame_rate,nb_frames:format=duration,size,format_name", "-of", "json", str(path),
    ], cwd, "probe")
    try:
        data = json.loads(raw)
        if not data.get("streams"):
            raise ValueError
        return data
    except (ValueError, TypeError):
        raise RenderError("invalid_media", "A media file has no readable streams.") from None


def _bundled_ffmpeg():
    # imageio-ffmpeg's build ships libass; Homebrew's default ffmpeg no longer does.
    try:
        import imageio_ffmpeg

        return str(Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve())
    except Exception:
        return None


def _select_ffmpeg(runner, requested, needs_subtitles, cwd):
    candidates = []
    for preferred in (None, requested):
        try:
            candidate = discover_executable("ffmpeg", preferred)
        except RenderError:
            continue
        if candidate not in candidates:
            candidates.append(candidate)
    if not candidates:
        raise RenderError("missing_tool", "FFmpeg must be installed for real rendering.")
    if not needs_subtitles:
        return candidates[0]
    bundled = _bundled_ffmpeg()
    if bundled and bundled not in candidates:
        candidates.append(bundled)
    for candidate in candidates:
        try:
            filters = runner.run([candidate, "-hide_banner", "-filters"], cwd, "subtitle capability check")
        except RenderError as exc:
            if exc.code != "process_failed":
                raise
            continue
        if re.search(rb"(?m)^\s*\S+\s+ass\s+V->V\s", filters):
            return candidate
    raise RenderError("missing_subtitle_filter", "Chinese subtitles require FFmpeg with the libass ass filter.")


_STILL_FORMATS = {"image2", "jpeg_pipe", "png_pipe", "webp_pipe"}


def _is_still(info):
    return bool(set(info.get("format", {}).get("format_name", "").split(",")) & _STILL_FORMATS)


def _zoompan(motion, duration, fps, width, height):
    frames = max(1, round(duration * fps))
    zoom, step = motion["zoom"], max(frames - 1, 1)
    scale = {"push": f"1+{zoom - 1:.6f}*on/{step}", "pull": f"{zoom:.6f}-{zoom - 1:.6f}*on/{step}", "none": "1"}
    return (f"zoompan=z='{scale[motion['kind']]}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
            f":d={frames}:s={width}x{height}:fps={fps}")


def _stream_duration(data, kind):
    stream = next((s for s in data["streams"] if s.get("codec_type") == kind), None)
    if not stream:
        raise RenderError("invalid_media", f"A required {kind} stream is missing.")
    value = stream.get("duration")
    if value in (None, "N/A"):
        value = data.get("format", {}).get("duration")
    return _number(None if value == "N/A" else value)


def discover_font(explicit: str | None = None, family: str | None = None) -> tuple[Path, str]:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in (".ttf", ".ttc", ".otf", ".otc"):
            raise RenderError("missing_font", "The subtitle font file is unavailable.")
        return path, family or path.stem
    roots = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
        Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Microsoft/Windows/Fonts",
        Path("/System/Library/Fonts"), Path("/Library/Fonts"),
        Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
        Path.home() / ".local/share/fonts", Path.home() / ".fonts", Path.home() / "Library/Fonts",
    ]
    patterns = [
        ("msyh.ttc", "Microsoft YaHei"), ("simhei.ttf", "SimHei"),
        ("NotoSansCJK-Regular.ttc", "Noto Sans CJK SC"),
        ("NotoSansCJKsc-Regular.otf", "Noto Sans CJK SC"),
        ("NotoSansSC-Regular.ttf", "Noto Sans SC"),
        ("SourceHanSansSC-Regular.otf", "Source Han Sans SC"),
        ("PingFang.ttc", "PingFang SC"), ("STHeiti Light.ttc", "Heiti SC"),
        ("wqy-zenhei.ttc", "WenQuanYi Zen Hei"), ("simsun.ttc", "SimSun"),
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for pattern, name in patterns:
            match = next(root.rglob(pattern), None)
            if match:
                return match, family or name
    raise RenderError("missing_font", "Chinese subtitles require a CJK font; install one or pass font_path.")


def _ass_time(seconds):
    centiseconds = max(0, int(round(seconds * 100)))
    return f"{centiseconds // 360000}:{centiseconds // 6000 % 60:02}:{centiseconds // 100 % 60:02}.{centiseconds % 100:02}"


def _subtitle_lines(text, columns):
    # Fullwidth punctuation remains visible but cannot open ASS overrides/escapes.
    text = str(text).replace("\\", "\uff3c").replace("{", "\uff5b").replace("}", "\uff5d")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = "".join(c for c in text if c == "\n" or not unicodedata.category(c).startswith("C"))
    lines = []
    for paragraph in text.split("\n"):
        line, width = "", 0
        for char in paragraph:
            cost = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
            if line and width + cost > columns:
                lines.append(line)
                line, width = "", 0
            line += char
            width += cost
        lines.append(line)
    return lines


def _write_subtitles(path, text, duration, width, height, family, style=None, events=None, lines_per_page=2):
    if any(c in family for c in ",{}\\") or any(ord(c) < 32 for c in family):
        raise RenderError("invalid_options", "The subtitle font family is invalid.")
    size = max(12, round(min(width, height) * .055))
    margin = max(8, round(min(width, height) * .055))
    color, alignment, border, outline = "&H00FFFFFF", 2, 1, max(1, size // 12)
    if style is not None:
        style = _validated_edit({"subtitle": style})["subtitle"]
        size = max(1, round(style["font_size"] * min(width, height) / 720))
        rgb = style["color"][1:]
        color = "&H00" + rgb[4:6] + rgb[2:4] + rgb[:2]
        alignment = {"top": 8, "center": 5, "bottom": 2}[style["position"]]
        border = 3 if style["background"] else 1
        outline = max(3, size // 6) if style["background"] else max(1, size // 12)
    header = (
        f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{family},{size},{color},{color},&H00101010,&H80000000,0,0,0,0,100,100,0,0,{border},"
        f"{outline},0,{alignment},{margin},{margin},{margin},1\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    for event in events if events is not None else [{"start": 0, "end": duration, "text": text}]:
        lines = _subtitle_lines(event["text"], max(8, int((width - margin * 2) / (size * .55))))
        pages = [r"\N".join(lines[i:i + lines_per_page]) for i in range(0, len(lines), lines_per_page)]
        span = event["end"] - event["start"]
        for i, page in enumerate(pages):
            start = event["start"] + span * i / len(pages)
            end = event["start"] + span * (i + 1) / len(pages)
            header += f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{page}\n"
    path.write_text(header, encoding="utf-8")


def render_assembly(plan, output_path, **options):
    """Keep all external exceptions redacted, including OS and callback failures."""
    try:
        return _render(plan, output_path, **options)
    except RenderError:
        raise
    except Exception:
        raise RenderError("render_failed", "Rendering failed; check local media, permissions and render options.") from None


def _render(plan, output_path, *, ffmpeg_path, overwrite, include_voiceover,
            progress_callback, cancel_callback, resolution, aspect_ratio, fps,
            video_bitrate, audio_bitrate, crf, preset, subtitles, font_path,
            font_name, ffprobe_path, base_dir, process_timeout, audio_settings=None):
    runner = _Runner(cancel_callback, progress_callback, _number(process_timeout))
    runner.check()
    runner.report(0.0)
    rows, timeline = _plan_rows(plan)
    try:
        sound = validate_audio_settings(audio_settings)
    except ValueError as exc:
        raise RenderError("invalid_options", str(exc)) from None
    base = Path(base_dir or Path.cwd()).expanduser().resolve()
    output = _local_path(output_path, base)
    if output.suffix.lower() != ".mp4":
        raise RenderError("invalid_output", "The rendered output must be an MP4 file.")
    if output.exists() and not overwrite:
        raise RenderError("output_exists", "The output already exists; choose a new destination or enable overwrite.")
    if aspect_ratio not in (None, "9:16", "16:9"):
        raise RenderError("invalid_options", "aspect_ratio must be 9:16 or 16:9.")
    default_size = (1280, 720) if aspect_ratio == "16:9" else (720, 1280)
    planned_size = timeline.get("resolution") or {}
    width, height = resolution or (default_size if aspect_ratio else (
        planned_size.get("width", 720), planned_size.get("height", 1280)))
    if any(not isinstance(x, int) or isinstance(x, bool) or x < 16 or x > 4096 or x % 2 for x in (width, height)):
        raise RenderError("invalid_options", "Output dimensions must be even integers between 16 and 4096.")
    if aspect_ratio and abs(width / height - (9 / 16 if aspect_ratio == "9:16" else 16 / 9)) > .005:
        raise RenderError("invalid_options", "Resolution does not match the requested aspect_ratio.")
    framing_ratio = next((ratio for ratio, number in (("9:16", 9 / 16), ("16:9", 16 / 9)) if abs(width / height - number) < .005), None)
    for row in rows:
        if row["edit"] is not None:
            row["video"] = dict(row["video"], **resolve_framing(row["edit"], framing_ratio))
    fps = fps if fps is not None else timeline.get("fps", 30)
    if not isinstance(fps, int) or not 1 <= fps <= 60:
        raise RenderError("invalid_options", "fps must be an integer between 1 and 60.")
    if not isinstance(crf, int) or not 0 <= crf <= 51:
        raise RenderError("invalid_options", "crf must be between 0 and 51.")
    if preset not in ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"):
        raise RenderError("invalid_options", "Unsupported H.264 preset.")
    for bitrate in (video_bitrate, audio_bitrate):
        if not re.fullmatch(r"[1-9][0-9]*[kKmM]?", str(bitrate)):
            raise RenderError("invalid_options", "Bitrate must be a positive integer with optional k or M suffix.")
    needs_subtitles = subtitles and any(
        (row["edit"] or {}).get("subtitle", {}).get("cues")
        if (row["edit"] or {}).get("subtitle", {}).get("timing_mode") == "cues"
        else row["text"].strip() for row in rows)
    ffmpeg = _select_ffmpeg(runner, ffmpeg_path, needs_subtitles, base)
    ffprobe = discover_executable("ffprobe", ffprobe_path, Path(ffmpeg))
    music = _local_path(sound["music_path"], base) if sound["music_path"] else None
    if music:
        if music == output or (output.exists() and music.exists() and os.path.samefile(music, output)):
            raise RenderError("input_output_conflict", "Output must not replace the music file.")
        if not music.is_file():
            raise RenderError("missing_input", "Background music is missing; import it again.")
    for row in rows:
        for kind in ("video", "audio"):
            clip = row[kind]
            if kind == "audio" and not include_voiceover:
                row[kind] = None
                continue
            if clip:
                raw = clip.get("source_path") or clip.get("path") or clip.get("asset_path")
                path = _local_path(raw, base)
                if path == output or (output.exists() and path.exists() and os.path.samefile(path, output)):
                    raise RenderError("input_output_conflict", "Output must not replace any source media file.")
                if not path.is_file():
                    raise RenderError("missing_input", "A selected video or narration file is missing.")
                row[kind] = dict(clip, path=path)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Same-filesystem staging allows atomic commit; only this private directory is removed.
    with tempfile.TemporaryDirectory(prefix=".mimo-render-", dir=output.parent) as temp:
        work = Path(temp)
        cache = {}
        warnings = []
        music_duration = None
        if music:
            music_info = _probe(runner, ffprobe, music, work)
            if not set(music_info.get("format", {}).get("format_name", "").split(",")).intersection({"wav", "mp3", "mov", "aac", "flac", "ogg"}):
                raise RenderError("invalid_media", "Background music must be a supported standalone audio file.")
            music_duration = _stream_duration(music_info, "audio")
            if music_duration <= 0:
                raise RenderError("invalid_media", "Background music has no measurable duration.")
        total = 0.0
        for i, row in enumerate(rows):
            for kind in ("video", "audio"):
                clip = row[kind]
                if not clip:
                    continue
                path = clip["path"]
                if path not in cache:
                    cache[path] = _probe(runner, ffprobe, path, work)
                if kind == "video" and _is_still(cache[path]):
                    # A photo has no timeline of its own; its push/pull fills the whole shot.
                    clip.update(still=True, start=0.0, available=math.inf)
                    continue
                measured = _stream_duration(cache[path], kind)
                start = _number(clip.get("source_in"))
                if measured <= 0:
                    raise RenderError("invalid_media", "A selected media stream has no measurable duration.")
                if start >= measured:
                    raise RenderError("invalid_plan", "A source trim starts beyond the end of its media.")
                clip["start"] = start
                clip["available"] = max(0.0, measured - start)
                if kind == "video" and clip.get("source_out") is not None:
                    end = _number(clip["source_out"])
                    if end <= start:
                        raise RenderError("invalid_plan", "A video trim must end after it starts.")
                    # Legacy planners stored an estimated source_out, not an explicit selection.
                    if row["edit"] is not None and end > measured + 1e-6:
                        raise RenderError("invalid_plan", "The selected video range exceeds its measured duration.")
                    clip["available"] = min(clip["available"], end - start)
            audio = row["audio"]
            manual = (row["edit"] or {}).get("duration_mode") == "manual"
            duration = audio["available"] if audio and not manual else _number(
                row["duration"], _number(row["video"].get("duration")))
            if duration <= 0 or duration > 3600:
                raise RenderError("invalid_plan", "Each shot must have a duration between zero and one hour.")
            if manual and audio and duration + 1e-6 < audio["available"]:
                raise RenderError("invalid_plan", "Manual shot duration cannot be shorter than measured narration.")
            try:
                duration = shot_duration(row["edit"] or {}, duration, audio["available"] if audio else None)
                row["subtitle_events"] = subtitle_events(row["text"], row["edit"] or {}, duration, audio["available"] if audio else None)
            except ValueError as exc:
                raise RenderError("invalid_plan", str(exc)) from None
            subtitle = (row["edit"] or {}).get("subtitle", {})
            if subtitles and subtitle.get("timing_mode") == "cues" and subtitle.get("timing_source") == "estimated":
                warnings.append(f"镜头 {i + 1}：字幕时间为估算，尚未确认校准。")
            if row["video"]["available"] + 1e-6 < duration:
                if row["video"].get("shortage", "freeze") == "error":
                    raise RenderError("invalid_plan", f"Shot {i + 1}: selected video is shorter than the target duration.")
                warnings.append(f"Shot {i + 1}: selected video is shorter than the target duration; its last frame was frozen.")
            row["duration"] = math.ceil(duration * fps - 1e-7) / fps
            total += row["duration"]
            runner.report(.1 * (i + 1) / len(rows))
        if needs_subtitles:
            font, family = discover_font(font_path, font_name)
            (work / "fonts").mkdir()
            shutil.copyfile(font, work / "fonts" / ("subtitle" + font.suffix))
        else:
            family = ""
        fade_in, fade_out, transition_warnings = transition_windows(rows)
        warnings.extend(transition_warnings)
        completed = 0.0
        parts = []
        voice_cache, audio_report = {}, []
        for i, row in enumerate(rows):
            runner.check()
            video, audio, duration = row["video"], row["audio"], row["duration"]
            argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-threads", "2", "-filter_threads", "2", "-protocol_whitelist", "file,pipe",
                    # Seeking into a photo can drop its only frame.
                    *([] if video.get("still") else ["-ss", str(video["start"])]), "-i", str(video["path"])]
            if audio:
                argv += ["-protocol_whitelist", "file,pipe", "-ss", str(audio["start"]), "-i", str(audio["path"])]
            else:
                argv += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            visual_duration = video["available"]
            filters = []
            if visual_duration:
                filters.append(f"trim=duration={min(visual_duration, duration):.9f}")
            filters.append("setpts=PTS-STARTPTS")
            # Photos are framed at 4x and zoomed down to size, so the push/pull moves without jitter.
            box_w, box_h = (width * 4, height * 4) if video.get("still") else (width, height)
            if video.get("fit", "cover") in ("contain", "fit"):
                filters += [f"scale={box_w}:{box_h}:force_original_aspect_ratio=decrease:force_divisible_by=2",
                            f"pad={box_w}:{box_h}:(ow-iw)/2:(oh-ih)/2:color=black"]
            else:
                crop_x = _number(video.get("crop_x"), .5)
                crop_y = _number(video.get("crop_y"), .5)
                if crop_x > 1 or crop_y > 1:
                    raise RenderError("invalid_plan", "Crop coordinates must be between zero and one.")
                filters += [f"scale={box_w}:{box_h}:force_original_aspect_ratio=increase:force_divisible_by=2",
                            f"crop={box_w}:{box_h}:(iw-ow)*{crop_x:.9f}:(ih-oh)*{crop_y:.9f}"]
            if video.get("still"):
                filters.append(_zoompan(image_motion(row["edit"]), duration, fps, width, height))
            filters += ["setsar=1", f"fps={fps}", f"tpad=stop_mode=clone:stop_duration={duration:.9f}",
                        f"trim=duration={duration:.9f}", "setpts=PTS-STARTPTS"]
            if subtitles and row["subtitle_events"]:
                subtitle_name = f"subtitle_{i:05d}.ass"
                style = row["edit"]["subtitle"] if row["edit"] is not None else None
                _write_subtitles(work / subtitle_name, row["text"], duration, width, height, family, style, row["subtitle_events"])
                # ASCII staging names avoid FFmpeg filter escaping for Windows/CJK paths.
                filters.append(f"ass=filename={subtitle_name}:fontsdir=fonts")
            if fade_in[i]:
                filters.append(f"fade=t=in:st=0:d={fade_in[i]:.9f}:color=black")
            if fade_out[i]:
                filters.append(f"fade=t=out:st={duration - fade_out[i]:.9f}:d={fade_out[i]:.9f}:color=black")
            part = f"part_{i:05d}.mkv"
            parts.append(part)
            audio_filters = ["asetpts=PTS-STARTPTS", "aresample=48000"]
            if audio:
                key = (audio["path"], audio["start"])
                if key not in voice_cache:
                    voice_cache[key] = voice_processing(runner, ffmpeg, audio, sound, work)
                processing, metrics = voice_cache[key]
                audio_filters.extend(processing)
                audio_report.append({"shot_index": i + 1, **metrics})
                if metrics["silent_or_too_quiet"]:
                    warnings.append(f"Shot {i + 1}: narration is silent or too quiet; loudness normalization was skipped.")
            lead = (row["edit"] or {}).get("lead_in_seconds", 0)
            audio_filters.append("aresample=48000")
            if audio and lead:
                audio_filters += [f"adelay={round(lead * 48000)}S:all=1"]
            audio_filters += ["aresample=48000", "apad", f"atrim=duration={duration:.9f}"]
            argv += ["-map", "0:v:0", "-map", "1:a:0", "-vf", ",".join(filters),
                     "-af", ",".join(audio_filters),
                     "-t", f"{duration:.9f}", "-c:v", "libx264", "-threads", "2", "-preset", preset,
                     "-crf", str(crf), "-maxrate", str(video_bitrate), "-bufsize", str(video_bitrate),
                     "-pix_fmt", "yuv420p", "-c:a", "pcm_f32le", "-ar", "48000", "-ac", "2",
                     "-map_metadata", "-1", "-map_chapters", "-1", "-progress", "pipe:1", "-nostats", part]
            runner.run(argv, work, "shot encoding", lambda seconds: runner.report(
                .1 + .8 * (completed + min(seconds, duration)) / total))
            completed += duration
            runner.report(.1 + .8 * completed / total)
        (work / "concat.txt").write_text("".join(f"file '{part}'\n" for part in parts), encoding="ascii")
        staging = work / "result.mp4"
        assembly = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat", "-safe", "1",
            "-i", "concat.txt"]
        if music:
            if sound["loop_music"]:
                assembly += ["-stream_loop", "-1"]
            assembly += ["-protocol_whitelist", "file,pipe", "-i", str(music)]
        assembly += ["-filter_complex_threads", "2", "-filter_complex", assembly_audio_filters(sound, total, music_duration),
            "-map", "0:v:0", "-map", "[mixed]", "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-b:a", str(audio_bitrate), "-t", f"{total:.9f}",
            "-movflags", "+faststart", "-map_metadata", "-1", "-map_chapters", "-1",
            "-progress", "pipe:1", "-nostats", staging.name]
        runner.run(assembly, work, "assembly", lambda seconds: runner.report(.9 + .08 * min(seconds / total, 1.0)))
        info = _probe(runner, ffprobe, staging, work)
        actual = _stream_duration(info, "video")
        _stream_duration(info, "audio")
        if abs(actual - total) > max(.15, len(rows) * .003):
            raise RenderError("invalid_output", "Rendered duration does not match the complete shot sequence.")
        result = {"output_path": str(output), "duration_seconds": actual,
                  "planned_duration_seconds": total, "width": width, "height": height, "fps": fps,
                  "segment_count": len(rows), "size_bytes": staging.stat().st_size, "warnings": warnings,
                  "audio_settings": sound, "audio_processing": audio_report,
                  "visual_processing": [{"shot_index": i + 1, "framing_ratio": framing_ratio,
                                         "framing": {key: row["video"].get(key, default) for key, default in (("fit", "cover"), ("crop_x", .5), ("crop_y", .5))},
                                         "fade_in_seconds": fade_in[i], "fade_out_seconds": fade_out[i]}
                                        for i, row in enumerate(rows)],
                  "shot_timing": [{"shot_index": i + 1, "duration_seconds": row["duration"],
                                   "lead_in_seconds": (row["edit"] or {}).get("lead_in_seconds", 0),
                                   "tail_out_seconds": (row["edit"] or {}).get("tail_out_seconds", 0),
                                   "subtitle_events": row["subtitle_events"] if subtitles else []}
                                  for i, row in enumerate(rows)]}
        runner.report(.99)
        runner.check()
        if overwrite:
            os.replace(staging, output)
        else:
            try:
                os.link(staging, output)
            except FileExistsError:
                raise RenderError("output_exists", "The output was created by another job; it was not replaced.") from None
        runner.report(1.0, committed=True)
        return result
