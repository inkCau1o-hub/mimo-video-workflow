from __future__ import annotations

import json
import tkinter as tk
from dataclasses import asdict, is_dataclass
from tkinter import scrolledtext, ttk
from typing import Any, Callable, Iterable, Mapping, Sequence


TEXT_FONT_SIZE = 10


def make_text_area(parent: tk.Widget, font_family: str, height: int) -> scrolledtext.ScrolledText:
    """Create a packed ScrolledText area for page output."""
    text = scrolledtext.ScrolledText(
        parent,
        height=height,
        wrap=tk.WORD,
        font=(font_family, TEXT_FONT_SIZE),
    )
    text.pack(fill=tk.BOTH, expand=True)
    return text


def make_labeled_entry(
    parent: tk.Widget,
    label_text: str,
    font_family: str,
    row: int | None = None,
    *,
    textvariable: tk.Variable | None = None,
    width: int = 34,
    show: str | None = None,
    column: int = 0,
    label_sticky: str = tk.W,
    entry_sticky: str = "ew",
    padx: int = 8,
    pady: int = 4,
) -> tuple[ttk.Label, ttk.Entry]:
    """Create a label and entry pair using grid when row is set, otherwise pack."""
    if row is None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=pady)
        label = ttk.Label(frame, text=label_text)
        label.pack(side=tk.LEFT)
        entry = ttk.Entry(
            frame,
            textvariable=textvariable,
            width=width,
            font=(font_family, TEXT_FONT_SIZE),
            show=show,
        )
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(padx, 0))
        return label, entry

    label = ttk.Label(parent, text=label_text)
    label.grid(row=row, column=column, sticky=label_sticky, pady=pady)
    entry = ttk.Entry(
        parent,
        textvariable=textvariable,
        width=width,
        font=(font_family, TEXT_FONT_SIZE),
        show=show,
    )
    entry.grid(row=row, column=column + 1, sticky=entry_sticky, padx=padx, pady=pady)
    try:
        parent.columnconfigure(column + 1, weight=1)
    except tk.TclError:
        pass
    return label, entry


def make_action_row(
    parent: tk.Widget,
    actions: Sequence[Mapping[str, Any] | Sequence[Any]],
    *,
    status_var: tk.Variable | None = None,
    status_style: str = "Info.TLabel",
    default_style: str = "Secondary.TButton",
    pack: bool = True,
    pady: tuple[int, int] | int = (10, 0),
) -> tuple[ttk.Frame, dict[str, ttk.Button]]:
    """Create a row of action buttons.

    Each action may be a dict with text/command/style/state/width/key, or a
    tuple shaped like (text, command[, style]).
    """
    row = ttk.Frame(parent)
    if pack:
        row.pack(fill=tk.X, pady=pady)

    buttons: dict[str, ttk.Button] = {}
    for index, action in enumerate(actions):
        spec = _normalize_action(action, default_style)
        button_kwargs = {
            "text": spec["text"],
            "style": spec.get("style"),
            "state": spec.get("state", tk.NORMAL),
        }
        if spec.get("command") is not None:
            button_kwargs["command"] = spec["command"]
        if spec.get("width") is not None:
            button_kwargs["width"] = spec["width"]
        button = ttk.Button(row, **button_kwargs)
        button.pack(side=tk.LEFT, padx=(0, 8) if index == 0 else 8)
        buttons[spec["key"]] = button

    if status_var is not None:
        ttk.Label(row, textvariable=status_var, style=status_style).pack(side=tk.LEFT, padx=12)

    return row, buttons


def set_text(text_widget: tk.Text, content: Any) -> None:
    """Replace all text in a Text/ScrolledText widget."""
    _with_writable_text(text_widget, lambda: _replace_text(text_widget, content))


def append_text(text_widget: tk.Text, content: Any) -> None:
    """Append text and scroll to the bottom."""
    _with_writable_text(text_widget, lambda: _append_text(text_widget, content))


def render_storyboard_summary(data: Any) -> str:
    """Render storyboard analysis or shot lists as plain text."""
    plain = _to_plain(data)
    plain = _unwrap_mapping(plain, "storyboard_analysis", "storyboard")

    if _is_empty(plain):
        return "分镜概览\n暂无分镜数据。"

    shots = plain if isinstance(plain, list) else _ensure_list(_first(plain, "shots", "items"))
    lines = ["分镜概览"]

    if isinstance(plain, Mapping):
        _append_field(lines, "分析ID", _first(plain, "analysis_id", "id"))
        _append_field(lines, "摘要", _first(plain, "summary", "description"))
        _append_field(lines, "目标时长", _format_seconds(_first(plain, "target_duration_seconds", "duration_seconds")))
        style_notes = _ensure_list(_first(plain, "style_notes", "style", "styles"))
        if style_notes:
            lines.append("风格备注:")
            lines.extend(f"- {_stringify(item)}" for item in style_notes if not _is_empty(item))

    lines.append(f"镜头数量: {len(shots)}")
    if shots:
        lines.append("")
        lines.append("镜头列表:")
        for index, shot in enumerate(shots, start=1):
            shot_map = _as_mapping(shot)
            title = _first(shot_map, "title", "name") or "未命名镜头"
            shot_id = _first(shot_map, "shot_id", "id")
            duration = _format_seconds(_first(shot_map, "duration_seconds", "duration"))
            heading = _join_compact([shot_id, title, duration], " | ")
            lines.append(f"{index}. {heading}")
            _append_field(lines, "文案", _first(shot_map, "script_text", "script", "copy"))
            _append_field(lines, "口播", _first(shot_map, "tts_text", "voiceover_text", "text"))
            _append_field(lines, "视觉标签", _join_values(_first(shot_map, "visual_tags", "tags")))
            _append_field(lines, "素材需求", _join_values(_first(shot_map, "material_requirements", "requirements")))
            _append_field(lines, "转场", _first(shot_map, "transition"))
            _append_field(lines, "备注", _first(shot_map, "notes", "note"))

    return "\n".join(lines).strip()


def render_material_summary(data: Any) -> str:
    """Render material assets or material analysis results as plain text."""
    plain = _to_plain(data)
    plain = _unwrap_mapping(plain, "material_assets", "materials", "assets")

    if _is_empty(plain):
        return "素材库概览\n暂无素材数据。"

    assets = plain if isinstance(plain, list) else [plain]
    lines = ["素材库概览", f"素材数量: {len(assets)}"]

    for index, asset in enumerate(assets, start=1):
        asset_map = _as_mapping(asset)
        title = _join_compact(
            [
                _first(asset_map, "asset_id", "id"),
                _first(asset_map, "material_type", "type", "category"),
                _short_path(_first(asset_map, "file_path", "path", "source_path")),
            ],
            " | ",
        )
        lines.append("")
        lines.append(f"{index}. {title or '未命名素材'}")
        _append_field(lines, "文件", _first(asset_map, "file_path", "path", "source_path"))
        _append_field(lines, "时长", _format_seconds(_first(asset_map, "duration_seconds", "duration")))
        _append_field(lines, "场景", _first(asset_map, "scene_summary", "summary", "description"))
        _append_field(lines, "视觉标签", _join_values(_first(asset_map, "visual_tags", "tags")))
        _append_field(lines, "匹配分镜", _join_values(_first(asset_map, "usable_shot_ids", "shot_ids", "matched_shots")))
        _append_field(lines, "缩略图", _first(asset_map, "thumbnail_path", "thumbnail"))
        analysis = _first(asset_map, "analysis")
        if analysis:
            _append_field(lines, "分析", _compact_json(analysis))

    return "\n".join(lines).strip()


def render_voiceover_summary(data: Any) -> str:
    """Render voiceover segments as plain text."""
    plain = _to_plain(data)
    plain = _unwrap_mapping(plain, "voiceover_segments", "voiceovers", "segments")

    if _is_empty(plain):
        return "配音概览\n暂无配音数据。"

    segments = plain if isinstance(plain, list) else [plain]
    total_duration = sum(_number_or_zero(_first(_as_mapping(item), "duration_seconds", "duration")) for item in segments)
    lines = ["配音概览", f"片段数量: {len(segments)}"]
    if total_duration:
        lines.append(f"总时长: {_format_seconds(total_duration)}")

    for index, segment in enumerate(segments, start=1):
        segment_map = _as_mapping(segment)
        title = _join_compact(
            [
                _first(segment_map, "segment_id", "id"),
                _first(segment_map, "shot_id"),
                _first(segment_map, "voice"),
            ],
            " | ",
        )
        lines.append("")
        lines.append(f"{index}. {title or '未命名配音片段'}")
        _append_field(lines, "文本", _first(segment_map, "text", "tts_text", "voiceover_text"))
        _append_field(lines, "音频", _first(segment_map, "audio_path", "path"))
        _append_field(lines, "起始", _format_seconds(_first(segment_map, "start_seconds", "start")))
        _append_field(lines, "时长", _format_seconds(_first(segment_map, "duration_seconds", "duration")))

    return "\n".join(lines).strip()


def render_assembly_summary(data: Any) -> str:
    """Render an assembly plan or item list as plain text."""
    plain = _to_plain(data)
    plain = _unwrap_mapping(plain, "assembly_plan", "assembly")

    if _is_empty(plain):
        return "合成计划概览\n暂无合成计划数据。"

    items = plain if isinstance(plain, list) else _ensure_list(_first(plain, "items", "clips"))
    lines = ["合成计划概览"]

    if isinstance(plain, Mapping):
        _append_field(lines, "计划ID", _first(plain, "plan_id", "id"))
        _append_field(lines, "总时长", _format_seconds(_first(plain, "total_duration_seconds", "duration_seconds", "duration")))
        _append_field(lines, "输出文件", _first(plain, "output_path", "output"))
        if not items and _looks_like_assembly_item(plain):
            items = [plain]

    lines.append(f"条目数量: {len(items)}")
    for index, item in enumerate(items, start=1):
        item_map = _as_mapping(item)
        title = _join_compact(
            [
                _first(item_map, "item_id", "id"),
                _first(item_map, "shot_id"),
                _format_seconds(_first(item_map, "start_seconds", "start")),
            ],
            " | ",
        )
        lines.append("")
        lines.append(f"{index}. {title or '未命名合成条目'}")
        _append_field(lines, "素材ID", _first(item_map, "material_asset_id", "asset_id", "material_id"))
        _append_field(lines, "配音ID", _first(item_map, "voiceover_segment_id", "segment_id", "voiceover_id"))
        _append_field(lines, "起始", _format_seconds(_first(item_map, "start_seconds", "start")))
        _append_field(lines, "时长", _format_seconds(_first(item_map, "duration_seconds", "duration")))
        _append_field(lines, "转场", _first(item_map, "transition"))
        _append_field(lines, "备注", _first(item_map, "notes", "note"))

    return "\n".join(lines).strip()


def _normalize_action(action: Mapping[str, Any] | Sequence[Any], default_style: str) -> dict[str, Any]:
    if isinstance(action, Mapping):
        text = _stringify(action.get("text", "操作"))
        return {
            "key": _stringify(action.get("key", text)),
            "text": text,
            "command": action.get("command"),
            "style": action.get("style", default_style),
            "state": action.get("state", tk.NORMAL),
            "width": action.get("width"),
        }

    items = list(action)
    text = _stringify(items[0] if items else "操作")
    command = items[1] if len(items) > 1 and callable(items[1]) else None
    style = items[2] if len(items) > 2 else default_style
    return {
        "key": text,
        "text": text,
        "command": command,
        "style": style,
        "state": tk.NORMAL,
        "width": None,
    }


def _replace_text(text_widget: tk.Text, content: Any) -> None:
    text_widget.delete("1.0", tk.END)
    text_widget.insert(tk.END, _stringify(content))
    text_widget.see(tk.END)


def _append_text(text_widget: tk.Text, content: Any) -> None:
    text_widget.insert(tk.END, _stringify(content))
    text_widget.see(tk.END)


def _with_writable_text(text_widget: tk.Text, writer: Callable[[], None]) -> None:
    previous_state = None
    try:
        previous_state = text_widget.cget("state")
        if previous_state == tk.DISABLED:
            text_widget.configure(state=tk.NORMAL)
    except tk.TclError:
        previous_state = None

    writer()

    if previous_state == tk.DISABLED:
        text_widget.configure(state=tk.DISABLED)


def _to_plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _to_plain(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_plain(item) for item in value]
    return value


def _unwrap_mapping(data: Any, *keys: str) -> Any:
    if not isinstance(data, Mapping):
        return data
    for key in keys:
        value = data.get(key)
        if not _is_empty(value):
            return value
    return data


def _as_mapping(value: Any) -> Mapping[str, Any]:
    plain = _to_plain(value)
    return plain if isinstance(plain, Mapping) else {"value": plain}


def _first(mapping: Any, *keys: str) -> Any:
    if not isinstance(mapping, Mapping):
        return None
    for key in keys:
        value = mapping.get(key)
        if not _is_empty(value):
            return value
    return None


def _ensure_list(value: Any) -> list[Any]:
    if _is_empty(value):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _append_field(lines: list[str], label: str, value: Any, prefix: str = "   ") -> None:
    if _is_empty(value):
        return
    text = _stringify(value)
    if not text:
        return
    indented = text.replace("\n", f"\n{prefix}   ")
    lines.append(f"{prefix}{label}: {indented}")


def _join_values(value: Any) -> str:
    if _is_empty(value):
        return ""
    if isinstance(value, (list, tuple, set)):
        return "、".join(_stringify(item) for item in value if not _is_empty(item))
    return _stringify(value)


def _join_compact(values: Iterable[Any], separator: str) -> str:
    return separator.join(_stringify(value) for value in values if not _is_empty(value))


def _format_seconds(value: Any) -> str:
    if _is_empty(value):
        return ""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return _stringify(value)
    if seconds.is_integer():
        return f"{int(seconds)} 秒"
    return f"{seconds:.1f} 秒"


def _number_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _short_path(value: Any) -> str:
    text = _stringify(value)
    if not text:
        return ""
    return text.rstrip("/\\").replace("\\", "/").split("/")[-1]


def _compact_json(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        return _stringify(value)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (Mapping, list, tuple, set)):
        return _compact_json(_to_plain(value))
    return str(value).strip()


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _looks_like_assembly_item(value: Mapping[str, Any]) -> bool:
    keys = {"shot_id", "material_asset_id", "voiceover_segment_id", "start_seconds", "duration_seconds"}
    return any(key in value for key in keys)


__all__ = [
    "make_text_area",
    "make_labeled_entry",
    "make_action_row",
    "set_text",
    "append_text",
    "render_storyboard_summary",
    "render_material_summary",
    "render_voiceover_summary",
    "render_assembly_summary",
]
