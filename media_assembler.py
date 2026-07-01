"""Build label-based video/audio assembly plans.

This module is intentionally self-contained: it accepts plain dicts,
dataclasses, pydantic-style objects, and future production_schema objects
without importing production_schema at module import time.
"""

from __future__ import annotations

import importlib
import json
import math
import re
import shlex
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


Number = Optional[float]


@dataclass
class LabelMatch:
    """Score details for one scene-to-asset match."""

    asset_id: str
    asset_path: str
    score: float
    breakdown: Dict[str, float]
    matched_terms: List[str]
    missing_terms: List[str]
    asset: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "asset_path": self.asset_path,
            "score": round(self.score, 4),
            "breakdown": {key: round(value, 4) for key, value in self.breakdown.items()},
            "matched_terms": list(self.matched_terms),
            "missing_terms": list(self.missing_terms),
            "asset": self.asset,
        }


class LabelMatcher:
    """Match storyboard scenes to material assets with transparent scoring."""

    DEFAULT_WEIGHTS = {
        "visual_tags": 0.45,
        "material_requirements": 0.30,
        "material_type": 0.15,
        "summary_keywords": 0.10,
    }

    def __init__(self, weights: Optional[Mapping[str, float]] = None):
        merged = dict(self.DEFAULT_WEIGHTS)
        if weights:
            merged.update({key: float(value) for key, value in weights.items()})
        self.weights = merged

    def match_scene(
        self,
        scene: Any,
        assets: Sequence[Any],
        top_k: int = 3,
        min_score: float = 0.0,
        scene_index: int = 0,
    ) -> List[LabelMatch]:
        normalized_scene = normalize_scene(scene, scene_index)
        normalized_assets = [normalize_asset(asset, index) for index, asset in enumerate(assets)]

        matches = [
            self.score_scene_asset(normalized_scene, asset)
            for asset in normalized_assets
        ]
        matches.sort(key=lambda item: item.score, reverse=True)

        if min_score is not None:
            matches = [item for item in matches if item.score >= min_score]
        if top_k and top_k > 0:
            matches = matches[:top_k]
        return matches

    def score_scene_asset(self, scene: Any, asset: Any) -> LabelMatch:
        normalized_scene = scene if _looks_normalized_scene(scene) else normalize_scene(scene, 0)
        normalized_asset = asset if _looks_normalized_asset(asset) else normalize_asset(asset, 0)

        asset_terms = set(normalized_asset["tags"])
        asset_type_terms = set(_terms_from_value(normalized_asset.get("material_type")))
        candidate_terms = asset_terms | asset_type_terms
        candidate_text = normalized_asset.get("search_text", "")
        summary_text = normalized_asset.get("summary_text", "")

        visual_terms = normalized_scene["visual_tags"]
        requirement_terms = normalized_scene["material_requirement_terms"]
        required_type_terms = normalized_scene["required_material_types"]
        all_scene_terms = _dedupe_terms(visual_terms + requirement_terms + required_type_terms)

        visual_score = _coverage(visual_terms, candidate_terms, candidate_text)
        requirement_score = _coverage(requirement_terms, candidate_terms, candidate_text)
        type_score = _coverage(required_type_terms, asset_type_terms or candidate_terms, candidate_text)
        summary_score = _coverage(all_scene_terms, set(), summary_text)

        breakdown = {
            "visual_tags": visual_score,
            "material_requirements": requirement_score,
            "material_type": type_score,
            "summary_keywords": summary_score,
        }
        score = self._weighted_score(breakdown, {
            "visual_tags": bool(visual_terms),
            "material_requirements": bool(requirement_terms),
            "material_type": bool(required_type_terms),
            "summary_keywords": bool(all_scene_terms),
        })

        matched_terms = [
            term for term in all_scene_terms
            if _term_matches(term, candidate_terms, candidate_text)
        ]
        missing_terms = [
            term for term in all_scene_terms
            if term not in matched_terms
        ]

        return LabelMatch(
            asset_id=normalized_asset["asset_id"],
            asset_path=normalized_asset["path"],
            score=score,
            breakdown=breakdown,
            matched_terms=matched_terms,
            missing_terms=missing_terms,
            asset=normalized_asset,
        )

    def _weighted_score(self, breakdown: Mapping[str, float], active: Mapping[str, bool]) -> float:
        total_weight = 0.0
        weighted = 0.0
        for key, value in breakdown.items():
            if not active.get(key):
                continue
            weight = max(0.0, self.weights.get(key, 0.0))
            weighted += weight * max(0.0, min(1.0, value))
            total_weight += weight
        if total_weight <= 0:
            return 0.0
        return weighted / total_weight


class AssemblyPlanner:
    """Generate AssemblyPlan-like data from storyboard, assets, and voiceover."""

    def __init__(
        self,
        matcher: Optional[LabelMatcher] = None,
        default_scene_duration: float = 4.0,
        fps: int = 30,
        resolution: Tuple[int, int] = (1080, 1920),
        min_match_score: float = 0.0,
        alternatives: int = 3,
        prefer_schema: bool = False,
    ):
        self.matcher = matcher or LabelMatcher()
        self.default_scene_duration = float(default_scene_duration)
        self.fps = int(fps)
        self.resolution = tuple(resolution)
        self.min_match_score = float(min_match_score)
        self.alternatives = int(alternatives)
        self.prefer_schema = bool(prefer_schema)

    def create_plan(
        self,
        storyboard_analysis: Any,
        material_assets: Sequence[Any],
        voiceover_segments: Optional[Sequence[Any]] = None,
        output_path: Optional[str] = None,
        as_schema: Optional[bool] = None,
    ) -> Any:
        scenes = [
            normalize_scene(scene, index)
            for index, scene in enumerate(_extract_collection(
                storyboard_analysis,
                ("scenes", "shots", "segments", "storyboards", "storyboard", "items"),
            ))
        ]
        if not scenes and storyboard_analysis is not None:
            scenes = [normalize_scene(storyboard_analysis, 0)]

        assets = [normalize_asset(asset, index) for index, asset in enumerate(material_assets or [])]
        voiceovers = [
            normalize_voiceover(segment, index)
            for index, segment in enumerate(voiceover_segments or [])
        ]

        warnings: List[str] = []
        if not scenes:
            warnings.append("No storyboard scenes were provided.")
        if not assets:
            warnings.append("No material assets were provided.")

        voiceover_by_scene = _index_voiceovers(voiceovers)
        video_clips: List[Dict[str, Any]] = []
        audio_clips: List[Dict[str, Any]] = []
        segments: List[Dict[str, Any]] = []
        cursor = 0.0

        for index, scene in enumerate(scenes):
            voiceover = _voiceover_for_scene(scene, index, voiceover_by_scene, voiceovers)
            start, end, duration = self._resolve_timing(scene, voiceover, cursor)
            cursor = end

            matches = self.matcher.match_scene(
                scene,
                assets,
                top_k=max(self.alternatives, 1),
                min_score=0.0,
                scene_index=index,
            )
            selected = matches[0] if matches and matches[0].score >= self.min_match_score else None
            alternatives = [match.to_dict() for match in matches]

            if not selected:
                warnings.append(f"No material asset matched scene {scene['scene_id']}.")

            video_clip = None
            if selected:
                video_clip = self._build_video_clip(scene, selected, start, end, duration)
                video_clips.append(video_clip)

            audio_clip = None
            if voiceover and voiceover.get("audio_path"):
                audio_clip = self._build_audio_clip(scene, voiceover, start, end, duration)
                audio_clips.append(audio_clip)
            elif voiceover_segments:
                warnings.append(f"No usable voiceover audio path for scene {scene['scene_id']}.")

            segments.append({
                "segment_id": scene["scene_id"],
                "shot_id": scene["scene_id"],
                "scene_index": index,
                "timeline_start": _round_seconds(start),
                "timeline_end": _round_seconds(end),
                "duration": _round_seconds(duration),
                "script_text": scene.get("text", ""),
                "visual_tags": scene["visual_tags"],
                "material_requirements": scene["material_requirements"],
                "transition": scene.get("transition", ""),
                "notes": scene.get("notes", ""),
                "selected_asset": selected.asset if selected else None,
                "match": selected.to_dict() if selected else None,
                "alternatives": alternatives,
                "video_clip": video_clip,
                "voiceover_clip": audio_clip,
            })

        timeline_duration = max(cursor, 0.0)
        plan = {
            "schema": "AssemblyPlan",
            "schema_version": "dict-v1",
            "source": "media_assembler.AssemblyPlanner",
            "timeline": {
                "duration": _round_seconds(timeline_duration),
                "fps": self.fps,
                "resolution": {
                    "width": int(self.resolution[0]),
                    "height": int(self.resolution[1]),
                },
            },
            "tracks": [
                {
                    "track_id": "video_main",
                    "kind": "video",
                    "clips": video_clips,
                },
                {
                    "track_id": "voiceover",
                    "kind": "audio",
                    "clips": audio_clips,
                },
            ],
            "segments": segments,
            "assets": assets,
            "voiceovers": voiceovers,
            "output_path": output_path,
            "warnings": warnings,
        }

        should_schema = self.prefer_schema if as_schema is None else bool(as_schema)
        if should_schema:
            return schema_object_or_dict("AssemblyPlan", plan)
        return plan

    def build_plan(self, *args: Any, **kwargs: Any) -> Any:
        return self.create_plan(*args, **kwargs)

    def _resolve_timing(
        self,
        scene: Mapping[str, Any],
        voiceover: Optional[Mapping[str, Any]],
        cursor: float,
    ) -> Tuple[float, float, float]:
        explicit_start = scene.get("start")
        explicit_end = scene.get("end")
        duration = scene.get("duration")

        if duration is None and voiceover:
            duration = voiceover.get("duration")
        if duration is None and explicit_start is not None and explicit_end is not None:
            duration = max(0.0, float(explicit_end) - float(explicit_start))
        if duration is None or duration <= 0:
            duration = self.default_scene_duration

        start = float(explicit_start) if explicit_start is not None else cursor
        end = float(explicit_end) if explicit_end is not None and explicit_end > start else start + duration
        duration = max(0.0, end - start)
        return start, end, duration

    def _build_video_clip(
        self,
        scene: Mapping[str, Any],
        match: LabelMatch,
        start: float,
        end: float,
        duration: float,
    ) -> Dict[str, Any]:
        asset_duration = match.asset.get("duration")
        source_out = duration
        if asset_duration is not None:
            source_out = min(duration, max(0.0, float(asset_duration)))

        return {
            "clip_id": f"video_{scene['scene_id']}",
            "track_id": "video_main",
            "kind": "video",
            "asset_id": match.asset_id,
            "source_path": match.asset_path,
            "source_in": 0.0,
            "source_out": _round_seconds(source_out),
            "timeline_start": _round_seconds(start),
            "timeline_end": _round_seconds(end),
            "duration": _round_seconds(duration),
            "fit": "cover",
            "match_score": round(match.score, 4),
        }

    def _build_audio_clip(
        self,
        scene: Mapping[str, Any],
        voiceover: Mapping[str, Any],
        start: float,
        end: float,
        duration: float,
    ) -> Dict[str, Any]:
        return {
            "clip_id": f"voiceover_{scene['scene_id']}",
            "track_id": "voiceover",
            "kind": "audio",
            "voiceover_id": voiceover["voiceover_id"],
            "source_path": voiceover["audio_path"],
            "source_in": 0.0,
            "source_out": _round_seconds(duration),
            "timeline_start": _round_seconds(start),
            "timeline_end": _round_seconds(end),
            "duration": _round_seconds(duration),
            "text": voiceover.get("text", ""),
        }


class FFmpegAssemblyRenderer:
    """Safe ffmpeg command builder for future rendering integration.

    The renderer does not execute commands. It validates inputs and returns an
    argv list that callers can inspect, log, or run explicitly elsewhere.
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg_path = ffmpeg_path

    def validate_plan(
        self,
        plan: Any,
        output_path: str,
        require_existing_inputs: bool = False,
    ) -> Dict[str, Any]:
        data = _to_plain_data(plan)
        errors: List[str] = []
        warnings: List[str] = []

        if not isinstance(data, Mapping):
            errors.append("Plan must be a mapping or a serializable schema object.")
            data = {}

        video_clip = self._first_clip(data, "video")
        audio_clip = self._first_clip(data, "audio")
        if not video_clip:
            errors.append("Plan has no video clip to render.")

        output = self._safe_path(output_path, "output_path", errors)
        input_paths: List[Path] = []

        for label, clip in (("video", video_clip), ("audio", audio_clip)):
            if not clip:
                continue
            raw_path = clip.get("source_path") or clip.get("path") or clip.get("asset_path")
            path = self._safe_path(raw_path, f"{label}_source_path", errors)
            if path is None:
                continue
            input_paths.append(path)
            if require_existing_inputs and not path.exists():
                errors.append(f"{label} source does not exist: {path}")

        if output:
            for input_path in input_paths:
                if _same_path(output, input_path):
                    errors.append("Output path must not be the same as an input path.")
            if not output.parent.exists():
                warnings.append(f"Output directory does not exist yet: {output.parent}")

        if not audio_clip:
            warnings.append("No voiceover/audio clip found; command will render video without audio.")

        return {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
            "video_clip": video_clip,
            "audio_clip": audio_clip,
            "output_path": str(output) if output else output_path,
        }

    def build_command(
        self,
        plan: Any,
        output_path: str,
        overwrite: bool = False,
        include_voiceover: bool = True,
        require_existing_inputs: bool = False,
    ) -> List[str]:
        validation = self.validate_plan(
            plan,
            output_path,
            require_existing_inputs=require_existing_inputs,
        )
        if not validation["ok"]:
            raise ValueError("; ".join(validation["errors"]))

        video_clip = validation["video_clip"]
        audio_clip = validation["audio_clip"] if include_voiceover else None
        video_path = str(Path(video_clip["source_path"]).expanduser())
        output = str(Path(output_path).expanduser())

        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y" if overwrite else "-n",
            "-i",
            video_path,
        ]

        if audio_clip and audio_clip.get("source_path"):
            audio_path = str(Path(audio_clip["source_path"]).expanduser())
            command.extend([
                "-i",
                audio_path,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                output,
            ])
        else:
            command.extend([
                "-map",
                "0:v:0",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-an",
                output,
            ])

        return command

    def command_string(self, *args: Any, **kwargs: Any) -> str:
        return shlex.join(self.build_command(*args, **kwargs))

    def render(self, *args: Any, dry_run: bool = True, **kwargs: Any) -> List[str]:
        command = self.build_command(*args, **kwargs)
        if dry_run:
            return command
        raise RuntimeError(
            "FFmpegAssemblyRenderer does not execute commands yet; inspect "
            "build_command() output and run it explicitly from a trusted caller."
        )

    def _first_clip(self, plan: Mapping[str, Any], kind: str) -> Optional[Dict[str, Any]]:
        for track in plan.get("tracks", []) or []:
            if track.get("kind") != kind:
                continue
            clips = track.get("clips") or []
            if clips:
                return clips[0]
        for segment in plan.get("segments", []) or []:
            clip_key = "video_clip" if kind == "video" else "voiceover_clip"
            clip = segment.get(clip_key)
            if clip:
                return clip
        metadata = plan.get("metadata") or {}
        if isinstance(metadata, Mapping):
            nested_plan = metadata.get("dict_plan") or metadata.get("assembly_plan")
            if isinstance(nested_plan, Mapping):
                return self._first_clip(nested_plan, kind)
        return None

    def _safe_path(self, value: Any, label: str, errors: List[str]) -> Optional[Path]:
        if not value or not isinstance(value, (str, Path)):
            errors.append(f"Missing {label}.")
            return None
        text = str(value)
        if "\x00" in text or "\n" in text or "\r" in text:
            errors.append(f"Unsafe characters in {label}.")
            return None
        return Path(text).expanduser()


def schema_object_or_dict(class_name: str, payload: Mapping[str, Any]) -> Any:
    """Convert payload to production_schema.<class_name> when available."""

    try:
        module = importlib.import_module("production_schema")
    except Exception:
        return dict(payload)

    cls = getattr(module, class_name, None)
    if cls is None:
        return dict(payload)

    schema_payload = dict(payload)
    if class_name == "AssemblyPlan":
        schema_payload = _schema_payload_for_assembly_plan(module, payload)

    if hasattr(cls, "model_validate"):
        try:
            return cls.model_validate(schema_payload)
        except Exception:
            return dict(payload)
    if hasattr(cls, "parse_obj"):
        try:
            return cls.parse_obj(schema_payload)
        except Exception:
            return dict(payload)
    if hasattr(cls, "from_dict"):
        try:
            return cls.from_dict(schema_payload)
        except Exception:
            return dict(payload)
    try:
        return cls(**schema_payload)
    except Exception:
        return dict(payload)


def _schema_payload_for_assembly_plan(module: Any, payload: Mapping[str, Any]) -> Dict[str, Any]:
    item_cls = getattr(module, "AssemblyItem", None)
    items = []
    for segment in payload.get("segments", []) or []:
        match = segment.get("match") or {}
        selected_asset = segment.get("selected_asset") or {}
        voiceover_clip = segment.get("voiceover_clip") or {}
        item_data = {
            "item_id": str(segment.get("segment_id") or segment.get("shot_id") or ""),
            "shot_id": str(segment.get("shot_id") or segment.get("segment_id") or ""),
            "material_asset_id": str(
                selected_asset.get("asset_id")
                or match.get("asset_id")
                or ""
            ),
            "voiceover_segment_id": str(voiceover_clip.get("voiceover_id") or ""),
            "start_seconds": float(segment.get("timeline_start") or 0.0),
            "duration_seconds": float(segment.get("duration") or 0.0),
            "transition": str(segment.get("transition") or ""),
            "notes": str(segment.get("notes") or ""),
            "metadata": {
                "match": match,
                "video_clip": segment.get("video_clip"),
                "voiceover_clip": voiceover_clip,
                "alternatives": segment.get("alternatives") or [],
                "visual_tags": segment.get("visual_tags") or [],
                "material_requirements": segment.get("material_requirements"),
            },
        }
        if item_cls is not None and hasattr(item_cls, "from_dict"):
            try:
                items.append(item_cls.from_dict(item_data))
                continue
            except Exception:
                pass
        if item_cls is not None:
            try:
                items.append(item_cls(**item_data))
                continue
            except Exception:
                pass
        items.append(item_data)

    return {
        "plan_id": str(payload.get("plan_id") or "assembly_plan"),
        "items": items,
        "total_duration_seconds": float((payload.get("timeline") or {}).get("duration") or 0.0),
        "output_path": str(payload.get("output_path") or ""),
        "metadata": {
            "schema_version": payload.get("schema_version"),
            "timeline": payload.get("timeline") or {},
            "tracks": payload.get("tracks") or [],
            "assets": payload.get("assets") or [],
            "voiceovers": payload.get("voiceovers") or [],
            "warnings": payload.get("warnings") or [],
            "dict_plan": dict(payload),
        },
    }


def normalize_scene(scene: Any, index: int = 0) -> Dict[str, Any]:
    data = _to_plain_mapping(scene)
    requirements = _first_value(
        data,
        "material_requirements",
        "materials",
        "required_materials",
        "shot_requirements",
        default={},
    )

    visual_tags = _dedupe_terms(_terms_from_values([
        data.get("visual_tags"),
        data.get("tags"),
        data.get("shot_tags"),
        data.get("scene_tags"),
    ]))
    requirement_terms = _dedupe_terms(_terms_from_values([
        requirements,
        data.get("required_tags"),
        data.get("required_visual_tags"),
        data.get("material_tags"),
        data.get("visual_description"),
    ]))
    required_types = _dedupe_terms(_terms_from_values([
        _mapping_value(requirements, "material_type"),
        _mapping_value(requirements, "material_types"),
        _mapping_value(requirements, "required_types"),
        data.get("material_type"),
        data.get("preferred_material_type"),
        data.get("required_material_type"),
    ]))

    start = _number_or_none(_first_value(data, "start", "start_time", "timeline_start"))
    end = _number_or_none(_first_value(data, "end", "end_time", "timeline_end"))
    duration = _number_or_none(_first_value(data, "duration", "duration_seconds", "estimated_duration"))

    return {
        "scene_id": str(_first_value(data, "scene_id", "shot_id", "segment_id", "id", default=f"scene_{index + 1:03d}")),
        "index": index,
        "text": _string_or_empty(_first_value(
            data,
            "script_text",
            "text",
            "copy",
            "narration",
            "voiceover_text",
            "description",
            "summary",
        )),
        "visual_tags": visual_tags,
        "material_requirements": _to_plain_data(requirements),
        "material_requirement_terms": requirement_terms,
        "required_material_types": required_types,
        "start": start,
        "end": end,
        "duration": duration,
        "transition": _string_or_empty(_first_value(data, "transition", "transition_suggestion")),
        "notes": _string_or_empty(_first_value(data, "notes", "note", "remark")),
        "raw": data,
    }


def normalize_asset(asset: Any, index: int = 0) -> Dict[str, Any]:
    data = _to_plain_mapping(asset)
    analysis = _parse_json_maybe(data.get("analysis"))
    if isinstance(analysis, Mapping):
        merged = dict(data)
        for key, value in analysis.items():
            merged.setdefault(key, value)
        data = merged

    path = _string_or_empty(_first_value(
        data,
        "path",
        "video_path",
        "file_path",
        "asset_path",
        "source_path",
        "uri",
        "url",
    ))
    material_type = _string_or_empty(_first_value(
        data,
        "material_type",
        "type",
        "category",
    ))
    summary = _string_or_empty(_first_value(
        data,
        "summary",
        "scene_summary",
        "description",
        "caption",
        "useful_for_copywriting",
    ))
    visual_style = _string_or_empty(data.get("visual_style"))
    tags = _dedupe_terms(_terms_from_values([
        data.get("tags"),
        data.get("visual_tags"),
        data.get("scene_tags"),
        data.get("labels"),
        material_type,
        visual_style,
    ]))
    summary_text = " ".join(part for part in [summary, visual_style, _string_or_empty(data.get("useful_for_copywriting"))] if part)
    search_text = " ".join(part for part in [
        " ".join(tags),
        material_type,
        summary_text,
        _string_or_empty(data.get("risks")),
    ] if part).lower()

    asset_id = _first_value(data, "asset_id", "material_id", "id", "name")
    if not asset_id:
        asset_id = Path(path).stem if path else f"asset_{index + 1:03d}"

    return {
        "asset_id": str(asset_id),
        "index": index,
        "path": path,
        "material_type": material_type,
        "tags": tags,
        "summary": summary,
        "summary_text": summary_text.lower(),
        "search_text": search_text,
        "duration": _number_or_none(_first_value(data, "duration", "duration_seconds")),
        "raw": data,
    }


def normalize_voiceover(segment: Any, index: int = 0) -> Dict[str, Any]:
    data = _to_plain_mapping(segment)
    start = _number_or_none(_first_value(data, "start", "start_time", "start_seconds", "timeline_start"))
    end = _number_or_none(_first_value(data, "end", "end_time", "end_seconds", "timeline_end"))
    duration = _number_or_none(_first_value(data, "duration", "duration_seconds"))
    if duration is None and start is not None and end is not None:
        duration = max(0.0, end - start)

    voiceover_id = _first_value(data, "voiceover_id", "segment_id", "scene_id", "id")
    if not voiceover_id:
        voiceover_id = f"voiceover_{index + 1:03d}"

    return {
        "voiceover_id": str(voiceover_id),
        "scene_id": _string_or_empty(_first_value(data, "scene_id", "shot_id", "segment_id")),
        "index": index,
        "audio_path": _string_or_empty(_first_value(data, "audio_path", "path", "file_path", "source_path")),
        "text": _string_or_empty(_first_value(data, "text", "script_text", "voiceover_text", "narration")),
        "start": start,
        "end": end,
        "duration": duration,
        "raw": data,
    }


def _looks_normalized_scene(value: Any) -> bool:
    return isinstance(value, Mapping) and "scene_id" in value and "material_requirement_terms" in value


def _looks_normalized_asset(value: Any) -> bool:
    return isinstance(value, Mapping) and "asset_id" in value and "search_text" in value


def _extract_collection(value: Any, keys: Sequence[str]) -> List[Any]:
    data = _to_plain_data(value)
    if data is None:
        return []
    if isinstance(data, Mapping):
        for key in keys:
            item = data.get(key)
            if item is None:
                continue
            if isinstance(item, Mapping):
                return list(item.values())
            if _is_sequence(item):
                return list(item)
        return []
    if _is_sequence(data):
        return list(data)
    return []


def _index_voiceovers(voiceovers: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}
    for item in voiceovers:
        scene_id = item.get("scene_id")
        if scene_id:
            indexed[str(scene_id)] = dict(item)
    return indexed


def _voiceover_for_scene(
    scene: Mapping[str, Any],
    index: int,
    by_scene: Mapping[str, Dict[str, Any]],
    voiceovers: Sequence[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    scene_id = str(scene.get("scene_id", ""))
    if scene_id and scene_id in by_scene:
        return dict(by_scene[scene_id])
    if index < len(voiceovers):
        return dict(voiceovers[index])
    return None


def _to_plain_mapping(value: Any) -> Dict[str, Any]:
    data = _to_plain_data(value)
    if isinstance(data, Mapping):
        return dict(data)
    return {}


def _to_plain_data(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {key: _to_plain_data(item) for key, item in value.items()}
    if is_dataclass(value):
        return _to_plain_data(asdict(value))
    if hasattr(value, "model_dump"):
        try:
            return _to_plain_data(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _to_plain_data(value.dict())
        except Exception:
            pass
    if _is_sequence(value):
        return [_to_plain_data(item) for item in value]
    return value


def _parse_json_maybe(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    candidates = [text]
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        candidates.append(text[first:last + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return value


def _first_value(data: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def _mapping_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return None


def _terms_from_values(values: Iterable[Any]) -> List[str]:
    terms: List[str] = []
    for value in values:
        terms.extend(_terms_from_value(value))
    return terms


def _terms_from_value(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        terms: List[str] = []
        for key, item in value.items():
            if isinstance(item, bool):
                if item:
                    terms.extend(_terms_from_value(key))
            else:
                terms.extend(_terms_from_value(item))
        return terms
    if _is_sequence(value):
        terms = []
        for item in value:
            terms.extend(_terms_from_value(item))
        return terms
    text = _string_or_empty(value).lower()
    if not text:
        return []

    chunks = re.split(r"[,，;；、/|｜\n\r\t]+", text)
    terms: List[str] = []
    for chunk in chunks:
        chunk = chunk.strip(" .:：()[]{}<>\"'`")
        if not chunk:
            continue
        if 1 < len(chunk) <= 40:
            terms.append(chunk)
        for token in re.findall(r"[a-z0-9_+-]{2,}", chunk):
            terms.append(token)
    return _dedupe_terms(terms)


def _dedupe_terms(terms: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for term in terms:
        normalized = _normalize_term(term)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _normalize_term(term: Any) -> str:
    text = _string_or_empty(term).lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .:：()[]{}<>\"'`")


def _coverage(terms: Sequence[str], candidate_terms: Iterable[str], candidate_text: str) -> float:
    normalized_terms = _dedupe_terms(terms)
    if not normalized_terms:
        return 0.0
    term_set = set(_dedupe_terms(candidate_terms))
    text = _string_or_empty(candidate_text).lower()
    hits = sum(1 for term in normalized_terms if _term_matches(term, term_set, text))
    return hits / len(normalized_terms)


def _term_matches(term: str, candidate_terms: Iterable[str], candidate_text: str) -> bool:
    normalized = _normalize_term(term)
    if not normalized:
        return False
    term_set = set(candidate_terms)
    if normalized in term_set:
        return True
    if len(normalized) > 1 and normalized in candidate_text:
        return True
    for candidate in term_set:
        if len(normalized) > 1 and normalized in candidate:
            return True
        if len(candidate) > 1 and candidate in normalized:
            return True
    return False


def _string_or_empty(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)
    return str(value).strip()


def _number_or_none(value: Any) -> Number:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _round_seconds(value: Any) -> Number:
    number = _number_or_none(value)
    if number is None:
        return None
    return round(number, 3)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


__all__ = [
    "AssemblyPlanner",
    "FFmpegAssemblyRenderer",
    "LabelMatch",
    "LabelMatcher",
    "normalize_asset",
    "normalize_scene",
    "normalize_voiceover",
    "schema_object_or_dict",
]
