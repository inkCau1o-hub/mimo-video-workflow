from __future__ import annotations

import json
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar, Union, get_args, get_origin, get_type_hints


try:
    from types import UnionType
except ImportError:  # pragma: no cover - Python < 3.10
    UnionType = None


T = TypeVar("T", bound="JsonDataclassMixin")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _is_union_origin(origin: Any) -> bool:
    return origin is Union or (UnionType is not None and origin is UnionType)


def _to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _to_plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


def _coerce_value(value: Any, annotation: Any) -> Any:
    if value is None:
        return None
    if annotation is Any:
        return value

    origin = get_origin(annotation)
    args = get_args(annotation)

    if _is_union_origin(origin):
        non_none_args = [item for item in args if item is not type(None)]
        for item in non_none_args:
            try:
                return _coerce_value(value, item)
            except (TypeError, ValueError):
                continue
        return value

    if origin in (list, List):
        item_type = args[0] if args else Any
        items = value if isinstance(value, list) else [value]
        return [_coerce_value(item, item_type) for item in items]

    if origin in (dict, Dict):
        key_type = args[0] if args else Any
        value_type = args[1] if len(args) > 1 else Any
        if not isinstance(value, dict):
            return {}
        return {
            _coerce_mapping_key(key, key_type): _coerce_value(item, value_type)
            for key, item in value.items()
        }

    if isinstance(annotation, type) and is_dataclass(annotation):
        if isinstance(value, annotation):
            return value
        if isinstance(value, dict) and hasattr(annotation, "from_dict"):
            return annotation.from_dict(value)

    return value


def _coerce_mapping_key(key: Any, annotation: Any) -> Any:
    if annotation is Any or isinstance(key, annotation if isinstance(annotation, type) else object):
        return key
    if annotation is str:
        return str(key)
    return key


def _from_dict(cls: Type[T], data: Dict[str, Any]) -> T:
    if not isinstance(data, dict):
        raise TypeError(f"{cls.__name__}.from_dict 需要 dict 数据")

    hints = get_type_hints(cls)
    kwargs = {}
    for item in fields(cls):
        if not item.init or item.name not in data:
            continue
        kwargs[item.name] = _coerce_value(data[item.name], hints.get(item.name, item.type))

    for item in fields(cls):
        if item.init and item.name not in kwargs and item.default is MISSING and item.default_factory is MISSING:
            raise TypeError(f"{cls.__name__}.from_dict 缺少必填字段: {item.name}")

    return cls(**kwargs)


def save_json(data: Any, path: Union[str, Path]) -> Path:
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(_to_plain(data), f, ensure_ascii=False, indent=2)
    return output_path


def load_json(path: Union[str, Path], cls: Optional[Type[T]] = None) -> Any:
    input_path = Path(path).expanduser()
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if cls is None:
        return data
    return cls.from_dict(data)


class JsonDataclassMixin:
    def to_dict(self) -> Dict[str, Any]:
        return _to_plain(self)

    @classmethod
    def from_dict(cls: Type[T], data: Dict[str, Any]) -> T:
        return _from_dict(cls, data)

    def save_json(self, path: Union[str, Path]) -> Path:
        return save_json(self, path)

    @classmethod
    def load_json(cls: Type[T], path: Union[str, Path]) -> T:
        return load_json(path, cls=cls)


@dataclass
class ReferenceCopy(JsonDataclassMixin):
    copy_id: str = ""
    title: str = ""
    source_text: str = ""
    source_path: str = ""
    product_name: str = ""
    keywords: List[str] = field(default_factory=list)
    language: str = "zh-CN"
    created_at: str = field(default_factory=_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StoryboardShot(JsonDataclassMixin):
    shot_id: str = ""
    title: str = ""
    script_text: str = ""
    tts_text: str = ""
    duration_seconds: float = 0.0
    visual_tags: List[str] = field(default_factory=list)
    material_requirements: List[str] = field(default_factory=list)
    transition: str = ""
    notes: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StoryboardAnalysis(JsonDataclassMixin):
    analysis_id: str = ""
    reference_copy: Optional[ReferenceCopy] = None
    summary: str = ""
    target_duration_seconds: Optional[float] = None
    shots: List[StoryboardShot] = field(default_factory=list)
    style_notes: List[str] = field(default_factory=list)
    model: str = ""
    raw_response: str = ""
    created_at: str = field(default_factory=_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MaterialAsset(JsonDataclassMixin):
    asset_id: str = ""
    file_path: str = ""
    material_type: str = ""
    scene_summary: str = ""
    visual_tags: List[str] = field(default_factory=list)
    duration_seconds: Optional[float] = None
    usable_shot_ids: List[str] = field(default_factory=list)
    thumbnail_path: str = ""
    analysis: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VoiceoverSegment(JsonDataclassMixin):
    segment_id: str = ""
    shot_id: str = ""
    text: str = ""
    audio_path: str = ""
    start_seconds: float = 0.0
    duration_seconds: float = 0.0
    voice: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AssemblyItem(JsonDataclassMixin):
    item_id: str = ""
    shot_id: str = ""
    material_asset_id: str = ""
    voiceover_segment_id: str = ""
    start_seconds: float = 0.0
    duration_seconds: float = 0.0
    transition: str = ""
    notes: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AssemblyPlan(JsonDataclassMixin):
    plan_id: str = ""
    items: List[AssemblyItem] = field(default_factory=list)
    total_duration_seconds: float = 0.0
    output_path: str = ""
    created_at: str = field(default_factory=_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProductionProject(JsonDataclassMixin):
    project_id: str = ""
    name: str = ""
    project_dir: str = ""
    reference_copy: Optional[ReferenceCopy] = None
    storyboard_analysis: Optional[StoryboardAnalysis] = None
    material_assets: List[MaterialAsset] = field(default_factory=list)
    voiceover_segments: List[VoiceoverSegment] = field(default_factory=list)
    assembly_plan: Optional[AssemblyPlan] = None
    schema_version: int = 1
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)
