from __future__ import annotations

import hashlib
import inspect
import json
import re
import shutil
import subprocess
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from multimodal_interfaces import MaterialAnalyzer


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
    ".mkv",
    ".webm",
    ".flv",
    ".wmv",
}


class MaterialCatalog:
    """Build a normalized catalog of tagged video material assets."""

    def __init__(
        self,
        analyzer: Optional[MaterialAnalyzer] = None,
        output_dir: Optional[str] = None,
        vision_model: Optional[str] = None,
        max_frames: int = 6,
        every_seconds: int = 3,
    ):
        self.analyzer = analyzer or MaterialAnalyzer()
        self.output_dir = Path(output_dir).expanduser() if output_dir else self._default_output_dir()
        self.vision_model = vision_model
        self.max_frames = max_frames
        self.every_seconds = every_seconds

    def analyze_video(
        self,
        video_path: str,
        output_dir: Optional[str] = None,
        vision_model: Optional[str] = None,
        max_frames: Optional[int] = None,
        every_seconds: Optional[int] = None,
        save_path: Optional[str] = None,
        as_schema: bool = False,
    ) -> Any:
        """Analyze one video and return a MaterialAsset-style record.

        This method delegates frame extraction and model calls to
        multimodal_interfaces.MaterialAnalyzer.
        """
        target_output_dir = Path(output_dir).expanduser() if output_dir else self.output_dir
        result = self.analyzer.analyze_video_material(
            video_path,
            str(target_output_dir),
            vision_model=vision_model or self.vision_model,
            max_frames=max_frames if max_frames is not None else self.max_frames,
            every_seconds=every_seconds if every_seconds is not None else self.every_seconds,
        )
        asset = self.asset_from_analysis_result(result, fallback_path=video_path)
        if save_path:
            self.save_asset(asset, save_path)
        return self.as_material_asset(asset) if as_schema else asset

    def analyze_batch(
        self,
        video_paths: Iterable[str],
        output_dir: Optional[str] = None,
        vision_model: Optional[str] = None,
        max_frames: Optional[int] = None,
        every_seconds: Optional[int] = None,
        save_path: Optional[str] = None,
        continue_on_error: bool = True,
        as_schema: bool = False,
        progress_callback=None,
        cancel_check=None,
    ) -> List[Any]:
        assets = []
        video_paths = list(video_paths)
        for index, video_path in enumerate(video_paths, start=1):
            if cancel_check:
                cancel_check()
            try:
                asset = self.analyze_video(
                    video_path,
                    output_dir=output_dir,
                    vision_model=vision_model,
                    max_frames=max_frames,
                    every_seconds=every_seconds,
                    as_schema=False,
                )
            except Exception as exc:
                if not continue_on_error:
                    raise
                asset = self.error_asset(video_path, exc)
            assets.append(asset)
            if cancel_check:
                cancel_check()
            if progress_callback:
                progress_callback(10 + 80 * index / max(len(video_paths), 1), f"素材 {index}/{len(video_paths)}")

        if save_path:
            self.save_catalog(assets, save_path)

        if as_schema:
            return [self.as_material_asset(asset) for asset in assets]
        return assets

    def analyze_videos(self, video_paths: Iterable[str], **kwargs: Any) -> List[Any]:
        return self.analyze_batch(video_paths, **kwargs)

    def analyze_directory(
        self,
        directory: str,
        recursive: bool = True,
        **kwargs: Any,
    ) -> List[Any]:
        paths = self.scan_videos(directory, recursive=recursive)
        return self.analyze_batch(paths, **kwargs)

    def scan_videos(self, directory: str, recursive: bool = True) -> List[str]:
        root = Path(directory).expanduser()
        if not root.exists():
            raise FileNotFoundError(f"Directory does not exist: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"Not a directory: {root}")

        iterator = root.rglob("*") if recursive else root.glob("*")
        return sorted(
            str(path)
            for path in iterator
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        )

    def asset_from_analysis_result(
        self,
        result: Mapping[str, Any],
        fallback_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        video_path = result.get("video_path") or result.get("path") or fallback_path or ""
        raw_analysis = result.get("analysis", result)
        parsed = parse_model_json(raw_analysis)
        return self.build_asset(
            path=str(video_path),
            parsed_analysis=parsed,
            raw_analysis=raw_analysis,
            frames=result.get("frames") or [],
        )

    def build_asset(
        self,
        path: str,
        parsed_analysis: Optional[Mapping[str, Any]] = None,
        raw_analysis: Any = None,
        frames: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        parsed = dict(parsed_analysis or {})
        video_path = _normalize_path(path)
        material_type = _first_text(
            parsed,
            "material_type",
            "type",
            "category",
            "asset_category",
            default="other",
        )
        summary = _first_text(
            parsed,
            "summary",
            "scene_summary",
            "description",
            "caption",
            "content_summary",
            default="",
        )
        visual_tags = _first_list(
            parsed,
            "visual_tags",
            "visual_style",
            "style_tags",
            "key_visuals",
            "objects",
        )
        scene_tags = _first_list(
            parsed,
            "scene_tags",
            "scene",
            "scenes",
            "scene_type",
            "environment",
            "environment_tags",
        )
        duration = _first_duration(parsed, "duration", "duration_seconds", "length")
        if duration is None:
            duration = probe_duration(video_path)

        asset = {
            "schema": "MaterialAsset",
            "schema_version": 1,
            "asset_id": _asset_id(video_path),
            "asset_type": "video",
            "path": video_path,
            "filename": Path(video_path).name if video_path else "",
            "material_type": material_type or "other",
            "summary": summary,
            "visual_tags": visual_tags,
            "scene_tags": scene_tags,
            "duration": duration,
            "frames": list(frames or []),
            "metadata": {
                "parsed_analysis": parsed,
                "raw_analysis": raw_analysis,
            },
        }
        return asset

    def error_asset(self, video_path: str, error: Exception) -> Dict[str, Any]:
        path = _normalize_path(video_path)
        return {
            "schema": "MaterialAsset",
            "schema_version": 1,
            "asset_id": _asset_id(path),
            "asset_type": "video",
            "path": path,
            "filename": Path(path).name if path else "",
            "material_type": "unknown",
            "summary": "",
            "visual_tags": [],
            "scene_tags": [],
            "duration": probe_duration(path),
            "frames": [],
            "metadata": {
                "error": str(error),
            },
        }

    def as_material_asset(self, asset: Mapping[str, Any]) -> Any:
        material_asset_class = _load_material_asset_class()
        data = dict(asset)
        if material_asset_class is None:
            return data

        schema_data = {
            "asset_id": data.get("asset_id", ""),
            "file_path": data.get("file_path") or data.get("path") or data.get("video_path") or "",
            "material_type": data.get("material_type", ""),
            "scene_summary": data.get("scene_summary") or data.get("summary") or "",
            "visual_tags": _dedupe_text(
                _coerce_list(data.get("visual_tags")) + _coerce_list(data.get("scene_tags"))
            ),
            "duration_seconds": data.get("duration_seconds") or data.get("duration"),
            "thumbnail_path": _first_frame(data.get("frames") or []),
            "analysis": data.get("metadata", {}).get("parsed_analysis", {}) if isinstance(data.get("metadata"), Mapping) else {},
            "metadata": data,
        }

        try:
            return material_asset_class(**schema_data)
        except Exception:
            try:
                signature = inspect.signature(material_asset_class)
                accepted = {
                    key: value
                    for key, value in schema_data.items()
                    if key in signature.parameters
                }
                return material_asset_class(**accepted)
            except Exception:
                return data

    def save_asset(self, asset: Any, output_path: str) -> str:
        data = serialize_asset(asset)
        path = Path(output_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return str(path)

    def save_catalog(self, assets: Iterable[Any], output_path: str) -> str:
        payload = {
            "schema": "MaterialCatalog",
            "schema_version": 1,
            "assets": [serialize_asset(asset) for asset in assets],
        }
        path = Path(output_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return str(path)

    def _default_output_dir(self) -> Path:
        try:
            import config

            return Path(config.OUTPUT_DIR).expanduser()
        except Exception:
            return Path.cwd() / "output"


def parse_model_json(raw: Any) -> Dict[str, Any]:
    """Parse plain JSON, fenced JSON, or JSON embedded in model prose."""
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, list):
        return {"items": raw}

    text = str(raw).strip()
    if not text:
        return {}

    candidates = _json_candidates(text)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, list):
            return {"items": value}

    decoder = json.JSONDecoder()
    for start in _json_start_indexes(text):
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, list):
            return {"items": value}

    return {}


def serialize_asset(asset: Any) -> Any:
    if is_dataclass(asset):
        return asdict(asset)
    if hasattr(asset, "model_dump"):
        return asset.model_dump()
    if hasattr(asset, "dict"):
        return asset.dict()
    if isinstance(asset, Mapping):
        return dict(asset)
    if isinstance(asset, list):
        return [serialize_asset(item) for item in asset]
    if hasattr(asset, "__dict__"):
        return dict(asset.__dict__)
    return asset


def probe_duration(video_path: str) -> Optional[float]:
    path = Path(video_path).expanduser() if video_path else None
    if not path or not path.exists():
        return None

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        cmd = [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        try:
            completed = subprocess.run(cmd, capture_output=True, text=True, check=True)
            duration = _coerce_duration(completed.stdout.strip())
            if duration is not None:
                return duration
        except Exception:
            pass

    return _probe_duration_with_ffmpeg(path)


def _probe_duration_with_ffmpeg(path: Path) -> Optional[float]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None

    cmd = [ffmpeg, "-hide_banner", "-i", str(path)]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True)
    except Exception:
        return None

    output = f"{completed.stderr}\n{completed.stdout}"
    match = re.search(r"Duration:\s*([0-9:.]+)", output)
    if not match:
        return None
    return _coerce_duration(match.group(1))


def _load_material_asset_class() -> Any:
    try:
        from production_schema import MaterialAsset

        return MaterialAsset
    except Exception:
        return None


def _json_candidates(text: str) -> List[str]:
    candidates = [text]
    fence_pattern = re.compile(r"```\s*(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
    candidates.extend(match.group(1).strip() for match in fence_pattern.finditer(text))
    stripped = text.strip("`").strip()
    if stripped.lower().startswith("json"):
        stripped = stripped[4:].strip()
    if stripped and stripped not in candidates:
        candidates.append(stripped)
    return candidates


def _json_start_indexes(text: str) -> List[int]:
    return [index for index, char in enumerate(text) if char in "{["]


def _normalize_path(path: str) -> str:
    if not path:
        return ""
    return str(Path(path).expanduser())


def _first_frame(frames: Iterable[str]) -> str:
    for frame in frames:
        if frame:
            return str(frame)
    return ""


def _asset_id(path: str) -> str:
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
    return f"material_{digest}"


def _first_text(data: Mapping[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        if key in data:
            value = data.get(key)
            if value is None:
                continue
            if isinstance(value, (list, tuple, set)):
                text = ", ".join(str(item).strip() for item in value if str(item).strip())
            elif isinstance(value, Mapping):
                text = json.dumps(value, ensure_ascii=False)
            else:
                text = str(value).strip()
            if text:
                return text
    return default


def _first_list(data: Mapping[str, Any], *keys: str) -> List[str]:
    for key in keys:
        if key not in data:
            continue
        values = _coerce_list(data.get(key))
        if values:
            return values
    return []


def _coerce_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [str(key).strip() for key, item in value.items() if item and str(key).strip()]
    if isinstance(value, (list, tuple, set)):
        items = []
        for item in value:
            items.extend(_coerce_list(item) if isinstance(item, (list, tuple, set)) else [item])
        return _dedupe_text(items)
    if isinstance(value, str):
        parts = re.split(r"[,;\n\r|/]+|[\uFF0C\u3001\uFF1B]", value)
        return _dedupe_text(parts)
    return _dedupe_text([value])


def _dedupe_text(values: Iterable[Any]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _first_duration(data: Mapping[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        if key not in data:
            continue
        duration = _coerce_duration(data.get(key))
        if duration is not None:
            return duration
    return None


def _coerce_duration(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        try:
            seconds = 0.0
            for part in parts:
                seconds = seconds * 60 + float(part)
            return seconds
        except ValueError:
            return None

    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        duration = float(match.group(0))
    except ValueError:
        return None
    return duration if duration >= 0 else None
