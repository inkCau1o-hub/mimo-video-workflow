from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import unquote, urlparse

import config
from ai_rewriter import AIRewriter
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
            product_profile=payload.get("product_profile") or {},
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

    def set_reference_videos(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        state = self.store.get_state(project_id)
        videos = _as_list(payload.get("videos") or payload.get("video"))
        validation = self.validate_reference_videos(project_id, {"videos": videos})
        normalized_videos = [
            item["resolved_path"] if item.get("exists") else item["input_path"]
            for item in validation["videos"]
        ]
        activity = state.set_reference_videos(normalized_videos)
        self.store.save_project(project_id)
        return {
            "activity": activity.to_dict(),
            "status": self.store.project_status(project_id),
            "project": state.to_dict(),
            "validation": validation,
        }

    def validate_reference_videos(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        state = self.store.get_state(project_id)
        videos = _as_list(payload.get("videos") or payload.get("video"))
        base_dirs = [state.project_dir, Path.cwd()]
        checked = [_resolve_video_path(path, base_dirs) for path in videos]
        return {
            "ok": bool(checked) and all(item["exists"] for item in checked),
            "videos": checked,
            "missing": [item for item in checked if not item["exists"]],
        }

    def upload_reference_videos(self, project_id: str, files: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
        state = self.store.get_state(project_id)
        upload_dir = state.project_dir / "media" / "reference"
        upload_dir.mkdir(parents=True, exist_ok=True)

        uploaded = []
        for file_info in files:
            original_filename = str(file_info.get("filename") or "").strip()
            filename = _safe_upload_filename(original_filename)
            if not _is_likely_video_path(filename):
                raise ValueError(f"不支持的视频文件类型: {original_filename or filename}")
            content = file_info.get("content") or b""
            if not isinstance(content, (bytes, bytearray)) or not content:
                raise ValueError(f"上传文件为空: {original_filename or filename}")

            target = _unique_upload_path(upload_dir / filename)
            target.write_bytes(bytes(content))
            uploaded.append(
                {
                    "original_filename": original_filename or filename,
                    "file_path": str(target.resolve()),
                    "size_bytes": target.stat().st_size,
                }
            )

        if not uploaded:
            raise ValueError("没有收到可导入的视频文件")

        existing = list(state.current_activity.reference_videos)
        next_videos = _unique_items([*existing, *[item["file_path"] for item in uploaded]])
        validation = self.validate_reference_videos(project_id, {"videos": next_videos})
        normalized_videos = [
            item["resolved_path"] if item.get("exists") else item["input_path"]
            for item in validation["videos"]
        ]
        activity = state.set_reference_videos(normalized_videos)
        self.store.save_project(project_id)
        return {
            "uploaded_videos": uploaded,
            "activity": activity.to_dict(),
            "status": self.store.project_status(project_id),
            "project": state.to_dict(),
            "validation": validation,
        }

    def set_reference_text(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        state = self.store.get_state(project_id)
        profile = state.project.product_profile
        state.set_reference_text(
            payload.get("text", ""),
            title=payload.get("title", ""),
            product_name=payload.get("product_name") or profile.product_name,
            keywords=payload.get("keywords") or _profile_keywords(profile),
            source_path=payload.get("source_path", ""),
            metadata={**(payload.get("metadata") or {}), "product_profile": profile.to_dict()},
        )
        self.store.save_project(project_id)
        return {
            "reference_copy": state.project.reference_copy.to_dict() if state.project.reference_copy else None,
            "status": self.store.project_status(project_id),
        }

    def rewrite_copy(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        def run(context: JobContext) -> Dict[str, Any]:
            context.update(10, "rewriting copy")
            state = self.store.get_state(project_id)
            reference_text = _current_reference_text(state)
            profile = state.project.product_profile
            prompt = build_product_rewrite_prompt(
                reference_text,
                profile.to_dict(),
                extra_requirements=str(payload.get("requirements") or ""),
                max_chars=_optional_int(payload.get("max_chars")) or 300,
            )
            rewritten = AIRewriter().rewrite_with_prompt(prompt).strip()
            activity = state.set_copywriting(
                "rewrite",
                {
                    "text": rewritten,
                    "source": "reference_transcript",
                    "source_chars": len(reference_text),
                    "product_profile": profile.to_dict(),
                    "created_at": _now_iso(),
                },
            )
            self.store.save_project(project_id)
            return {
                "copywriting": activity.copywriting,
                "status": self.store.project_status(project_id),
            }

        return {"job": self.jobs.submit("copy.rewrite", project_id, run).to_dict()}

    def generate_copy_titles(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        def run(context: JobContext) -> Dict[str, Any]:
            context.update(10, "generating titles")
            state = self.store.get_state(project_id)
            source_text = _copywriting_rewrite_text(state) or _current_reference_text(state)
            profile = state.project.product_profile
            count = _optional_int(payload.get("count")) or 5
            prompt = build_product_titles_prompt(source_text, profile.to_dict(), count=count)
            raw = AIRewriter().rewrite_with_prompt(prompt).strip()
            titles = _parse_generated_titles(raw)
            activity = state.set_copywriting(
                "titles",
                {
                    "items": titles,
                    "raw_response": raw,
                    "source": "rewritten_copy" if _copywriting_rewrite_text(state) else "reference_transcript",
                    "source_chars": len(source_text),
                    "product_profile": profile.to_dict(),
                    "created_at": _now_iso(),
                },
            )
            self.store.save_project(project_id)
            return {
                "copywriting": activity.copywriting,
                "status": self.store.project_status(project_id),
            }

        return {"job": self.jobs.submit("copy.titles", project_id, run).to_dict()}

    def transcribe_reference(self, project_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        videos = _as_list(payload.get("videos") or payload.get("video"))
        validation = self.validate_reference_videos(project_id, {"videos": videos})
        if not validation["ok"]:
            missing_names = ", ".join(item["input_path"] for item in validation["missing"]) or "空视频队列"
            raise ValueError(f"视频文件不存在或路径不完整: {missing_names}")
        videos = [item["resolved_path"] for item in validation["videos"]]
        model_size = str(payload.get("model_size") or "medium")
        output_dir = str(payload.get("output_dir") or self.store.get_state(project_id).project_dir)
        profile = self.store.get_state(project_id).project.product_profile
        product_name = str(payload.get("product_name") or profile.product_name)
        keywords = payload.get("keywords") or _profile_keywords(profile)

        def run(context: JobContext) -> Dict[str, Any]:
            transcripts = []
            total = max(len(videos), 1)
            for index, video_path in enumerate(videos, start=1):
                context.raise_if_cancelled()
                context.update(index / total * 85.0, f"transcribing {index}/{total}")
                result = transcribe_video(video_path, output_dir, model_size)
                context.raise_if_cancelled()
                transcripts.append(f"===== {Path(video_path).stem} =====\n{result['transcript']}")
                context.log(f"transcribed: {video_path}")
            merged = "\n\n".join(transcripts).strip()
            state = self.store.get_state(project_id)
            state.set_reference_text(
                merged,
                title=product_name or "参考文案",
                product_name=product_name,
                keywords=keywords,
                metadata={
                    "videos": videos,
                    "model_size": model_size,
                    "product_profile": profile.to_dict(),
                },
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
        state = self.store.get_state(project_id)
        profile = state.project.product_profile
        output_dir = str(payload.get("output_dir") or state.project_dir)
        model_size = str(payload.get("model_size") or "medium")
        product_name = str(payload.get("product_name") or profile.product_name)
        selling_points = str(payload.get("selling_points") or profile.selling_points)
        requirements = str(payload.get("requirements") or profile.rewrite_requirements)
        enable_titles = bool(payload.get("enable_titles", True))

        def run(context: JobContext) -> Dict[str, Any]:
            outputs = []
            total = max(len(videos), 1)
            for index, video_path in enumerate(videos, start=1):
                context.raise_if_cancelled()
                context.update(index / total * 80.0, f"processing {index}/{total}")
                transcript = transcribe_video(video_path, output_dir, model_size)["transcript"]
                context.raise_if_cancelled()
                work_text = transcript
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
        normalized = command.lower().strip()

        if normalized in {"help", "帮助", "命令"}:
            return {
                "summary": "可用命令: 创建项目 名称、删除项目、确认删除项目、创建活动 名称、切换活动 名称或ID、状态、当前活动、转写当前活动、文案改写、标题生成、生成分镜、生成配音、生成合成计划。",
                "requires_confirmation": False,
                "actions": [],
            }

        if normalized.startswith("创建项目"):
            name = command.replace("创建项目", "", 1).strip() or "MiMo Prototype Project"
            created = self.create_project({"name": name})
            return {
                "summary": f"已创建项目: {created['status']['name']}",
                "requires_confirmation": False,
                "actions": [{"type": "create_project", "name": name}],
                **created,
                "projects": self.list_projects()["projects"],
            }

        if not project_id:
            return {
                "summary": "请先创建或打开项目，再执行这个命令。",
                "requires_confirmation": False,
                "actions": [],
            }

        if normalized in {"status", "状态", "查看状态", "查看当前活动状态", "当前活动"}:
            status = self.get_status(project_id)
            project = self.get_project(project_id)["project"]
            activity = _current_activity(project)
            return {
                "summary": _status_summary(status, activity),
                "requires_confirmation": False,
                "actions": [],
                "status": status,
                "activity": activity,
            }

        if normalized.startswith("删除项目") and not normalized.startswith("确认删除项目"):
            return {
                "summary": "删除项目会把当前项目移入 .trash。输入“确认删除项目”执行删除。",
                "requires_confirmation": True,
                "actions": [{"type": "delete_project", "project_id": project_id}],
                "status": self.get_status(project_id),
            }

        if normalized.startswith("确认删除项目"):
            deleted = self.delete_project(project_id)
            return {
                "summary": f"已删除项目 {project_id}，项目已移入回收站。",
                "requires_confirmation": False,
                "actions": [{"type": "delete_project", "project_id": project_id}],
                **deleted,
            }

        if normalized.startswith("创建活动"):
            name = command.replace("创建活动", "", 1).strip() or "新活动"
            created = self.create_activity(project_id, {"name": name})
            return {
                "summary": f"已创建并切换到活动: {created['activity']['name']}",
                "requires_confirmation": False,
                "actions": [{"type": "create_activity", "name": name}],
                **created,
            }

        if normalized.startswith("切换活动"):
            target = command.replace("切换活动", "", 1).strip()
            if not target:
                raise ValueError("切换活动需要活动名称或 activity_id")
            project = self.get_project(project_id)["project"]
            activity = _find_activity(project, target)
            if activity is None:
                raise KeyError(f"activity not found: {target}")
            selected = self.select_activity(project_id, activity["activity_id"])
            return {
                "summary": f"已切换到活动: {selected['activity']['name']}",
                "requires_confirmation": False,
                "actions": [{"type": "select_activity", "activity_id": activity["activity_id"]}],
                **selected,
            }

        if normalized in {"转写当前活动", "转写参考", "参考转写"}:
            project = self.get_project(project_id)["project"]
            activity = _current_activity(project)
            videos = activity.get("reference_videos") or []
            if not videos:
                return {
                    "summary": "当前活动还没有参考视频。请先在参考转写页加入视频队列。",
                    "requires_confirmation": False,
                    "actions": [],
                    "status": self.get_status(project_id),
                }
            result = self.transcribe_reference(project_id, {"videos": videos, "model_size": "medium"})
            return {
                "summary": f"已启动参考转写，共 {len(videos)} 个视频。",
                "requires_confirmation": False,
                "actions": [{"type": "transcribe_reference", "videos": videos}],
                **result,
            }

        if normalized in {"文案改写", "改写文案", "生成文案"}:
            result = self.rewrite_copy(project_id, {})
            return {
                "summary": "已启动文案改写。",
                "requires_confirmation": False,
                "actions": [{"type": "rewrite_copy"}],
                **result,
            }

        if normalized in {"标题生成", "生成标题"}:
            result = self.generate_copy_titles(project_id, {})
            return {
                "summary": "已启动标题生成。",
                "requires_confirmation": False,
                "actions": [{"type": "generate_copy_titles"}],
                **result,
            }

        if normalized in {"生成分镜", "分析分镜"}:
            result = self.analyze_storyboard(project_id, {})
            return {
                "summary": "已启动分镜分析。",
                "requires_confirmation": False,
                "actions": [{"type": "analyze_storyboard"}],
                **result,
            }

        if normalized in {"生成配音", "配音"}:
            result = self.generate_voiceovers(project_id, {})
            return {
                "summary": "已启动配音生成。",
                "requires_confirmation": False,
                "actions": [{"type": "generate_voiceovers"}],
                **result,
            }

        if normalized in {"生成合成计划", "合成计划", "生成合成"}:
            result = self.build_assembly_plan(project_id, {})
            return {
                "summary": "已启动合成计划生成。",
                "requires_confirmation": False,
                "actions": [{"type": "build_assembly_plan"}],
                **result,
            }

        results = []
        for action in actions:
            results.append(self.dispatch_action(project_id, action))
        return {
            "summary": payload.get("summary") or "暂未识别这个命令。输入“帮助”查看可用命令。",
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

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        return self.jobs.cancel(job_id).to_dict()


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


def build_product_rewrite_prompt(
    transcript_text: str,
    product_profile: Mapping[str, Any],
    extra_requirements: str = "",
    max_chars: int = 300,
) -> str:
    requirements = str(product_profile.get("rewrite_requirements") or "").strip()
    if extra_requirements.strip():
        requirements = f"{requirements}\n{extra_requirements.strip()}".strip()
    return f"""你是短视频电商文案改写助手。请根据产品资料，把参考转写改写成可直接用于短视频口播的成稿。

产品资料:
{_format_product_profile(product_profile)}

改写要求:
{requirements or "保留核心信息，口语化，适合短视频口播。"}

参考转写:
{transcript_text}

输出要求:
- 只输出改写后的口播文案，不要解释。
- 控制在{max_chars}字以内。
- 保留参考转写中的核心事实，但表达要更清楚、更像真实口播。
- 不要使用产品资料中禁用或夸大的表达。"""


def build_product_titles_prompt(copy_text: str, product_profile: Mapping[str, Any], count: int = 5) -> str:
    return f"""你是短视频标题策划助手。请基于成稿文案和产品资料生成{count}个标题。

产品资料:
{_format_product_profile(product_profile)}

成稿文案:
{copy_text}

输出要求:
- 每行一个标题。
- 每个标题尽量控制在20字以内。
- 标题要体现核心卖点，适合短视频平台。
- 不要解释，不要编号以外的额外说明。"""


def _as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (str, Path)):
        return [str(value)]
    if isinstance(value, Iterable):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _current_reference_text(state: Any) -> str:
    text = str(getattr(state, "reference_text", "") or "").strip()
    if not text:
        raise ValueError("当前活动还没有参考转写，请先完成参考转写。")
    return text


def _copywriting_rewrite_text(state: Any) -> str:
    activity = getattr(state, "current_activity", None)
    copywriting = getattr(activity, "copywriting", {}) if activity else {}
    if not isinstance(copywriting, Mapping):
        return ""
    rewrite = copywriting.get("rewrite")
    if isinstance(rewrite, Mapping):
        return str(rewrite.get("text") or "").strip()
    return ""


def _format_product_profile(profile: Mapping[str, Any]) -> str:
    labels = [
        ("product_name", "产品名称"),
        ("brand_name", "品牌/店铺"),
        ("features", "产品特点"),
        ("selling_points", "核心卖点"),
        ("target_audience", "目标人群"),
        ("scenarios", "使用场景"),
        ("promotion", "价格/活动"),
        ("rewrite_requirements", "转写/改写要求"),
        ("tone", "文案风格"),
        ("forbidden_claims", "禁用表达"),
    ]
    lines = []
    for key, label in labels:
        value = str(profile.get(key) or "").strip()
        if value:
            lines.append(f"- {label}: {value}")
    return "\n".join(lines) if lines else "无"


def _parse_generated_titles(raw: str) -> List[str]:
    titles = []
    for line in str(raw or "").splitlines():
        text = line.strip()
        text = text.lstrip("-•* \t")
        text = text.lstrip("0123456789.、）) ")
        text = text.strip("「」“”\"'")
        if text:
            titles.append(text)
    return titles[:12]


def _unique_items(items: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _profile_keywords(profile: Any) -> List[str]:
    raw_items = [
        getattr(profile, "features", ""),
        getattr(profile, "selling_points", ""),
        getattr(profile, "target_audience", ""),
        getattr(profile, "scenarios", ""),
        getattr(profile, "tone", ""),
    ]
    keywords: List[str] = []
    seen = set()
    for raw in raw_items:
        for item in str(raw or "").replace("，", ",").replace("、", ",").split(","):
            text = item.strip()
            if text and text not in seen:
                seen.add(text)
                keywords.append(text)
    return keywords[:12]


def _path_from_reference(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("file://"):
        parsed = urlparse(text)
        return unquote(parsed.path)
    return text


def _resolve_video_path(value: Any, base_dirs: Iterable[Path | str]) -> Dict[str, Any]:
    input_path = str(value or "").strip()
    normalized = _path_from_reference(input_path)
    if not normalized:
        return {
            "input_path": input_path,
            "resolved_path": "",
            "exists": False,
            "reason": "路径为空",
        }

    raw_path = Path(normalized).expanduser()
    candidates: List[Path] = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        for base_dir in base_dirs:
            if base_dir:
                candidates.append(Path(base_dir).expanduser() / raw_path)

    seen = set()
    unique_candidates = []
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)

    for candidate in unique_candidates:
        if candidate.exists() and candidate.is_file():
            return {
                "input_path": input_path,
                "resolved_path": str(candidate.resolve()),
                "exists": True,
                "reason": "",
            }

    resolved_path = str(unique_candidates[0]) if unique_candidates else str(raw_path)
    return {
        "input_path": input_path,
        "resolved_path": resolved_path,
        "exists": False,
        "reason": "文件不存在，请使用完整路径或重新选择文件",
    }


def _is_likely_video_path(path: Any) -> bool:
    return Path(str(path or "")).suffix.lower() in {
        ".mp4",
        ".mov",
        ".m4v",
        ".avi",
        ".mkv",
        ".webm",
    }


def _safe_upload_filename(value: Any) -> str:
    raw = str(value or "").replace("\x00", "").replace("\\", "/")
    name = raw.split("/")[-1].strip() or f"reference-video-{uuid.uuid4().hex[:8]}.mp4"
    safe = "".join("_" if char in '<>:"|?*' or ord(char) < 32 else char for char in name)
    safe = safe.strip(" .") or f"reference-video-{uuid.uuid4().hex[:8]}.mp4"
    if len(safe) <= 180:
        return safe
    suffix = Path(safe).suffix
    stem_limit = max(24, 180 - len(suffix))
    return f"{Path(safe).stem[:stem_limit]}{suffix}"


def _unique_upload_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem or "reference-video"
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}_{uuid.uuid4().hex[:8]}{suffix}")


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    return float(value)


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    return int(value)


def _current_activity(project: Mapping[str, Any]) -> Dict[str, Any]:
    activities = project.get("activities") or []
    current_id = project.get("current_activity_id") or ""
    for activity in activities:
        if isinstance(activity, Mapping) and activity.get("activity_id") == current_id:
            return dict(activity)
    if activities and isinstance(activities[0], Mapping):
        return dict(activities[0])
    return {}


def _find_activity(project: Mapping[str, Any], target: str) -> Optional[Dict[str, Any]]:
    needle = str(target or "").strip()
    for activity in project.get("activities") or []:
        if not isinstance(activity, Mapping):
            continue
        if activity.get("activity_id") == needle or activity.get("name") == needle:
            return dict(activity)
    return None


def _status_summary(status: Mapping[str, Any], activity: Mapping[str, Any]) -> str:
    activity_name = activity.get("name") or status.get("current_activity_name") or "未选择"
    return (
        f"当前项目: {status.get('name', '未命名项目')}\n"
        f"当前活动: {activity_name}\n"
        f"参考文案: {'已就绪' if status.get('has_reference') else '未设置'}\n"
        f"分镜: {status.get('storyboard_shots') or 0} 条\n"
        f"素材: {status.get('material_assets') or 0} 个\n"
        f"配音: {status.get('voiceover_segments') or 0} 条\n"
        f"合成计划: {'已生成' if status.get('has_assembly_plan') else '未生成'}"
    )
