from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ai_client import OpenAICompatibleClient
from production_schema import ReferenceCopy, StoryboardAnalysis, StoryboardShot


FENCED_BLOCK_RE = re.compile(r"```(?:\s*[a-zA-Z0-9_-]+)?\s*(.*?)```", re.DOTALL)


SYSTEM_PROMPT = """你是短视频分镜策划助手。
你需要把参考文案拆成可执行的分镜 JSON，方便后续素材匹配、TTS 配音和自动剪辑。
只输出合法 JSON，不要解释，不要 Markdown。"""


def parse_storyboard_json(raw: str) -> Any:
    """Parse model JSON, including responses wrapped in fenced code blocks."""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("模型返回为空，无法解析分镜 JSON")

    candidates = _json_candidates(text)
    last_error = None
    for candidate in candidates:
        try:
            return _decode_first_json(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc

    detail = f": {last_error}" if last_error else ""
    raise ValueError(f"无法解析模型返回的 JSON{detail}")


def _json_candidates(text: str) -> List[str]:
    candidates = []
    for match in FENCED_BLOCK_RE.findall(text):
        block = match.strip()
        if block:
            candidates.append(block)
    candidates.append(text)

    unique = []
    seen = set()
    for candidate in candidates:
        if candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return unique


def _decode_first_json(text: str) -> Any:
    decoder = json.JSONDecoder()
    stripped = text.strip()

    try:
        value, _ = decoder.raw_decode(stripped)
        return value
    except json.JSONDecodeError:
        pass

    for index, char in enumerate(stripped):
        if char not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[index:])
            return value
        except json.JSONDecodeError:
            continue

    raise json.JSONDecodeError("No JSON object or array found", stripped, 0)


class StoryboardAnalyzer:
    def __init__(
        self,
        client: Optional[OpenAICompatibleClient] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 3000,
        timeout: int = 90,
    ):
        self.client = client or OpenAICompatibleClient()
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def analyze(
        self,
        reference_copy: Any,
        target_duration_seconds: Optional[float] = None,
        shot_count: Optional[int] = None,
        model: Optional[str] = None,
    ) -> StoryboardAnalysis:
        reference = self._coerce_reference_copy(reference_copy)
        if not reference.source_text.strip():
            raise ValueError("参考文案不能为空")

        used_model = model or self.model or getattr(self.client, "model", "")
        messages = self.build_messages(reference, target_duration_seconds=target_duration_seconds, shot_count=shot_count)
        raw = self.client.chat(
            messages,
            model=used_model or None,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
        )
        payload = parse_storyboard_json(raw)
        return self.analysis_from_payload(
            payload,
            reference_copy=reference,
            raw_response=raw,
            model=used_model,
            target_duration_seconds=target_duration_seconds,
        )

    def analyze_text(self, reference_text: str, title: str = "", **kwargs: Any) -> StoryboardAnalysis:
        reference = ReferenceCopy(title=title, source_text=reference_text)
        return self.analyze(reference, **kwargs)

    def build_messages(
        self,
        reference: ReferenceCopy,
        target_duration_seconds: Optional[float] = None,
        shot_count: Optional[int] = None,
    ) -> List[Dict[str, str]]:
        target_text = f"{target_duration_seconds:g} 秒" if target_duration_seconds else "由文案节奏自行判断"
        shot_count_text = f"{shot_count} 个分镜" if shot_count else "按内容自然拆分"
        keywords = "、".join(reference.keywords) if reference.keywords else "无"

        user_prompt = f"""请分析下面的参考文案，并拆成短视频分镜。

标题: {reference.title or "无"}
产品/主题: {reference.product_name or "无"}
关键词: {keywords}
目标总时长: {target_text}
期望分镜数量: {shot_count_text}

参考文案:
{reference.source_text}

输出 JSON 结构必须包含:
{{
  "summary": "整体创意和叙事节奏摘要",
  "target_duration_seconds": 30,
  "style_notes": ["剪辑风格、镜头节奏或声音建议"],
  "shots": [
    {{
      "shot_id": "S01",
      "title": "分镜标题",
      "script_text": "该分镜对应的画面文案或字幕文案",
      "tts_text": "适合 TTS 朗读的配音文本",
      "duration_seconds": 3.5,
      "visual_tags": ["画面标签", "情绪标签", "素材类型标签"],
      "material_requirements": ["需要匹配的素材描述"],
      "transition": "转场建议",
      "notes": "剪辑、节奏或风险备注"
    }}
  ]
}}

要求:
- shot_id 从 S01 开始递增。
- duration_seconds 使用数字，不要写单位。
- visual_tags 和 material_requirements 必须是字符串数组。
- tts_text 要口语、顺畅，适合直接转音频。
- 不要输出 JSON 以外的文字。"""

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def analysis_from_payload(
        self,
        payload: Any,
        reference_copy: Optional[ReferenceCopy] = None,
        raw_response: str = "",
        model: str = "",
        target_duration_seconds: Optional[float] = None,
    ) -> StoryboardAnalysis:
        if isinstance(payload, list):
            root = {}
            shots_payload = payload
        elif isinstance(payload, dict):
            root = payload
            shots_payload = self._extract_shots_payload(payload)
        else:
            raise ValueError("分镜 JSON 顶层必须是对象或数组")

        shots = [self._shot_from_payload(item, index + 1) for index, item in enumerate(shots_payload)]
        payload_duration = _to_float(_pick(root, ["target_duration_seconds", "total_duration_seconds", "duration_seconds"]))
        total_duration = target_duration_seconds or payload_duration or _sum_duration(shots)
        metadata = _metadata_without(root, {"analysis_id", "summary", "target_duration_seconds", "total_duration_seconds", "duration_seconds", "style_notes", "shots", "storyboard", "storyboard_shots", "items", "segments"})

        return StoryboardAnalysis(
            analysis_id=_as_text(root.get("analysis_id", "")),
            reference_copy=reference_copy,
            summary=_as_text(root.get("summary", "")),
            target_duration_seconds=total_duration,
            shots=shots,
            style_notes=_as_str_list(root.get("style_notes", [])),
            model=model,
            raw_response=raw_response,
            metadata=metadata,
        )

    def _coerce_reference_copy(self, reference_copy: Any) -> ReferenceCopy:
        if isinstance(reference_copy, ReferenceCopy):
            return reference_copy
        if isinstance(reference_copy, dict):
            return ReferenceCopy.from_dict(reference_copy)
        return ReferenceCopy(source_text=str(reference_copy or ""))

    def _extract_shots_payload(self, payload: Dict[str, Any]) -> List[Any]:
        shots = (
            payload.get("shots")
            or payload.get("storyboard_shots")
            or payload.get("items")
            or payload.get("segments")
            or payload.get("storyboard")
            or []
        )
        if isinstance(shots, dict):
            shots = shots.get("shots") or shots.get("items") or shots.get("segments") or []
        if isinstance(shots, list):
            return shots
        if shots:
            return [shots]
        return []

    def _shot_from_payload(self, payload: Any, index: int) -> StoryboardShot:
        if not isinstance(payload, dict):
            text = _as_text(payload)
            return StoryboardShot(
                shot_id=f"S{index:02d}",
                title=f"分镜 {index}",
                script_text=text,
                tts_text=text,
                duration_seconds=_estimate_duration(text),
            )

        script_text = _as_text(_pick(payload, ["script_text", "script", "copy", "caption", "content", "text"]))
        tts_text = _as_text(_pick(payload, ["tts_text", "voiceover", "voiceover_text", "narration", "spoken_text"])) or script_text
        duration = _to_float(
            _pick(payload, ["duration_seconds", "duration", "seconds"]),
            default=_estimate_duration(tts_text or script_text),
        )
        known_keys = {
            "shot_id", "id", "shot", "title", "name", "script_text", "script", "copy", "caption", "content",
            "text", "tts_text", "voiceover", "voiceover_text", "narration", "spoken_text", "duration_seconds",
            "duration", "seconds", "visual_tags", "tags", "keywords", "material_requirements", "materials",
            "material", "asset_requirements", "transition", "transition_suggestion", "notes", "note", "remark",
        }

        return StoryboardShot(
            shot_id=_as_text(_pick(payload, ["shot_id", "id", "shot"])) or f"S{index:02d}",
            title=_as_text(_pick(payload, ["title", "name"])) or f"分镜 {index}",
            script_text=script_text,
            tts_text=tts_text,
            duration_seconds=duration,
            visual_tags=_as_str_list(_pick(payload, ["visual_tags", "tags", "keywords"], [])),
            material_requirements=_as_str_list(_pick(payload, ["material_requirements", "materials", "material", "asset_requirements"], [])),
            transition=_as_text(_pick(payload, ["transition", "transition_suggestion"])),
            notes=_as_text(_pick(payload, ["notes", "note", "remark"])),
            metadata=_metadata_without(payload, known_keys),
        )


def _pick(data: Dict[str, Any], keys: List[str], default: Any = "") -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _as_str_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,，、\n]+", value) if item.strip()]
    if isinstance(value, list):
        result = []
        for item in value:
            text = _requirement_text(item)
            if text:
                result.append(text)
        return result
    return [_requirement_text(value)]


def _requirement_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            text = _as_text(item)
            if text:
                parts.append(f"{key}: {text}")
        return "；".join(parts)
    return _as_text(value)


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if match:
        return float(match.group(0))
    return default


def _estimate_duration(text: str) -> float:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return 3.0
    return round(max(2.0, min(12.0, len(compact) / 4.2)), 1)


def _sum_duration(shots: List[StoryboardShot]) -> float:
    return round(sum(shot.duration_seconds or 0 for shot in shots), 1)


def _metadata_without(data: Dict[str, Any], known_keys: set) -> Dict[str, Any]:
    return {key: value for key, value in data.items() if key not in known_keys}
