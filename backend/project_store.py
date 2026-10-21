from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import config
from production_schema import ProductProfile, ProductionProject, load_json, save_json
from workflow_state import WorkflowState
from .editor_history import record_workflow_update


def safe_project_name(value: str) -> str:
    text = str(value or "").strip() or "project"
    safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text).strip("_")
    return safe or "project"


class ProjectStore:
    """Persist and retrieve WorkflowState objects for the local API service."""

    def __init__(self, root_dir: Optional[str | Path] = None):
        self.root_dir = Path(root_dir or Path(config.OUTPUT_DIR) / "api_projects").expanduser().resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.trash_dir = self.root_dir / ".trash"
        self._states: Dict[str, WorkflowState] = {}
        self.index_path = self.root_dir / ".project_index.json"
        self._project_files: Dict[str, str] = {}
        if self.index_path.is_file():
            index = load_json(self.index_path)
            self._project_files = {
                str(project_id): str(path)
                for project_id, path in index.get("projects", {}).items()
                if isinstance(path, str)
            }

    def create_project(
        self,
        name: str = "project",
        output_dir: Optional[str | Path] = None,
        project_id: Optional[str] = None,
        product_profile: Optional[Dict[str, Any]] = None,
    ) -> WorkflowState:
        project_id = project_id or f"project_{uuid.uuid4().hex[:8]}"
        base_dir = Path(output_dir).expanduser().resolve() if output_dir else self.root_dir
        project_dir = base_dir / f"{safe_project_name(name)}_{project_id[-8:]}.mimoproject"
        project = ProductionProject(
            project_id=project_id,
            name=name or "project",
            project_dir=str(project_dir),
            product_profile=_coerce_product_profile(product_profile),
        )
        state = WorkflowState(project=project, output_dir=str(base_dir))
        self._states[project_id] = state
        self.save_project(project_id)
        return state

    def load_project(self, path: str | Path) -> WorkflowState:
        project_path = Path(path).expanduser().resolve()
        if project_path.is_dir():
            project_path /= "project.json"
        state = WorkflowState.load_project(project_path)
        self._states[state.project.project_id] = state
        self._register_project(state.project.project_id, project_path)
        return state

    def open_project(self, path: str | Path) -> WorkflowState:
        return self.load_project(path)

    def _register_project(self, project_id: str, path: Path) -> None:
        try:
            location = path.resolve().relative_to(self.root_dir).as_posix()
        except ValueError:
            location = str(path.resolve())
        if self._project_files.get(project_id) != location:
            previous = dict(self._project_files)
            self._project_files[project_id] = location
            try:
                self._save_index()
            except Exception:
                self._project_files = previous
                raise

    def _save_index(self) -> None:
        save_json({"schema_version": 1, "projects": self._project_files}, self.index_path)

    def _indexed_path(self, project_id: str) -> Optional[Path]:
        location = self._project_files.get(project_id)
        if location is None:
            return None
        path = Path(location).expanduser()
        return path if path.is_absolute() else self.root_dir / path

    def _forget_project(self, project_id: str) -> None:
        self._states.pop(project_id, None)
        self._project_files.pop(project_id, None)
        self._save_index()

    def get_state(self, project_id: str) -> WorkflowState:
        if project_id in self._states:
            return self._states[project_id]

        project_path = self.find_project_file(project_id)
        if not project_path:
            raise KeyError(f"project not found: {project_id}")
        return self.load_project(project_path)

    def save_project(self, project_id: str) -> Dict[str, Any]:
        state = self.get_state(project_id) if project_id in self._states else None
        if state is None:
            raise KeyError(f"project not found: {project_id}")
        for activity in state.project.activities:
            record_workflow_update(activity)
        project_path = self._indexed_path(project_id) or Path(state.project.project_dir).expanduser() / "project.json"
        result = state.save_project(project_path)
        self._register_project(project_id, project_path)
        return result

    def update_project(self, project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        state = self.get_state(project_id)
        name = str(payload.get("name") or "").strip()
        if name:
            state.project.name = name
        if "product_profile" in payload:
            state.project.product_profile = _coerce_product_profile(payload.get("product_profile"))
        state._touch()
        self.save_project(project_id)
        return self.to_payload(project_id)

    def delete_project(self, project_id: str) -> Dict[str, Any]:
        state = self.get_state(project_id)
        project_dir = Path(state.project.project_dir).expanduser().resolve()
        if not project_dir.exists():
            self._forget_project(project_id)
            return {"deleted": True, "project_id": project_id, "trash_path": ""}

        # Opening an arbitrary external manifest does not grant ownership of its parent.
        if project_dir == self.root_dir or self.root_dir not in project_dir.parents:
            self._forget_project(project_id)
            return {"deleted": True, "project_id": project_id, "trash_path": "", "unlinked": True}

        self.trash_dir.mkdir(parents=True, exist_ok=True)
        target = self.trash_dir / project_dir.name
        if target.exists():
            target = self.trash_dir / f"{project_dir.name}.{uuid.uuid4().hex[:8]}"
        shutil.move(str(project_dir), str(target))
        self._forget_project(project_id)
        return {
            "deleted": True,
            "project_id": project_id,
            "trash_path": str(target),
        }

    def project_status(self, project_id: str) -> Dict[str, Any]:
        state = self.get_state(project_id)
        project = state.project
        activity = state.current_activity
        return {
            "project_id": project.project_id,
            "name": project.name,
            "project_dir": project.project_dir,
            "product_name": project.product_profile.product_name,
            "product_profile_complete": _product_profile_complete(project.product_profile),
            "has_reference": bool(state.reference_text.strip()),
            "has_copywriting": _has_copywriting(activity),
            "storyboard_shots": len(state.storyboard_analysis.shots) if state.storyboard_analysis else 0,
            "material_assets": len(state.material_assets),
            "voiceover_segments": len(state.voiceover_segments),
            "has_assembly_plan": state.assembly_plan is not None,
            "current_activity_id": activity.activity_id,
            "current_activity_name": activity.name,
            "activity_count": len(project.activities),
            "activity_status": dict(activity.status),
            "updated_at": project.updated_at,
        }

    def to_payload(self, project_id: str) -> Dict[str, Any]:
        state = self.get_state(project_id)
        return {
            "status": self.project_status(project_id),
            "project": state.to_dict(),
        }

    def find_project_file(self, project_id: str) -> Optional[Path]:
        indexed = self._indexed_path(project_id)
        paths = ([indexed] if indexed is not None else []) + list(self.root_dir.glob("*.mimoproject/project.json"))
        for project_path in paths:
            try:
                data = load_json(project_path)
            except Exception:
                continue
            if data.get("project_id") == project_id:
                return project_path
        return None

    def list_projects(self) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        for project_id in list(self._states):
            result[project_id] = self.project_status(project_id)
        paths = [self._indexed_path(project_id) for project_id in self._project_files]
        paths.extend(self.root_dir.glob("*.mimoproject/project.json"))
        for project_path in paths:
            try:
                data = load_json(project_path)
            except Exception:
                continue
            project_id = data.get("project_id")
            if project_id and project_id not in result:
                self.load_project(project_path)
                result[project_id] = self.project_status(project_id)
        return dict(
            sorted(
                result.items(),
                key=lambda item: item[1].get("updated_at") or "",
                reverse=True,
            )
        )


def _coerce_product_profile(value: Optional[Dict[str, Any]]) -> ProductProfile:
    if isinstance(value, ProductProfile):
        return value
    if isinstance(value, dict):
        return ProductProfile.from_dict(value)
    return ProductProfile()


def _product_profile_complete(profile: ProductProfile) -> bool:
    required = (
        profile.product_name,
        profile.features,
        profile.selling_points,
        profile.rewrite_requirements,
    )
    return all(str(item or "").strip() for item in required)


def _has_copywriting(activity: Any) -> bool:
    copywriting = getattr(activity, "copywriting", {})
    if not isinstance(copywriting, dict):
        return False
    rewrite = copywriting.get("rewrite")
    return isinstance(rewrite, dict) and bool(str(rewrite.get("text") or "").strip())
