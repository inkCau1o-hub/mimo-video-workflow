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

from media_render import RenderCancelled, RenderError, _validated_edit, render_assembly
from shot_timing import shot_duration, validate_cue_bounds


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
            selected_id = (scene["raw"].get("metadata") or {}).get("material_asset_id")
            if selected_id not in (None, ""):
                selected_asset = next(
                    (asset for asset in assets if asset["asset_id"] == str(selected_id)), None
                )
                if selected_asset is None:
                    raise ValueError(
                        f"Shot {index + 1}: metadata.material_asset_id does not exist in material_assets."
                    )
                selected = self.matcher.score_scene_asset(scene, selected_asset)
            else:
                selected = matches[0] if matches and matches[0].score >= self.min_match_score else None
            alternatives = [match.to_dict() for match in matches]

            if not selected:
                warnings.append(f"No material asset matched scene {scene['scene_id']}.")

            video_clip = None
            if selected:
                video_clip = self._build_video_clip(scene, selected, start, end, duration)
                video_clips.append(video_clip)
                if scene["edit"] is not None:
                    available = selected.asset.get("duration")
                    if video_clip["source_out"] is not None:
                        available = video_clip["source_out"]
                    if available is not None and available - video_clip["source_in"] + 1e-6 < duration:
                        if scene["edit"]["shortage"] == "error":
                            raise ValueError(f"Shot {index + 1}: selected video is shorter than the target duration.")
                        warnings.append(
                            f"Shot {index + 1}: selected video is shorter than the target duration; its last frame will be frozen."
                        )

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
                "timeline_start": start if scene["edit"] is not None else _round_seconds(start),
                "timeline_end": end if scene["edit"] is not None else _round_seconds(end),
                "duration": duration if scene["edit"] is not None else _round_seconds(duration),
                "script_text": scene.get("text", ""),
                "tts_text": scene.get("tts_text", ""),
                "edit": scene["edit"],
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
                "duration": timeline_duration if any(s["edit"] is not None for s in scenes) else _round_seconds(timeline_duration),
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
        voiceover_duration = _number_or_none(voiceover.get("duration")) if voiceover else None
        edit = scene.get("edit")
        if edit is not None:
            if not voiceover or not voiceover.get("audio_path"):
                voiceover_duration = None
            if edit["duration_mode"] == "manual":
                duration = _number_or_none(scene["raw"].get("duration_seconds", scene.get("duration")))
                if duration is None or duration <= 0:
                    raise ValueError("Manual shot duration_seconds must be a positive finite number.")
                if voiceover_duration and duration + 1e-6 < voiceover_duration:
                    raise ValueError("Manual shot duration cannot be shorter than existing narration.")
                duration = shot_duration(edit, duration, voiceover_duration)
                validate_cue_bounds(edit["subtitle"], duration)
                return cursor, cursor + duration, duration
        if voiceover_duration is not None and voiceover_duration > 0:
            # Narration follows the rendered shot order, not stale storyboard timestamps.
            duration = shot_duration(edit, voiceover_duration, voiceover_duration) if edit is not None else voiceover_duration
            if edit is not None:
                validate_cue_bounds(edit["subtitle"], duration)
            return cursor, cursor + duration, duration

        explicit_start = scene.get("start")
        explicit_end = scene.get("end")
        duration = scene.get("duration")

        if duration is None and explicit_start is not None and explicit_end is not None:
            duration = max(0.0, float(explicit_end) - float(explicit_start))
        if duration is None or duration <= 0:
            duration = self.default_scene_duration

        if edit is not None:
            duration = shot_duration(edit, duration)
            validate_cue_bounds(edit["subtitle"], duration)
            return cursor, cursor + duration, duration

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
        edit = scene.get("edit")
        source_out = duration
        if asset_duration is not None:
            source_out = min(duration, max(0.0, float(asset_duration)))
        if edit is not None:
            source_out = edit["source_out"]
            if asset_duration is not None and (
                edit["source_in"] >= asset_duration or
                (source_out is not None and source_out > asset_duration + 1e-6)
            ):
                raise ValueError("The selected video range exceeds the known material duration.")

        return {
            "clip_id": f"video_{scene['scene_id']}",
            "track_id": "video_main",
            "kind": "video",
            "asset_id": match.asset_id,
            "source_path": match.asset_path,
            "source_in": edit["source_in"] if edit is not None else 0.0,
            "source_out": source_out if edit is not None else _round_seconds(source_out),
            "timeline_start": start if edit is not None else _round_seconds(start),
            "timeline_end": end if edit is not None else _round_seconds(end),
            "duration": duration if edit is not None else _round_seconds(duration),
            "fit": edit["fit"] if edit is not None else "cover",
            "crop_x": edit["crop_x"] if edit is not None else .5,
            "crop_y": edit["crop_y"] if edit is not None else .5,
            "shortage": edit["shortage"] if edit is not None else "freeze",
            "edit": edit,
            "script_text": scene.get("text", ""),
            "tts_text": scene.get("tts_text", ""),
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
        lead = (scene.get("edit") or {}).get("lead_in_seconds", 0)
        spoken = _number_or_none(voiceover.get("duration"))
        audio_duration = spoken if spoken and scene.get("edit") is not None else duration
        return {
            "clip_id": f"voiceover_{scene['scene_id']}",
            "track_id": "voiceover",
            "kind": "audio",
            "voiceover_id": voiceover["voiceover_id"],
            "source_path": voiceover["audio_path"],
            "source_in": 0.0,
            "source_out": audio_duration if scene["edit"] is not None else _round_seconds(duration),
            "timeline_start": start + lead if scene["edit"] is not None else _round_seconds(start),
            "timeline_end": start + lead + audio_duration if scene["edit"] is not None else _round_seconds(end),
            "duration": audio_duration if scene["edit"] is not None else _round_seconds(duration),
            "text": voiceover.get("text", ""),
        }


class FFmpegAssemblyRenderer:
    """Legacy command preview plus transactional multi-shot FFmpeg rendering.

    build_command/validate_plan and the default dry run retain their legacy
    single-clip preview semantics. Only render(dry_run=False) executes media work.
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

    def render(
        self,
        plan: Any,
        output_path: str,
        overwrite: bool = False,
        include_voiceover: bool = True,
        require_existing_inputs: bool = False,
        *,
        dry_run: bool = True,
        progress_callback: Any = None,
        cancel_callback: Any = None,
        cancel_check: Any = None,
        resolution: Optional[Tuple[int, int]] = None,
        aspect_ratio: Optional[str] = None,
        fps: Optional[int] = None,
        video_bitrate: str = "2500k",
        audio_bitrate: str = "128k",
        crf: int = 23,
        preset: str = "veryfast",
        subtitles: bool = True,
        font_path: Optional[str] = None,
        font_name: Optional[str] = None,
        ffprobe_path: Optional[str] = None,
        base_dir: Optional[str] = None,
        process_timeout: float = 600.0,
        audio_settings: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        """Render a planner dict or AssemblyPlan; no plan fields are mutated.

        dry_run=True returns the unchanged legacy argv preview (no I/O).
        dry_run=False returns a JSON-safe dict with output_path (absolute),
        duration_seconds (probed), planned_duration_seconds, width, height,
        fps, segment_count, size_bytes, and warnings. Outputs must be .mp4.
        Real rendering always validates every input, irrespective of the legacy
        require_existing_inputs flag. Relative paths use base_dir or cwd.

        progress_callback(float) receives monotonic 0..1 on this calling thread;
        1 means the final MP4 has been published. cancel_check() (or its alias
        cancel_callback()) returning
        truthy raises RenderCancelled, a RenderError subclass. Callbacks must
        be quick; callback failures become redacted RenderError. The final
        progress notification is best-effort after the commit point.

        Shots are hard-cut in list order (timeline gaps/overlaps and transition
        hints are not applied). Full measured narration from source_in sets
        each duration, rounded UP to a frame; manual duration uses the planned
        length and refuses to truncate measured narration. Stale audio source_out
        estimates are ignored. Without narration, use planned duration.
        Editor source ranges are validated against measured video duration.
        Short visuals freeze their last frame with a warning, or fail for
        shortage=error. Material audio is ALWAYS muted; missing narration produces
        a silent audio track. include_voiceover=False ignores narration files.

        audio_settings optionally enables two-pass per-clip loudness normalization,
        voice gain and local background music with fades, looping and sidechain
        ducking. Original audio files are never changed. Defaults preserve legacy
        sound; result metadata records the applied settings and measurements.

        Resolution defaults to plan timeline or 720x1280; aspect_ratio selects
        9:16 or 16:9 (720x1280/1280x720 when resolution is omitted). Explicit
        dimensions must be even; fps defaults to plan or 30. video_bitrate is
        the H.264 VBV ceiling, with crf controlling quality. Subtitles use libass
        and a discovered CJK font, or font_path + optional family font_name.
        Real rendering prefers system FFmpeg over bundled executables and
        checks the ass filter when burning subtitles. Dry runs keep ffmpeg_path.

        RenderError.code is safe to persist; messages never contain input paths,
        subtitle text, commands or FFmpeg stderr. Cancellation/failure terminates
        children and cleans staging files, leaving any old output untouched.
        overwrite=False refuses an existing destination (including commit races).
        """
        if dry_run:
            return self.build_command(
                plan, output_path, overwrite, include_voiceover, require_existing_inputs
            )
        if cancel_check is not None and cancel_callback is not None:
            raise RenderError("invalid_options", "Pass either cancel_check or cancel_callback, not both.")
        return render_assembly(
            _to_plain_data(plan), output_path, ffmpeg_path=self.ffmpeg_path,
            overwrite=overwrite, include_voiceover=include_voiceover,
            progress_callback=progress_callback,
            cancel_callback=cancel_check if cancel_check is not None else cancel_callback,
            resolution=resolution, aspect_ratio=aspect_ratio, fps=fps,
            video_bitrate=video_bitrate, audio_bitrate=audio_bitrate,
            crf=crf, preset=preset, subtitles=subtitles, font_path=font_path,
            font_name=font_name, ffprobe_path=ffprobe_path, base_dir=base_dir,
            process_timeout=process_timeout, audio_settings=audio_settings,
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
                "script_text": segment.get("script_text", ""),
                "tts_text": segment.get("tts_text", ""),
                "edit": segment.get("edit"),
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
    edit = None
    metadata = data.get("metadata") or {}
    if "edit" in metadata:
        try:
            edit = _validated_edit(metadata["edit"])
        except RenderError as exc:
            raise ValueError(str(exc)) from None
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
        "edit": edit,
        "tts_text": _string_or_empty(data.get("tts_text")),
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
    duration = None
    for key in ("duration_seconds", "duration"):
        candidate = _number_or_none(data.get(key))
        if candidate is not None and candidate > 0:
            duration = candidate
            break
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
        candidate = voiceovers[index]
        raw = candidate.get("raw") or {}
        # A missing shot's narration must not borrow a different shot's audio.
        if not (raw.get("scene_id") or raw.get("shot_id")):
            return dict(candidate)
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
    "RenderCancelled",
    "RenderError",
    "LabelMatch",
    "LabelMatcher",
    "normalize_asset",
    "normalize_scene",
    "normalize_voiceover",
    "schema_object_or_dict",
]
