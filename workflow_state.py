from __future__ import annotations

import inspect
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

import config
from material_catalog import MaterialCatalog
from media_assembler import AssemblyPlanner, FFmpegAssemblyRenderer, schema_object_or_dict
from multimodal_interfaces import FFMPEG_PATH
from production_schema import (
    NARRATION_MODES,
    AssemblyPlan,
    MaterialAsset,
    ProductionProject,
    ReferenceCopy,
    StoryboardAnalysis,
    StoryboardShot,
    VoiceoverSegment,
    WorkflowActivity,
    load_json,
    project_from_storage,
    project_to_storage,
    save_json,
)
from storyboard_analyzer import StoryboardAnalyzer
from tts_interfaces import MimoTTSClient


PathLike = Union[str, Path]


@dataclass
class WorkflowState:
    """Pure state layer for the GUI production workflow.

    The class keeps a single ProductionProject as the source of truth and
    delegates heavy work to the existing backend modules. GUI code can inject
    fake analyzer/catalog/TTS/planner objects for local tests without touching
    tkinter or real APIs.
    """

    project: ProductionProject = field(default_factory=ProductionProject)
    output_dir: Optional[PathLike] = None
    storyboard_analyzer: Optional[StoryboardAnalyzer] = field(default=None, repr=False)
    material_catalog: Optional[MaterialCatalog] = field(default=None, repr=False)
    tts_client: Optional[MimoTTSClient] = field(default=None, repr=False)
    assembly_planner: Optional[AssemblyPlanner] = field(default=None, repr=False)
    assembly_renderer: Optional[FFmpegAssemblyRenderer] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.project, Mapping):
            self.project = ProductionProject.from_dict(dict(self.project))

        if not self.project.project_id:
            self.project.project_id = f"project_{uuid.uuid4().hex[:8]}"
        if not self.project.name:
            self.project.name = "project"

        base_output = Path(self.output_dir or config.OUTPUT_DIR).expanduser().resolve()
        self.output_dir = str(base_output)
        if not self.project.project_dir:
            self.project.project_dir = str(base_output / _safe_name(self.project.name))
        self.ensure_current_activity()

    @property
    def project_dir(self) -> Path:
        return Path(self.project.project_dir).expanduser()

    @property
    def activity_dir(self) -> Path:
        return self.project_dir / "activities" / _safe_name(self.current_activity.activity_id)

    def new_voiceover_dir(self, output_dir: Optional[PathLike] = None) -> Path:
        """Allocate a fresh destination even when a caller supplies an output root."""
        base = Path(output_dir).expanduser() if output_dir else self.activity_dir / "voiceovers"
        return base / f"run_{uuid.uuid4().hex}"

    @property
    def current_activity(self) -> WorkflowActivity:
        return self.ensure_current_activity()

    @property
    def reference_text(self) -> str:
        reference = self.current_activity.reference_copy
        if not reference:
            return ""
        return reference.source_text

    @property
    def final_copy_text(self) -> str:
        rewrite = _copywriting_rewrite_text(self.current_activity)
        return rewrite or self.reference_text

    @property
    def storyboard_analysis(self) -> Optional[StoryboardAnalysis]:
        return self.current_activity.storyboard_analysis

    @property
    def material_assets(self) -> List[MaterialAsset]:
        return self.current_activity.material_assets

    @property
    def voiceover_segments(self) -> List[VoiceoverSegment]:
        return self.current_activity.voiceover_segments

    @property
    def assembly_plan(self) -> Optional[AssemblyPlan]:
        return self.current_activity.assembly_plan

    def ensure_current_activity(self) -> WorkflowActivity:
        if not self.project.activities:
            activity = self._activity_from_legacy()
            self.project.activities = [activity]
            self.project.current_activity_id = activity.activity_id

        activity = self.get_activity(self.project.current_activity_id)
        if activity is None:
            activity = self.project.activities[0]
            self.project.current_activity_id = activity.activity_id
        activity.status = _activity_status(activity)
        self._sync_legacy_from_activity(activity)
        return activity

    def get_activity(self, activity_id: str) -> Optional[WorkflowActivity]:
        for activity in self.project.activities:
            if activity.activity_id == activity_id:
                return activity
        return None

    def create_activity(self, name: str = "", make_current: bool = True) -> WorkflowActivity:
        activity = WorkflowActivity(
            activity_id=f"activity_{uuid.uuid4().hex[:8]}",
            name=name.strip() or f"活动 {len(self.project.activities) + 1}",
            status=_activity_status(),
        )
        self.project.activities.append(activity)
        if make_current:
            self.project.current_activity_id = activity.activity_id
            self._sync_legacy_from_activity(activity)
        self._touch_activity(activity)
        self._touch()
        return activity

    def set_current_activity(self, activity_id: str) -> WorkflowActivity:
        activity = self.get_activity(activity_id)
        if activity is None:
            raise KeyError(f"activity not found: {activity_id}")
        self.project.current_activity_id = activity.activity_id
        self._sync_legacy_from_activity(activity)
        self._touch()
        return activity

    def set_reference_videos(self, videos: Iterable[PathLike], *, activity_id: Optional[str] = None) -> WorkflowActivity:
        activity = self._target_activity(activity_id)
        normalized = [str(Path(path).expanduser()) for path in videos if str(path).strip()]
        if normalized != activity.reference_videos:
            activity.reference_copy = None
            activity.transcript = {}
            self._invalidate(activity, "reference")
        activity.reference_videos = normalized
        self._changed(activity)
        return activity

    def set_reference_text(
        self,
        reference_text: str,
        title: str = "",
        product_name: str = "",
        keywords: Optional[Sequence[str]] = None,
        source_path: str = "",
        language: str = "zh-CN",
        metadata: Optional[Mapping[str, Any]] = None,
        activity_id: Optional[str] = None,
    ) -> ReferenceCopy:
        reference = ReferenceCopy(
            copy_id=f"copy_{uuid.uuid4().hex[:8]}",
            title=title,
            source_text=str(reference_text or ""),
            source_path=source_path,
            product_name=product_name,
            keywords=[str(item) for item in (keywords or []) if str(item).strip()],
            language=language,
            metadata=dict(metadata or {}),
        )
        activity = self._target_activity(activity_id)
        previous = activity.reference_copy.to_dict() if activity.reference_copy else {}
        updated = reference.to_dict()
        for payload in (previous, updated):
            payload.pop("copy_id", None)
            payload.pop("created_at", None)
        if previous != updated:
            self._invalidate(activity, "reference")
        activity.reference_copy = reference
        activity.transcript = {
            "text": reference.source_text,
            "source": source_path,
            "created_at": reference.created_at,
            "title": title,
            "product_name": product_name,
            "keywords": [str(item) for item in (keywords or []) if str(item).strip()],
        }
        videos = _as_list((metadata or {}).get("videos") if isinstance(metadata, Mapping) else None)
        if videos:
            activity.reference_videos = videos
        self._changed(activity)
        return reference

    def set_copywriting(self, key: str, payload: Mapping[str, Any], *, activity_id: Optional[str] = None) -> WorkflowActivity:
        activity = self._target_activity(activity_id)
        previous_text = _copywriting_rewrite_text(activity)
        activity.copywriting = dict(activity.copywriting or {})
        activity.copywriting[str(key)] = dict(payload)
        if key == "rewrite" and previous_text != _copywriting_rewrite_text(activity):
            self._invalidate(activity, "rewrite")
        self._changed(activity)
        return activity

    def set_storyboard_shots(self, shots: Sequence[Any], *, activity_id: Optional[str] = None) -> StoryboardAnalysis:
        """Replace shots by stable ID; unchanged narration keeps existing audio."""
        activity = self._target_activity(activity_id)
        analysis = StoryboardAnalysis.from_dict(activity.storyboard_analysis.to_dict()) if activity.storyboard_analysis else StoryboardAnalysis()
        analysis.shots = [StoryboardShot.from_dict(item.to_dict() if isinstance(item, StoryboardShot) else dict(item)) for item in shots]
        return self.set_storyboard_analysis(analysis, activity_id=activity.activity_id)

    def set_storyboard_analysis(self, analysis: Any, *, activity_id: Optional[str] = None) -> StoryboardAnalysis:
        activity = self._target_activity(activity_id)
        analysis = StoryboardAnalysis.from_dict(analysis.to_dict() if isinstance(analysis, StoryboardAnalysis) else dict(analysis))
        seen = set()
        for index, shot in enumerate(analysis.shots, start=1):
            shot.shot_id = shot.shot_id or f"S{index:02d}"
            if shot.shot_id in seen:
                raise ValueError(f"duplicate shot_id: {shot.shot_id}")
            seen.add(shot.shot_id)
        previous = activity.storyboard_analysis
        old_narration = {
            shot.shot_id or f"S{index:02d}": shot.tts_text or shot.script_text
            for index, shot in enumerate(previous.shots if previous else [], start=1)
        }
        unchanged = {
            shot.shot_id for shot in analysis.shots
            if shot.shot_id in old_narration and old_narration[shot.shot_id] == (shot.tts_text or shot.script_text)
        }
        previous_voiceovers = [segment.to_dict() for segment in activity.voiceover_segments]
        if previous is None or previous.to_dict() != analysis.to_dict():
            activity.voiceover_segments = [segment for segment in activity.voiceover_segments if segment.shot_id in unchanged]
            self._invalidate(activity, "storyboard")
            analysis.metadata.pop("json_path", None)
            activity.metadata.pop("storyboard_path", None)
        activity.storyboard_analysis = analysis
        activity.voiceover_segments = _ordered_voiceovers(activity, activity.voiceover_segments)
        if previous_voiceovers != [segment.to_dict() for segment in activity.voiceover_segments]:
            activity.metadata.pop("voiceover_segments_path", None)
        self._changed(activity)
        return analysis

    def set_material_assets(self, assets: Iterable[Any], *, activity_id: Optional[str] = None) -> List[MaterialAsset]:
        activity = self._target_activity(activity_id)
        assets = [_coerce_material_asset(item) for item in assets]
        if [item.to_dict() for item in activity.material_assets] != [item.to_dict() for item in assets]:
            self._invalidate(activity, "materials")
        activity.material_assets = assets
        self._changed(activity)
        return assets

    def set_material_paths(self, paths: Iterable[PathLike], *, activity_id: Optional[str] = None) -> WorkflowActivity:
        """Update the uploaded, not necessarily analyzed, material queue."""
        activity = self._target_activity(activity_id)
        normalized = [str(Path(path).expanduser()) for path in paths if str(path).strip()]
        if normalized != activity.metadata.get("material_paths", []):
            self._invalidate(activity, "materials")
        activity.metadata["material_paths"] = normalized
        self._changed(activity)
        return activity

    def set_narration_mode(self, mode: str, *, activity_id: Optional[str] = None) -> WorkflowActivity:
        if mode not in NARRATION_MODES:
            raise ValueError("旁白模式只能是 voiceover（使用配音）或 none（无旁白）。")
        activity = self._target_activity(activity_id)
        if activity.narration_mode != mode:
            activity.narration_mode = mode
            self._invalidate(activity, "narration")
            self._changed(activity)
        return activity

    def set_voiceover_segments(self, segments: Iterable[Any], *, activity_id: Optional[str] = None) -> List[VoiceoverSegment]:
        activity = self._target_activity(activity_id)
        segments = [VoiceoverSegment.from_dict(item.to_dict() if isinstance(item, VoiceoverSegment) else dict(item)) for item in segments]
        segments = _ordered_voiceovers(activity, segments)
        if [item.to_dict() for item in activity.voiceover_segments] != [item.to_dict() for item in segments]:
            self._invalidate(activity, "voiceovers")
        activity.voiceover_segments = segments
        self._changed(activity)
        return segments

    def _target_activity(self, activity_id: Optional[str]) -> WorkflowActivity:
        if activity_id is None:
            return self.current_activity
        activity = self.get_activity(activity_id)
        if activity is None:
            raise KeyError(f"activity not found: {activity_id}")
        return activity

    def _invalidate(self, activity: WorkflowActivity, upstream: str) -> None:
        if upstream == "reference":
            activity.copywriting = {}
        if upstream in {"reference", "rewrite"}:
            activity.storyboard_analysis = None
            activity.voiceover_segments = []
            activity.metadata.pop("storyboard_path", None)
            activity.metadata.pop("voiceover_dir", None)
            activity.metadata.pop("voiceover_segments_path", None)
        activity.assembly_plan = None
        activity.metadata.pop("assembly_plan_path", None)
        activity.metadata.pop("render_result", None)

    def _changed(self, activity: WorkflowActivity) -> None:
        activity.status = _activity_status(activity)
        if activity.activity_id == self.project.current_activity_id:
            self._sync_legacy_from_activity(activity)
        self._touch_activity(activity)
        self._touch()

    def analyze_storyboard(
        self,
        target_duration_seconds: Optional[float] = None,
        shot_count: Optional[int] = None,
        analyzer: Optional[Any] = None,
        model: Optional[str] = None,
        save: bool = True,
        save_path: Optional[PathLike] = None,
        **kwargs: Any,
    ) -> StoryboardAnalysis:
        reference = self._require_reference_copy()
        worker = analyzer or self.storyboard_analyzer or StoryboardAnalyzer()

        result = _call_method(
            worker,
            ("analyze", "analyze_text"),
            reference,
            target_duration_seconds=target_duration_seconds,
            shot_count=shot_count,
            model=model,
            **kwargs,
        )
        analysis = self.set_storyboard_analysis(_coerce_storyboard_analysis(result, reference))
        activity = self.current_activity

        if save:
            path = Path(save_path).expanduser() if save_path else self.activity_dir / "storyboard.json"
            analysis.metadata = dict(analysis.metadata)
            analysis.metadata["json_path"] = str(path)
            self._save_artifact(analysis, path)
            activity.metadata["storyboard_path"] = str(path)

        self._changed(activity)
        return analysis

    def analyze_materials(
        self,
        material_paths: Optional[Iterable[PathLike]] = None,
        material_dir: Optional[PathLike] = None,
        recursive: bool = True,
        catalog: Optional[Any] = None,
        output_dir: Optional[PathLike] = None,
        save: bool = True,
        save_path: Optional[PathLike] = None,
        **kwargs: Any,
    ) -> List[MaterialAsset]:
        worker = catalog or self.material_catalog
        step_output_dir = Path(output_dir).expanduser() if output_dir else self.activity_dir / "materials"
        if worker is None:
            worker = MaterialCatalog(output_dir=str(step_output_dir))

        if material_dir is not None:
            result = _call_method(
                worker,
                ("analyze_directory",),
                str(Path(material_dir).expanduser()),
                recursive=recursive,
                output_dir=str(step_output_dir),
                as_schema=True,
                **kwargs,
            )
        else:
            paths = [str(Path(path).expanduser()) for path in (material_paths or [])]
            if not paths:
                raise ValueError("material_paths or material_dir is required")
            result = _call_method(
                worker,
                ("analyze_batch", "analyze_videos"),
                paths,
                output_dir=str(step_output_dir),
                as_schema=True,
                **kwargs,
            )

        assets = self.set_material_assets(result or [])
        activity = self.current_activity

        if save:
            path = Path(save_path).expanduser() if save_path else self.activity_dir / "material_catalog.json"
            payload = {
                "schema": "MaterialCatalog",
                "schema_version": 1,
                "assets": [asset.to_dict() for asset in assets],
            }
            self._save_artifact(payload, path)
            activity.metadata["material_catalog_path"] = str(path)

        self._changed(activity)
        return assets

    def generate_voiceovers(
        self,
        tts_client: Optional[Any] = None,
        output_dir: Optional[PathLike] = None,
        style_prompt: str = "Read naturally and clearly for short video narration.",
        voice: Optional[str] = None,
        audio_format: str = "mp3",
        save: bool = True,
        save_path: Optional[PathLike] = None,
        **kwargs: Any,
    ) -> List[VoiceoverSegment]:
        analysis = self._require_storyboard_analysis()
        worker = tts_client or self.tts_client or MimoTTSClient()
        voiceover_dir = self.new_voiceover_dir(output_dir)

        request_segments = []
        for index, shot in enumerate(analysis.shots, start=1):
            shot_id = shot.shot_id or f"S{index:02d}"
            request_segments.append(
                {
                    "segment_id": f"voiceover_{shot_id}",
                    "shot_id": shot_id,
                    "text": shot.tts_text or shot.script_text,
                    "duration_seconds": shot.duration_seconds,
                }
            )

        result = _call_method(
            worker,
            ("synthesize_segments",),
            request_segments,
            str(voiceover_dir),
            style_prompt=style_prompt,
            voice=voice,
            audio_format=audio_format,
            **kwargs,
        )

        by_segment_id = {
            str(item.get("segment_id", "")): item
            for item in result or []
            if isinstance(item, Mapping)
        }
        by_shot_id = {
            str(item.get("shot_id", "")): item
            for item in result or []
            if isinstance(item, Mapping) and item.get("shot_id")
        }

        cursor = 0.0
        voiceovers: List[VoiceoverSegment] = []
        generated_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        for index, shot in enumerate(analysis.shots, start=1):
            shot_id = shot.shot_id or f"S{index:02d}"
            segment_id = f"voiceover_{shot_id}"
            item = by_segment_id.get(segment_id) or by_shot_id.get(shot_id) or {}
            duration = _to_float(item.get("duration_seconds") or item.get("duration"), shot.duration_seconds or 0.0)
            audio_path = str(item.get("audio_path") or item.get("path") or "")
            if not audio_path:
                raise ValueError(f"TTS did not return an audio path for {shot_id}")
            metadata = {key: value for key, value in dict(item).items() if key not in {"segment_id", "shot_id"}}
            metadata["generated_at"] = generated_at
            voiceovers.append(
                VoiceoverSegment(
                    segment_id=segment_id,
                    shot_id=shot_id,
                    text=shot.tts_text or shot.script_text,
                    audio_path=audio_path,
                    start_seconds=round(cursor, 3),
                    duration_seconds=round(duration, 3),
                    voice=voice or getattr(worker, "voice", "") or "",
                    metadata=metadata,
                )
            )
            cursor += max(duration, 0.0)

        activity = self.current_activity
        if save:
            voiceover_dir.mkdir(parents=True, exist_ok=True)
            path = Path(save_path).expanduser() if save_path else voiceover_dir / "voiceover_segments.json"
            self._save_artifact({"segments": [segment.to_dict() for segment in voiceovers]}, path)
            activity.metadata["voiceover_segments_path"] = str(path)
        else:
            activity.metadata.pop("voiceover_segments_path", None)
        activity.metadata["voiceover_dir"] = str(voiceover_dir)

        return self.set_voiceover_segments(voiceovers)

    def generate_voiceover_segment(
        self,
        shot_id: str,
        tts_client: Optional[Any] = None,
        output_dir: Optional[PathLike] = None,
        style_prompt: str = "Read naturally and clearly for short video narration.",
        voice: Optional[str] = None,
        audio_format: str = "mp3",
        save: bool = True,
        save_path: Optional[PathLike] = None,
        **kwargs: Any,
    ) -> VoiceoverSegment:
        target_shot_id = str(shot_id or "").strip()
        if not target_shot_id:
            raise ValueError("shot_id is required")

        analysis = self._require_storyboard_analysis()
        indexed_shots = [
            ((shot.shot_id or f"S{index:02d}"), shot)
            for index, shot in enumerate(analysis.shots, start=1)
        ]
        shot = next((item for item_id, item in indexed_shots if item_id == target_shot_id), None)
        if shot is None:
            raise ValueError(f"shot not found: {target_shot_id}")

        worker = tts_client or self.tts_client or MimoTTSClient()
        voiceover_dir = self.new_voiceover_dir(output_dir)
        segment_id = f"voiceover_{target_shot_id}"
        request_segment = {
            "segment_id": segment_id,
            "shot_id": target_shot_id,
            "text": shot.tts_text or shot.script_text,
            "duration_seconds": shot.duration_seconds,
        }
        result = _call_method(
            worker,
            ("synthesize_segments",),
            [request_segment],
            str(voiceover_dir),
            style_prompt=style_prompt,
            voice=voice,
            audio_format=audio_format,
            **kwargs,
        )
        items = [item for item in result or [] if isinstance(item, Mapping)]
        item = next((entry for entry in items if entry.get("segment_id") == segment_id), None) or (
            next((entry for entry in items if entry.get("shot_id") == target_shot_id), None) if items else {}
        )
        audio_path = str((item or {}).get("audio_path") or (item or {}).get("path") or "")
        if not audio_path:
            raise ValueError(f"TTS did not return an audio path for {target_shot_id}")

        duration = _to_float((item or {}).get("duration_seconds") or (item or {}).get("duration"), shot.duration_seconds or 0.0)
        metadata = {
            key: value
            for key, value in dict(item or {}).items()
            if key not in {"segment_id", "shot_id"}
        }
        metadata["generated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        metadata["regenerated"] = True
        updated_segment = VoiceoverSegment(
            segment_id=segment_id,
            shot_id=target_shot_id,
            text=shot.tts_text or shot.script_text,
            audio_path=audio_path,
            duration_seconds=round(duration, 3),
            voice=voice or getattr(worker, "voice", "") or "",
            metadata=metadata,
        )

        activity = self.current_activity
        existing_by_shot = {
            segment.shot_id: VoiceoverSegment.from_dict(segment.to_dict())
            for segment in activity.voiceover_segments
            if segment.shot_id
        }
        existing_by_shot[target_shot_id] = updated_segment

        ordered_voiceovers = _ordered_voiceovers(activity, list(existing_by_shot.values()))

        if save:
            voiceover_dir.mkdir(parents=True, exist_ok=True)
            path = Path(save_path).expanduser() if save_path else voiceover_dir / "voiceover_segments.json"
            self._save_artifact({"segments": [segment.to_dict() for segment in ordered_voiceovers]}, path)
            activity.metadata["voiceover_segments_path"] = str(path)
        else:
            activity.metadata.pop("voiceover_segments_path", None)
        activity.metadata["voiceover_dir"] = str(voiceover_dir)

        segments = self.set_voiceover_segments(ordered_voiceovers)
        return next(segment for segment in segments if segment.shot_id == target_shot_id)

    def build_assembly_plan(
        self,
        planner: Optional[Any] = None,
        renderer: Optional[Any] = None,
        output_path: Optional[PathLike] = None,
        save: bool = True,
        save_path: Optional[PathLike] = None,
        overwrite: bool = False,
        include_voiceover: bool = True,
        **kwargs: Any,
    ) -> AssemblyPlan:
        analysis = self._require_storyboard_analysis()
        # Without narration the storyboard's own shot durations set the timing, not stale voiceover.
        include_voiceover = include_voiceover and self.current_activity.narration_mode != "none"
        output = Path(output_path).expanduser() if output_path else self.activity_dir / "final_video.mp4"
        planning_worker = planner or self.assembly_planner or AssemblyPlanner()
        rendering_worker = renderer or self.assembly_renderer or FFmpegAssemblyRenderer(ffmpeg_path=FFMPEG_PATH)

        raw_plan = _call_method(
            planning_worker,
            ("create_plan", "build_plan"),
            analysis,
            self.material_assets,
            self.voiceover_segments if include_voiceover else [],
            output_path=str(output),
            as_schema=True,
            **kwargs,
        )
        plan = _coerce_assembly_plan(raw_plan)

        validation = _call_method(
            rendering_worker,
            ("validate_plan",),
            raw_plan,
            str(output),
            require_existing_inputs=False,
        )
        renderer_metadata: Dict[str, Any] = {"validation": validation}
        if isinstance(validation, Mapping) and validation.get("ok"):
            renderer_metadata["command"] = _call_method(
                rendering_worker,
                ("build_command",),
                raw_plan,
                str(output),
                overwrite=overwrite,
                include_voiceover=include_voiceover,
                require_existing_inputs=False,
            )

        plan.output_path = str(output)
        plan.metadata = dict(plan.metadata)
        plan.metadata["renderer"] = renderer_metadata
        activity = self.current_activity
        activity.assembly_plan = plan
        activity.status = _activity_status(activity)
        self.project.assembly_plan = plan

        if save:
            path = Path(save_path).expanduser() if save_path else self.activity_dir / "assembly_plan.json"
            plan.metadata["json_path"] = str(path)
            self._save_artifact(plan, path)
            activity.metadata["assembly_plan_path"] = str(path)

        self._changed(activity)
        return plan

    def save_project(self, path: Optional[PathLike] = None) -> Dict[str, Any]:
        self.ensure_current_activity()
        self._touch()
        project_path = (Path(path).expanduser() if path else self.project_dir / "project.json").resolve()
        payload = project_to_storage(self.project, project_path.parent)
        save_json(payload, project_path)
        self.project.project_dir = str(project_path.parent)
        self.project.schema_version = 3
        return {"project_path": str(project_path), "project": self.project.to_dict()}

    def _save_artifact(self, data: Any, path: PathLike) -> None:
        # Artifact paths share the project manifest's relative-path base.
        payload = project_to_storage(data, self.project_dir)
        payload.pop("project_dir", None)
        payload.pop("schema_version", None)
        if isinstance(data, Mapping) and "schema_version" in data:
            payload["schema_version"] = data["schema_version"]
        save_json(payload, path)

    @classmethod
    def load_project(
        cls,
        path: PathLike,
        **dependencies: Any,
    ) -> "WorkflowState":
        project_path = Path(path).expanduser().resolve()
        if project_path.is_dir():
            project_path /= "project.json"
        data = load_json(project_path)
        project_data = data.get("project") if isinstance(data.get("project"), Mapping) else data
        resolved = project_from_storage(project_data, project_path.parent)
        state = cls.from_dict(resolved, **dependencies)
        portable = project_to_storage(state.project, project_path.parent)
        if project_data.get("schema_version", 1) != 3 or project_to_storage(resolved, project_path.parent) != project_data:
            backup = project_path.with_suffix(project_path.suffix + ".bak")
            original = project_path.read_bytes()
            created_backup = False
            try:
                with backup.open("xb") as stream:
                    created_backup = True
                    stream.write(original)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                pass
            except BaseException:
                if created_backup:
                    backup.unlink()
                raise
            save_json(portable, project_path)
        return state

    def to_dict(self) -> Dict[str, Any]:
        return self.project.to_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], **dependencies: Any) -> "WorkflowState":
        project_data = data.get("project") if isinstance(data.get("project"), Mapping) else data
        project = ProductionProject.from_dict(dict(project_data))
        output_dir = dependencies.pop("output_dir", None)
        if output_dir is None and project.project_dir:
            output_dir = str(Path(project.project_dir).expanduser().parent)
        return cls(project=project, output_dir=output_dir, **dependencies)

    def _require_reference_copy(self) -> ReferenceCopy:
        reference = self.current_activity.reference_copy
        if not reference:
            raise ValueError("reference_text is required")
        if not reference.source_text.strip():
            raise ValueError("reference_text is required")
        rewrite_text = _copywriting_rewrite_text(self.current_activity)
        if rewrite_text:
            metadata = dict(reference.metadata or {})
            metadata["copywriting"] = dict(self.current_activity.copywriting or {})
            return ReferenceCopy(
                copy_id=reference.copy_id,
                title="文案处理结果",
                source_text=rewrite_text,
                source_path=reference.source_path,
                product_name=reference.product_name,
                keywords=list(reference.keywords),
                language=reference.language,
                metadata=metadata,
            )
        return reference

    def _require_storyboard_analysis(self) -> StoryboardAnalysis:
        analysis = self.current_activity.storyboard_analysis
        if not analysis:
            raise ValueError("storyboard_analysis is required")
        return analysis

    def _touch(self) -> None:
        self.project.updated_at = _now_iso()

    def _touch_activity(self, activity: WorkflowActivity) -> None:
        activity.updated_at = _now_iso()

    def _activity_from_legacy(self) -> WorkflowActivity:
        reference = self.project.reference_copy
        transcript: Dict[str, Any] = {}
        if reference:
            transcript = {
                "text": reference.source_text,
                "source": reference.source_path,
                "created_at": reference.created_at,
                "title": reference.title,
                "product_name": reference.product_name,
                "keywords": list(reference.keywords),
            }
        activity = WorkflowActivity(
            activity_id=f"activity_{uuid.uuid4().hex[:8]}",
            name="默认活动",
            reference_videos=_as_list(reference.metadata.get("videos")) if reference else [],
            transcript=transcript,
            reference_copy=reference,
            copywriting={},
            storyboard_analysis=self.project.storyboard_analysis,
            material_assets=list(self.project.material_assets or []),
            voiceover_segments=list(self.project.voiceover_segments or []),
            assembly_plan=self.project.assembly_plan,
            metadata={key: self.project.metadata[key] for key in _ACTIVITY_PATH_KEYS if key in self.project.metadata},
        )
        activity.status = _activity_status(activity)
        return activity

    def _sync_legacy_from_activity(self, activity: WorkflowActivity) -> None:
        self.project.reference_copy = activity.reference_copy
        self.project.storyboard_analysis = activity.storyboard_analysis
        self.project.material_assets = list(activity.material_assets or [])
        self.project.voiceover_segments = list(activity.voiceover_segments or [])
        self.project.assembly_plan = activity.assembly_plan
        for key in _ACTIVITY_PATH_KEYS:
            if key in activity.metadata:
                self.project.metadata[key] = activity.metadata[key]
            else:
                self.project.metadata.pop(key, None)


_ACTIVITY_PATH_KEYS = ("storyboard_path", "material_catalog_path", "voiceover_dir", "voiceover_segments_path", "assembly_plan_path")


def _ordered_voiceovers(activity: WorkflowActivity, segments: Sequence[VoiceoverSegment]) -> List[VoiceoverSegment]:
    if activity.storyboard_analysis is None:
        return list(segments)
    by_shot = {segment.shot_id: segment for segment in segments}
    ordered = []
    cursor = 0.0
    for index, shot in enumerate(activity.storyboard_analysis.shots, start=1):
        edit = shot.metadata.get("edit") or {}
        lead = edit.get("lead_in_seconds", 0)
        tail = edit.get("tail_out_seconds", 0)
        segment = by_shot.get(shot.shot_id or f"S{index:02d}")
        if segment is not None:
            segment = VoiceoverSegment.from_dict(segment.to_dict())
            segment.start_seconds = round(cursor + lead, 3)
            if not segment.duration_seconds:
                segment.duration_seconds = shot.duration_seconds or 0.0
            ordered.append(segment)
        spoken_duration = (segment.duration_seconds if segment else shot.duration_seconds) or 0.0
        manual = edit.get("duration_mode") == "manual"
        required = spoken_duration + lead + tail if segment else spoken_duration
        cursor += max(required, shot.duration_seconds or 0.0, 0.0) if manual else max(required, 0.0)
    return ordered


def _call_method(obj: Any, names: Sequence[str], *args: Any, **kwargs: Any) -> Any:
    for name in names:
        method = getattr(obj, name, None)
        if method is None:
            continue
        return _call_with_supported_kwargs(method, *args, **kwargs)
    raise AttributeError(f"Object does not provide any of: {', '.join(names)}")


def _call_with_supported_kwargs(method: Any, *args: Any, **kwargs: Any) -> Any:
    signature = inspect.signature(method)
    parameters = signature.parameters
    accepts_kwargs = any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values())
    if accepts_kwargs:
        return method(*args, **kwargs)
    supported = {key: value for key, value in kwargs.items() if key in parameters}
    return method(*args, **supported)


def _coerce_storyboard_analysis(value: Any, reference: ReferenceCopy) -> StoryboardAnalysis:
    if isinstance(value, StoryboardAnalysis):
        if value.reference_copy is None:
            value.reference_copy = reference
        return value
    if isinstance(value, Mapping):
        data = dict(value)
        data.setdefault("reference_copy", reference.to_dict())
        return StoryboardAnalysis.from_dict(data)
    raise TypeError("storyboard analyzer must return StoryboardAnalysis or dict")


def _coerce_material_asset(value: Any) -> MaterialAsset:
    if isinstance(value, MaterialAsset):
        return value
    if isinstance(value, Mapping):
        data = dict(value)
        if "metadata" in data and "file_path" in data:
            return MaterialAsset.from_dict(data)
        schema_data = {
            "asset_id": data.get("asset_id", ""),
            "file_path": data.get("file_path") or data.get("path") or data.get("video_path") or "",
            "material_type": data.get("material_type") or data.get("type") or "",
            "scene_summary": data.get("scene_summary") or data.get("summary") or data.get("description") or "",
            "visual_tags": _dedupe(
                _as_list(data.get("visual_tags"))
                + _as_list(data.get("scene_tags"))
                + _as_list(data.get("tags"))
            ),
            "duration_seconds": _to_optional_float(data.get("duration_seconds") or data.get("duration")),
            "usable_shot_ids": _as_list(data.get("usable_shot_ids")),
            "thumbnail_path": data.get("thumbnail_path") or _first_item(data.get("frames")) or "",
            "analysis": data.get("analysis") if isinstance(data.get("analysis"), Mapping) else {},
            "metadata": data,
        }
        return MaterialAsset.from_dict(schema_data)
    raise TypeError("material catalog must return MaterialAsset or dict items")


def _coerce_assembly_plan(value: Any) -> AssemblyPlan:
    if isinstance(value, AssemblyPlan):
        return value
    if isinstance(value, Mapping):
        converted = schema_object_or_dict("AssemblyPlan", value)
        if isinstance(converted, AssemblyPlan):
            return converted
        if isinstance(converted, Mapping):
            return AssemblyPlan.from_dict(dict(converted))
    raise TypeError("assembly planner must return AssemblyPlan or dict")


def _safe_name(value: str) -> str:
    text = str(value or "").strip() or "project"
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in text)
    return safe.strip("_") or "project"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace(";", ",").replace("|", ",").split(",") if item.strip()]
    if isinstance(value, Mapping):
        return [str(key).strip() for key, item in value.items() if item and str(key).strip()]
    if isinstance(value, Iterable):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _dedupe(values: Iterable[str]) -> List[str]:
    result = []
    seen = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _first_item(value: Any) -> str:
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray, Mapping)):
        for item in value:
            return str(item)
    return ""


def _to_float(value: Any, default: float = 0.0) -> float:
    number = _to_optional_float(value)
    return default if number is None else number


def _to_optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _activity_status(activity: Optional[WorkflowActivity] = None) -> Dict[str, str]:
    if activity is None:
        return {
            "transcript": "pending",
            "copywriting": "pending",
            "storyboard": "pending",
            "materials": "pending",
            "voiceover": "pending",
            "assembly": "pending",
        }
    reference_text = ""
    if activity.reference_copy:
        reference_text = activity.reference_copy.source_text
    elif isinstance(activity.transcript, Mapping):
        reference_text = str(activity.transcript.get("text") or "")
    voiceover_status = "pending"
    if activity.narration_mode == "none":
        voiceover_status = "skipped"
    elif activity.voiceover_segments:
        voiced = {segment.shot_id for segment in activity.voiceover_segments if segment.audio_path}
        required = {shot.shot_id or f"S{index:02d}" for index, shot in enumerate(activity.storyboard_analysis.shots if activity.storyboard_analysis else [], start=1)}
        voiceover_status = "ready" if voiced and required.issubset(voiced) else "partial"
    return {
        "transcript": "ready" if reference_text.strip() else "pending",
        "copywriting": "ready" if _copywriting_rewrite_text(activity) else "pending",
        "storyboard": "ready" if activity.storyboard_analysis and activity.storyboard_analysis.shots else "pending",
        "materials": "ready" if activity.material_assets else "pending",
        "voiceover": voiceover_status,
        "assembly": "ready" if activity.assembly_plan else "pending",
    }


def _copywriting_rewrite_text(activity: Optional[WorkflowActivity]) -> str:
    if activity is None or not isinstance(activity.copywriting, Mapping):
        return ""
    rewrite = activity.copywriting.get("rewrite")
    if isinstance(rewrite, Mapping):
        return str(rewrite.get("text") or "").strip()
    return ""


__all__ = ["WorkflowState"]
