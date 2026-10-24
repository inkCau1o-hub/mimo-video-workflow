"""Validated shot edits and bounded, portable history stored with each activity."""

from copy import deepcopy
from datetime import datetime
import math
import re
import uuid

from production_schema import VoiceoverSegment
from shot_timing import normalize_timing, shot_duration, validate_cue_bounds
from visual_edit import validate_visual_edit


HISTORY_LIMIT = 30


def _number(value, name, minimum, maximum):
    if isinstance(value, bool):
        raise ValueError(f"{name}必须是数字。")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}必须是数字。") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name}必须在 {minimum} 到 {maximum} 之间。")
    return result


def narration(shot):
    return shot.get("tts_text") or shot.get("script_text") or ""


def validate_shots(shots, activity):
    if not isinstance(shots, list) or not 1 <= len(shots) <= 500:
        raise ValueError("分镜需要 1 到 500 个镜头。")
    assets = {asset.asset_id: asset for asset in activity.material_assets}
    voices = {voice.shot_id: voice for voice in activity.voiceover_segments}
    result, seen = deepcopy(shots), set()
    for index, shot in enumerate(result, 1):
        if not isinstance(shot, dict):
            raise ValueError("分镜格式无效。")
        identifier = shot.get("shot_id") or f"S{index:02d}"
        if not isinstance(identifier, str) or not re.fullmatch(r"[\w-]{1,80}", identifier) or identifier in seen:
            raise ValueError("镜头编号必须唯一，且不能包含路径符号。")
        shot["shot_id"] = identifier
        seen.add(identifier)
        for field in ("script_text", "tts_text"):
            if field in shot and (not isinstance(shot[field], str) or len(shot[field]) > 10000):
                raise ValueError("镜头正文必须是文本且不超过一万字。")
        if not narration(shot).strip():
            raise ValueError("每个镜头需要配音文本或脚本文本。")
        shot["duration_seconds"] = _number(shot.get("duration_seconds"), "镜头时长", 0.1, 3600)
        metadata = shot.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("镜头设置格式无效。")
        asset_id = metadata.get("material_asset_id")
        if asset_id is not None and not isinstance(asset_id, str):
            raise ValueError("素材编号格式无效。")
        if asset_id and asset_id not in assets:
            raise ValueError("所选素材已不存在，请重新选择。")
        if metadata.get("duplicate_of") is not None and not isinstance(metadata["duplicate_of"], str):
            raise ValueError("复制来源编号格式无效。")
        if "edit" not in metadata:
            continue
        edit = metadata["edit"]
        if not isinstance(edit, dict):
            raise ValueError("镜头编辑设置格式无效。")
        edit["source_in"] = _number(edit.get("source_in", 0), "片段开始", 0, 86400)
        if edit.get("source_out") is not None:
            edit["source_out"] = _number(edit["source_out"], "片段结束", 0, 86400)
            if edit["source_out"] <= edit["source_in"]:
                raise ValueError("片段结束必须晚于开始。")
        asset = assets.get(asset_id)
        if asset and asset.duration_seconds and asset.duration_seconds > 0:
            if edit["source_in"] >= asset.duration_seconds or (
                edit.get("source_out") is not None and edit["source_out"] > asset.duration_seconds + 0.001
            ):
                raise ValueError("所选片段超出素材时长。")
        for field, default, choices in (
            ("fit", "cover", {"cover", "contain"}),
            ("shortage", "freeze", {"freeze", "error"}),
            ("duration_mode", "narration", {"narration", "manual"}),
        ):
            value = edit.get(field, default)
            if not isinstance(value, str) or value not in choices:
                raise ValueError(f"无效的镜头选项：{field}")
            edit[field] = value
        for field in ("crop_x", "crop_y"):
            edit[field] = _number(edit.get(field, 0.5), "裁剪位置", 0, 1)
        voice = voices.get(identifier)
        if (voice and voice.text == narration(shot) and edit["duration_mode"] == "manual"
                and shot["duration_seconds"] + 0.001 < voice.duration_seconds):
            raise ValueError("手动时长短于现有配音，请增加时长或修改配音，不能截断旁白。")
        if "subtitle" in edit:
            subtitle = edit["subtitle"]
            if not isinstance(subtitle, dict):
                raise ValueError("字幕设置格式无效。")
            if "text" in subtitle and (not isinstance(subtitle["text"], str) or len(subtitle["text"]) > 10000):
                raise ValueError("字幕必须是文本且不超过一万字。")
            subtitle["font_size"] = _number(subtitle.get("font_size", 48), "字幕字号", 16, 96)
            if subtitle.get("position", "bottom") not in ("top", "center", "bottom"):
                raise ValueError("字幕位置无效。")
            if not isinstance(subtitle.get("color", "#FFFFFF"), str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", subtitle.get("color", "#FFFFFF")):
                raise ValueError("字幕颜色必须为 #RRGGBB。")
            if not isinstance(subtitle.get("background", False), bool):
                raise ValueError("字幕背景必须为开关值。")
        edit = metadata["edit"] = validate_visual_edit(normalize_timing(edit))
        voice_duration = voice.duration_seconds if voice and voice.text == narration(shot) else None
        duration = shot_duration(edit, shot["duration_seconds"], voice_duration)
        validate_cue_bounds(edit.get("subtitle", {}), duration)
    return result


def _snapshot(activity):
    return {
        "shots": [shot.to_dict() for shot in activity.storyboard_analysis.shots] if activity.storyboard_analysis else [],
        "voiceover_segments": [voice.to_dict() for voice in activity.voiceover_segments],
    }


def _entry(activity, label):
    return {"id": uuid.uuid4().hex, "label": str(label or "编辑保存")[:100],
            "created_at": datetime.now().isoformat(timespec="seconds"), "snapshot": _snapshot(activity)}


def history_summary(activity):
    history = activity.metadata.get("edit_history") or {"entries": [], "cursor": -1}
    entries, cursor = history["entries"], history["cursor"]
    return {"cursor": cursor, "can_undo": cursor > 0, "can_redo": 0 <= cursor < len(entries) - 1,
            "entries": [{key: item[key] for key in ("id", "label", "created_at")} for item in entries]}


def check_revision(activity, payload):
    if "base_revision" not in payload:
        return
    summary = history_summary(activity)
    current = summary["entries"][summary["cursor"]]["id"] if summary["cursor"] >= 0 else ""
    if payload["base_revision"] != current:
        raise ValueError("编辑版本已更新，请刷新后重试；未覆盖已有修改。")


def _append(history, activity, label, truncate=True):
    entries = history["entries"]
    if truncate:
        del entries[history["cursor"] + 1:]
    entries.append(_entry(activity, label))
    del entries[:-HISTORY_LIMIT]
    history["cursor"] = len(entries) - 1


def _prepare(activity):
    history = activity.metadata.setdefault("edit_history", {"entries": [], "cursor": -1})
    if not history["entries"]:
        _append(history, activity, "初始版本")
    else:
        current = history["entries"][history["cursor"]]
        snapshot = _snapshot(activity)
        if snapshot["shots"] == current["snapshot"]["shots"]:
            # Preserve newly generated audio before moving away from this revision.
            current["snapshot"]["voiceover_segments"] = snapshot["voiceover_segments"]
        else:
            _append(history, activity, "工作流更新")
    return history


def record_workflow_update(activity):
    history = activity.metadata.get("edit_history")
    if history and history["entries"]:
        current = history["entries"][history["cursor"]]["snapshot"]
        if current != _snapshot(activity):
            _append(history, activity, "工作流更新")


def record_change(state, label, change):
    """Run change(activity) between two history entries so that one undo reverts all of it."""
    activity = state.current_activity
    history = _prepare(activity)
    result = change(activity)
    if _snapshot(activity) != history["entries"][history["cursor"]]["snapshot"]:
        _append(history, activity, label)
    return result


def save_shots(state, shots, label="编辑保存"):
    activity = state.current_activity
    shots = validate_shots(shots, activity)
    history = _prepare(activity)
    original_shots = {shot.shot_id: shot.to_dict() for shot in activity.storyboard_analysis.shots} if activity.storyboard_analysis else {}
    voices = {voice.shot_id: voice for voice in activity.voiceover_segments}
    incoming = {shot["shot_id"]: shot for shot in shots}
    previous = _snapshot(activity)
    state.set_storyboard_shots(shots)
    retained = {voice.shot_id: voice for voice in activity.voiceover_segments}
    for shot in shots:
        source = shot.get("metadata", {}).get("duplicate_of")
        original = original_shots.get(source)
        voice = voices.get(source)
        if (shot["shot_id"] not in original_shots and shot["shot_id"] not in retained
                and source in incoming and original and voice
                and narration(original) == narration(incoming[source]) == narration(shot) == voice.text):
            clone = VoiceoverSegment.from_dict(voice.to_dict())
            clone.shot_id = shot["shot_id"]
            clone.segment_id = f"voiceover_{clone.shot_id}"
            retained[clone.shot_id] = clone
    state.set_voiceover_segments(retained.values())
    # Newly copied audio must obey the same duration checks as existing audio.
    validate_shots(shots, activity)
    if _snapshot(activity) != previous:
        _append(history, activity, label)
    return history_summary(activity)


def change_history(state, payload):
    activity = state.current_activity
    action = payload.get("action")
    if action not in {"undo", "redo", "restore", "checkpoint"}:
        raise ValueError("无效的版本操作。")
    history = _prepare(activity)
    if action == "checkpoint":
        _append(history, activity, payload.get("label") or "手动保存版本")
    else:
        if action in {"undo", "redo"}:
            target = history["cursor"] + (-1 if action == "undo" else 1)
            if target < 0 or target >= len(history["entries"]):
                raise ValueError("没有可撤销或重做的版本。")
        else:
            target = next((index for index, entry in enumerate(history["entries"])
                           if entry["id"] == payload.get("revision_id")), -1)
            if target < 0:
                raise ValueError("所选版本已不存在。")
        entry = history["entries"][target]
        snapshot = deepcopy(entry["snapshot"])
        # Empty initial snapshots are valid: undoing the first manual storyboard.
        if snapshot["shots"]:
            target_activity = deepcopy(activity)
            target_activity.voiceover_segments = [VoiceoverSegment.from_dict(item) for item in snapshot["voiceover_segments"]]
            validate_shots(snapshot["shots"], target_activity)
        state.set_storyboard_shots(snapshot["shots"])
        state.set_voiceover_segments(snapshot["voiceover_segments"])
        state._invalidate(activity, "storyboard")
        if snapshot["shots"]:
            state.build_assembly_plan(save=False)
        if action == "restore":
            _append(history, activity, payload.get("label") or f"恢复：{entry['label']}", truncate=False)
        else:
            history["cursor"] = target
    state._changed(activity)
    return history_summary(activity)
