"""Measured subtitle timing for voiceovers.

The known voiceover text is force-aligned to the audio in a separate Python environment
(WhisperX and PyTorch, see tools/align_worker.py and docs/WhisperX中文对齐实测.md), so the
main application stays light. Numbers are aligned in the way they are read and mapped back
to the text. Clause boundaries next to English words are snapped to a detected silence.
Without an aligner, or when it fails, cues are estimated from the character count.
Silences inside a voiceover are reported as pauses, with the words on either side.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Iterable, List, Mapping, Optional, Sequence, Tuple

WORKER = Path(__file__).resolve().parent / "tools" / "align_worker.py"
CLAUSE = re.compile(r"[^，、；：。！？,;:!?\n]+[，、；：。！？,;:!?\n]*")
NUMBER = re.compile(r"(\d+)(?=\s*年)|\d+\.\d+|\d+%|\d+|.", re.S)
PUNCTUATION = set("，、；：。！？,;:!?.\n \t")
TRAILING = "，、；：。,;:. \n\t"
LATIN = re.compile(r"[A-Za-z]")
DIGITS = "零一二三四五六七八九"
SNAP_WINDOW = 0.4
PAUSE_LIMIT_SECONDS = 0.5  # pauses inside a voiceover longer than this are flagged by default
PAUSE_TOLERANCE = 0.01  # silence detection jitter: a pause this close to the limit is within it
MIN_CUE = 0.05


class AlignmentError(RuntimeError):
    """Why measured timing is not available; shown to the user next to the estimated cues."""


def _read_int(n: int) -> str:
    """Standard Mandarin reading of an integer (1999 -> 一千九百九十九)."""
    if n >= 100000:
        return "".join(DIGITS[int(d)] for d in str(n))
    if n == 0:
        return "零"
    out, zero = "", False
    for value, unit in ((10000, "万"), (1000, "千"), (100, "百"), (10, "十")):
        q, n = divmod(n, value)
        if q:
            out += ("零" if zero else "") + ("" if unit == "十" and q == 1 and not out else DIGITS[q]) + unit
            zero = False
        elif out:
            zero = True
    return out + (("零" if zero else "") + DIGITS[n] if n else "")


def spoken_tokens(text: str) -> List[Tuple[str, str]]:
    """(original, spoken) pairs; numbers become how a Mandarin voice reads them, years digit by digit."""
    tokens = []
    for match in NUMBER.finditer(text):
        token = match.group(0)
        if match.group(1):
            spoken = "".join(DIGITS[int(d)] for d in token)
        elif token.endswith("%") and token[:-1].isdigit():
            spoken = "百分之" + _read_int(int(token[:-1]))
        elif "." in token and token.replace(".", "").isdigit():
            whole, fraction = token.split(".")
            spoken = _read_int(int(whole)) + "点" + "".join(DIGITS[int(d)] for d in fraction)
        elif token.isdigit():
            spoken = _read_int(int(token))
        else:
            spoken = token
        tokens.append((token, spoken))
    return tokens


def spoken_text(text: str) -> str:
    return "".join(spoken for _, spoken in spoken_tokens(text))


def clauses(text: str) -> List[Tuple[str, int, int]]:
    """Subtitle clauses with their character offsets; punctuation stays with the clause before it."""
    return [(match.group(0), match.start(), match.end()) for match in CLAUSE.finditer(text) if match.group(0).strip(TRAILING)]


def _cue_text(clause: str) -> str:
    return clause.strip().rstrip(TRAILING) or clause.strip()


def _weight(clause: str) -> int:
    return max(1, sum(1 for char in clause if char not in PUNCTUATION))


def estimate_cues(text: str, duration: float) -> List[dict]:
    """Cues spread over the voice by character count, relative to the voice start."""
    parts = clauses(text)
    if not parts or duration <= 0:
        return []
    total, cursor, cues = sum(_weight(part) for part, _, _ in parts), 0, []
    for index, (part, _, _) in enumerate(parts):
        start = round(duration * cursor / total, 2)
        cursor += _weight(part)
        end = round(duration, 2) if index == len(parts) - 1 else round(duration * cursor / total, 2)
        cues.append({"start": start, "end": end, "text": _cue_text(part)})
    return cues


def detect_silences(audio_path, ffmpeg: str, noise_db: float = -40, min_seconds: float = 0.12,
                    runner: Callable = subprocess.run) -> List[Tuple[float, float]]:
    """Silent stretches (start, end) in seconds, found with FFmpeg's silencedetect."""
    done = runner([ffmpeg, "-hide_banner", "-nostats", "-i", str(audio_path), "-af",
                   f"silencedetect=noise={noise_db}dB:d={min_seconds}", "-f", "null", "-"],
                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    starts = [max(0.0, float(value)) for value in re.findall(r"silence_start: (-?[\d.]+)", done.stderr)]
    ends = [float(value) for value in re.findall(r"silence_end: ([\d.]+)", done.stderr)]
    return [(start, ends[index] if index < len(ends) else float("inf")) for index, start in enumerate(starts)]


def _latin_edge(clause: str, last: bool) -> bool:
    chars = [char for char in clause if char not in PUNCTUATION]
    return bool(chars) and bool(LATIN.match(chars[-1] if last else chars[0]))


def _snap(silences: Iterable[Tuple[float, float]], time: float, edge: int) -> Optional[float]:
    """The silence edge (0 = start, 1 = end) nearest to time, if one is within SNAP_WINDOW."""
    candidates = [silence[edge] for silence in silences if abs(silence[edge] - time) <= SNAP_WINDOW]
    return min(candidates, key=lambda value: abs(value - time)) if candidates else None


def _timed(char) -> bool:
    return all(isinstance(value, (int, float)) and math.isfinite(value) for value in char[1:3])


def _units(text: str, chars: Sequence[Sequence]):
    """(original token, start, end) per token of text, from the aligner's characters of its spoken form."""
    tokens = spoken_tokens(text)
    if len(chars) != sum(len(spoken) for _, spoken in tokens):
        raise AlignmentError("对齐结果和配音文案对不上")
    units, offsets, cursor, position = [], [], 0, 0
    for original, spoken in tokens:
        timed = [char for char in chars[cursor:cursor + len(spoken)] if _timed(char)]
        cursor += len(spoken)
        units.append((original, float(timed[0][1]) if timed else None, float(timed[-1][2]) if timed else None))
        offsets.append(position)
        position += len(original)
    return units, offsets


def internal_pauses(silences: Iterable[Tuple[float, float]], duration: float, edge: float = 0.02) -> List[Tuple[float, float]]:
    """Silences inside the speech: those touching the start or the end of the voice are not pauses."""
    return [(start, end) for start, end in silences if start > edge and end < duration - edge]


def _pause(text: str, units, offsets, span: Tuple[float, float]) -> dict:
    """A pause with the words on either side of it, when the characters are timed."""
    start, end = span
    before = after = ""
    if units:
        middle = (start + end) / 2
        index = next((offset for unit, offset in zip(units, offsets)
                      if unit[1] is not None and unit[1] >= middle and unit[0].strip() and unit[0] not in PUNCTUATION), len(text))
        before, after = text[:index].rstrip(TRAILING)[-6:], text[index:].strip(TRAILING)[:6]
    return {"start": round(start, 3), "end": round(end, 3), "seconds": round(end - start, 3), "before": before, "after": after}


def cues_from_chars(text: str, chars: Sequence[Sequence], duration: float,
                    silences: Sequence[Tuple[float, float]] = ()) -> Tuple[List[dict], List[str]]:
    """Continuous clause cues from per-character times of the spoken text, relative to the voice start."""
    units, offsets = _units(text, chars)
    parts, spans = clauses(text), []
    for _, a, b in parts:
        timed = [unit for unit, offset in zip(units, offsets)
                 if a <= offset < b and unit[1] is not None and unit[0].strip() and unit[0] not in PUNCTUATION]
        if not timed:
            raise AlignmentError("有分句没有对上时间")
        spans.append([timed[0][1], timed[-1][2]])
    starts, warnings = [span[0] for span in spans], []
    edges = [(0, _latin_edge(parts[0][0], last=False))] + [
        (k, _latin_edge(parts[k - 1][0], last=True) or _latin_edge(parts[k][0], last=False)) for k in range(1, len(parts))]
    for k, latin in edges:
        if not latin:
            continue
        snapped = _snap(silences, starts[k], 1)
        if snapped is not None and (k == 0 or snapped > starts[k - 1] + MIN_CUE):
            starts[k] = snapped
        elif k:
            warnings.append(f"第 {k + 1} 句与上一句之间有英文，时间请复核。")
    end = spans[-1][1]
    if _latin_edge(parts[-1][0], last=True):
        snapped = _snap(silences, end, 0)
        if snapped is not None and snapped > starts[-1] + MIN_CUE:
            end = snapped
        else:
            warnings.append("最后一句结尾是英文，结束时间请复核。")
    bounds = [max(0.0, value) for value in starts] + [min(end, duration)]
    cues = [{"start": round(bounds[k], 2), "end": round(bounds[k + 1], 2), "text": _cue_text(part)}
            for k, (part, _, _) in enumerate(parts)]
    if any(cue["end"] - cue["start"] < MIN_CUE for cue in cues):
        raise AlignmentError("对齐出的分句时间过短或顺序异常")
    return cues, warnings


def _run_worker(items: Sequence[Mapping], settings: Mapping, ffmpeg: Optional[str], runner: Callable) -> list:
    python, model = Path(str(settings["python"])).expanduser(), Path(str(settings["model"])).expanduser()
    if not python.is_file():
        raise AlignmentError(f"找不到对齐环境：{python}")
    if not model.is_dir():
        raise AlignmentError(f"找不到对齐模型：{model}（外接盘是否已连接？）")
    request = {"model": str(model), "items": [
        {"audio": str(item["audio"]), "text": spoken_text(item["text"]), "duration": float(item["duration"])} for item in items]}
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"}
    if settings.get("nltk_data"):
        env["NLTK_DATA"] = str(Path(str(settings["nltk_data"])).expanduser())
    if ffmpeg:  # the worker decodes audio with the ffmpeg command
        env["PATH"] = os.pathsep.join([str(Path(ffmpeg).parent), env.get("PATH", "")])
    try:
        done = runner([str(python), str(WORKER)], input=json.dumps(request, ensure_ascii=False), capture_output=True,
                      text=True, encoding="utf-8", errors="replace", timeout=120 + 10 * len(items), env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AlignmentError(f"对齐进程没有完成：{exc}") from None
    if done.returncode != 0:
        lines = [line for line in (done.stderr or "").splitlines() if line.strip()]
        raise AlignmentError("对齐进程出错：" + (lines[-1] if lines else f"退出码 {done.returncode}"))
    try:
        answers = json.loads(done.stdout)["items"]
    except (ValueError, KeyError, TypeError):
        raise AlignmentError("对齐进程返回的内容无法读取") from None
    if not isinstance(answers, list) or len(answers) != len(items):
        raise AlignmentError("对齐进程返回的条数不对")
    return answers


def align_voiceovers(items: Sequence[Mapping], settings: Optional[Mapping], *, ffmpeg: Optional[str] = None,
                     runner: Callable = subprocess.run) -> List[dict]:
    """Cues for each {"audio", "text", "duration"}: measured when the aligner works, estimated otherwise."""
    answers, reason = [None] * len(items), "未配置对齐环境"
    if settings and items:
        try:
            answers = _run_worker(items, settings, ffmpeg, runner)
        except AlignmentError as exc:
            reason = str(exc)
    results = []
    for item, answer in zip(items, answers):
        problem, duration = reason, float(item["duration"])
        try:
            silences = detect_silences(item["audio"], ffmpeg, runner=runner) if ffmpeg else []
        except (OSError, subprocess.SubprocessError):
            silences = []
        pauses = internal_pauses(silences, duration)
        if isinstance(answer, Mapping) and isinstance(answer.get("chars"), list):
            try:
                cues, warnings = cues_from_chars(item["text"], answer["chars"], duration, silences)
                units, offsets = _units(item["text"], answer["chars"])
                results.append({"source": "aligned", "cues": cues, "warnings": warnings,
                                "pauses": [_pause(item["text"], units, offsets, span) for span in pauses]})
                continue
            except AlignmentError as exc:
                problem = str(exc)
        elif isinstance(answer, Mapping) and answer.get("error"):
            problem = f"对齐失败：{answer['error']}"
        results.append({"source": "estimated", "cues": estimate_cues(item["text"], duration),
                        "warnings": [f"字幕时间为估算（{problem}）。"],
                        "pauses": [_pause(item["text"], None, None, span) for span in pauses]})
    return results
