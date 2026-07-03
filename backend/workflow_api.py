from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import config
from ai_rewriter import AIRewriter
from text_cleaner import TextCleaner
from title_generator import TitleGenerator

from .jobs import JobContext, JobManager
from .project_store import ProjectStore


class WorkflowBackend:
    """Application-facing API for local desktop clients.

    This layer is intentionally GUI-free. The existing Tkinter app can keep
    running as-is while the future Tauri/React app talks to this service.
    """

    def __init__(
        self,
        store: Optional[ProjectStore] = None,
        jobs: Optional[JobManager] = None,
        run_inline_jobs: bool = False,
    ):
        self.store = store or ProjectStore()
        self.jobs = jobs or JobManager(run_inline=run_inline_jobs)

    def create_project(self, payload: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        state = self.store.create_project(
            name=str(payload.get("name") or "project"),
            output_dir=payload.get("output_dir"),
            project_id=payload.get("project_id"),
        )
        return self.store.to_payload(state.project.project_id)

    def list_projects(self) -> Dict[str, Any]:
        return {"projects": list(self.store.list_projects().values())}

    def get_project(self, project_id: str) -> Dict[str, Any]:
        return self.store.to_payload(project_id)

    def get_status(self, project_id: str) -> Dict[str, Any]:
        return self.store.project_status(project_id)

    def update_project(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        return self.store.update_project(project_id, dict(payload))

    def delete_project(self, project_id: str) -> Dict[str, Any]:
        deleted = self.store.delete_project(project_id)
        return {**deleted, "projects": list(self.store.list_projects().values())}

    def create_activity(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        state = self.store.get_state(project_id)
        activity = state.create_activity(str(payload.get("name") or ""))
        self.store.save_project(project_id)
        return {
            "activity": activity.to_dict(),
            "status": self.store.project_status(project_id),
            "project": state.to_dict(),
        }

    def update_activity(self, project_id: str, activity_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        state = self.store.get_state(project_id)
        activity = state.get_activity(activity_id)
        if activity is None:
            raise KeyError(f"activity not found: {activity_id}")
        name = str(payload.get("name") or "").strip()
        if name:
            activity.name = name
        state._touch_activity(activity)
        state._touch()
        self.store.save_project(project_id)
        return {
            "activity": activity.to_dict(),
            "status": self.store.project_status(project_id),
            "project": state.to_dict(),
        }

    def select_activity(self, project_id: str, activity_id: str) -> Dict[str, Any]:
        state = self.store.get_state(project_id)
        activity = state.set_current_activity(activity_id)
        self.store.save_project(project_id)
        return {
            "activity": activity.to_dict(),
            "status": self.store.project_status(project_id),
            "project": state.to_dict(),
        }

    def set_reference_text(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        state = self.store.get_state(project_id)
        state.set_reference_text(
            payload.get("text", ""),
            title=payload.get("title", ""),
            product_name=payload.get("product_name", ""),
            keywords=payload.get("keywords") or [],
            source_path=payload.get("source_path", ""),
            metadata=payload.get("metadata") or {},
        )
        self.store.save_project(project_id)
        return {
            "reference_copy": state.project.reference_copy.to_dict() if state.project.reference_copy else None,
            "status": self.store.project_status(project_id),
        }

    def transcribe_reference(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        videos = _as_list(payload.get("videos") or payload.get("video"))
        model_size = str(payload.get("model_size") or "medium")
        output_dir = str(payload.get("output_dir") or self.store.get_state(project_id).project_dir)
        product_name = str(payload.get("product_name") or "")
        keywords = payload.get("keywords") or []

        def run(context: JobContext) -> Dict[str, Any]:
            transcripts = []
            total = max(len(videos), 1)
            for index, video_path in enumerate(videos, start=1):
                context.update(index / total * 85.0, f"transcribing {index}/{total}")
                result = transcribe_video(video_path, output_dir, model_size)
                transcripts.append(f"===== {Path(video_path).stem} =====\n{result['transcript']}")
                context.log(f"transcribed: {video_path}")
            merged = "\n\n".join(transcripts).strip()
            state = self.store.get_state(project_id)
            state.set_reference_text(
                merged,
                title=product_name or "参考文案",
                product_name=product_name,
                keywords=keywords,
                metadata={"videos": videos, "model_size": model_size},
            )
            self.store.save_project(project_id)
            return {
                "reference_text": merged,
                "status": self.store.project_status(project_id),
            }

        return {"job": self.jobs.submit("reference.transcribe", project_id, run).to_dict()}

    def analyze_storyboard(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        def run(context: JobContext) -> Dict[str, Any]:
            context.update(10, "analyzing storyboard")
            state = self.store.get_state(project_id)
            analysis = state.analyze_storyboard(
                target_duration_seconds=_optional_float(payload.get("target_duration_seconds")),
                shot_count=_optional_int(payload.get("shot_count")),
                model=payload.get("model") or config.API_MODEL,
            )
            self.store.save_project(project_id)
            return {
                "storyboard_analysis": analysis.to_dict(),
                "status": self.store.project_status(project_id),
            }

        return {"job": self.jobs.submit("storyboard.analyze", project_id, run).to_dict()}

    def tag_materials(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        material_paths = _as_list(payload.get("material_paths") or payload.get("videos") or payload.get("video"))
        material_dir = payload.get("material_dir")

        def run(context: JobContext) -> Dict[str, Any]:
            context.update(10, "tagging materials")
            state = self.store.get_state(project_id)
            assets = state.analyze_materials(
                material_paths=material_paths,
                material_dir=material_dir,
                output_dir=payload.get("output_dir"),
                vision_model=payload.get("vision_model") or config.VISION_API_MODEL,
                max_frames=payload.get("max_frames", 4),
                every_seconds=payload.get("every_seconds", 3),
            )
            self.store.save_project(project_id)
            return {
                "material_assets": [asset.to_dict() for asset in assets],
                "status": self.store.project_status(project_id),
            }

        return {"job": self.jobs.submit("materials.tag", project_id, run).to_dict()}

    def generate_voiceovers(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        def run(context: JobContext) -> Dict[str, Any]:
            context.update(10, "generating voiceovers")
            state = self.store.get_state(project_id)
            segments = state.generate_voiceovers(
                output_dir=payload.get("output_dir"),
                style_prompt=payload.get("style_prompt")
                or "Read naturally and clearly for short video narration.",
                voice=payload.get("voice"),
                audio_format=payload.get("audio_format", "mp3"),
            )
            self.store.save_project(project_id)
            return {
                "voiceover_segments": [segment.to_dict() for segment in segments],
                "status": self.store.project_status(project_id),
            }

        return {"job": self.jobs.submit("voiceovers.generate", project_id, run).to_dict()}

    def build_assembly_plan(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        def run(context: JobContext) -> Dict[str, Any]:
            context.update(10, "building assembly plan")
            state = self.store.get_state(project_id)
            plan = state.build_assembly_plan(
                output_path=payload.get("output_path"),
                overwrite=bool(payload.get("overwrite", False)),
                include_voiceover=bool(payload.get("include_voiceover", True)),
            )
            self.store.save_project(project_id)
            return {
                "assembly_plan": plan.to_dict(),
                "status": self.store.project_status(project_id),
            }

        return {"job": self.jobs.submit("assembly.plan", project_id, run).to_dict()}

    def legacy_generate(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        videos = _as_list(payload.get("videos") or payload.get("video"))
        output_dir = str(payload.get("output_dir") or self.store.get_state(project_id).project_dir)
        model_size = str(payload.get("model_size") or "medium")
        product_name = str(payload.get("product_name") or "")
        selling_points = str(payload.get("selling_points") or "")
        requirements = str(payload.get("requirements") or "")
        enable_cleaning = bool(payload.get("enable_cleaning", True))
        enable_titles = bool(payload.get("enable_titles", True))

        def run(context: JobContext) -> Dict[str, Any]:
            outputs = []
            total = max(len(videos), 1)
            for index, video_path in enumerate(videos, start=1):
                context.update(index / total * 80.0, f"processing {index}/{total}")
                transcript = transcribe_video(video_path, output_dir, model_size)["transcript"]
                work_text = TextCleaner().clean_text(transcript) if enable_cleaning else transcript
                prompt = build_rewrite_prompt(work_text, product_name, selling_points, requirements)
                rewrite = AIRewriter().rewrite_with_prompt(prompt)
                titles = TitleGenerator().generate_titles_from_transcript(work_text, count=5) if enable_titles else []
                outputs.append(
                    {
                        "video": video_path,
                        "transcript": transcript,
                        "rewrite": rewrite,
                        "titles": titles,
                    }
                )
            return {"outputs": outputs}

        return {"job": self.jobs.submit("legacy.generate", project_id, run).to_dict()}

    def agent_command(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        project_id = str(payload.get("project_id") or "")
        command = str(payload.get("command") or "").strip()
        actions = payload.get("actions") or []

        if command.lower() in {"status", "状态"} and project_id:
            return {
                "summary": "当前项目状态",
                "requires_confirmation": False,
                "actions": [],
                "status": self.get_status(project_id),
            }

        results = []
        for action in actions:
            results.append(self.dispatch_action(project_id, action))
        return {
            "summary": payload.get("summary") or "agent command accepted",
            "requires_confirmation": bool(payload.get("requires_confirmation", False)),
            "actions": actions,
            "results": results,
        }

    def dispatch_action(self, project_id: str, action: Mapping[str, Any]) -> Dict[str, Any]:
        action_type = action.get("type") or action.get("action")
        value = action.get("value")
        if action_type == "set_reference_text":
            return self.set_reference_text(project_id, {"text": value or action.get("text", "")})
        if action_type == "analyze_storyboard":
            return self.analyze_storyboard(project_id, action)
        if action_type == "tag_materials":
            return self.tag_materials(project_id, action)
        if action_type == "generate_voiceovers":
            return self.generate_voiceovers(project_id, action)
        if action_type == "build_assembly_plan":
            return self.build_assembly_plan(project_id, action)
        raise ValueError(f"unknown agent action: {action_type}")

    def get_job(self, job_id: str) -> Dict[str, Any]:
        return self.jobs.get(job_id).to_dict()


def transcribe_video(video_path: str, output_dir: str, model_size: str) -> Dict[str, Any]:
    Path(output_dir).expanduser().mkdir(parents=True, exist_ok=True)
    result_file = tempfile.NamedTemporaryFile(
        prefix="backend_transcription_",
        suffix=".json",
        dir=output_dir,
        delete=False,
    )
    result_path = result_file.name
    result_file.close()

    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "reference_transcription_cli.py"),
        "--video",
        str(video_path),
        "--output-dir",
        str(output_dir),
        "--model-size",
        str(model_size),
        "--language",
        "zh",
        "--result-json",
        result_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    payload: Dict[str, Any] = {}
    if os.path.exists(result_path):
        with open(result_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    try:
        os.remove(result_path)
    except OSError:
        pass

    if result.returncode != 0 or not payload.get("ok"):
        raise RuntimeError(payload.get("error") or f"transcription failed: {result.returncode}")
    return payload


def build_rewrite_prompt(
    transcript_text: str,
    product_name: str,
    selling_points: str,
    requirements: str,
) -> str:
    return f"""以下是一个视频内容转写文本：
{transcript_text}

请围绕以下产品信息改写为短视频口播文案：
产品名称: {product_name}
产品卖点: {selling_points}

要求：
{requirements}

请直接输出改写后的文案，不需要其他说明。"""


def _as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (str, Path)):
        return [str(value)]
    if isinstance(value, Iterable):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    return float(value)


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    return int(value)
