from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import config
from production_schema import ProductionProject, load_json, save_json
from workflow_state import WorkflowState


def safe_project_name(value: str) -> str:
    text = str(value or "").strip() or "project"
    safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text).strip("_")
    return safe or "project"


class ProjectStore:
    """Persist and retrieve WorkflowState objects for the local API service."""

    def __init__(self, root_dir: Optional[str | Path] = None):
        self.root_dir = Path(root_dir or Path(config.OUTPUT_DIR) / "api_projects").expanduser()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.trash_dir = self.root_dir / ".trash"
        self._states: Dict[str, WorkflowState] = {}

    def create_project(
        self,
        name: str = "project",
        output_dir: Optional[str | Path] = None,
        project_id: Optional[str] = None,
    ) -> WorkflowState:
        project_id = project_id or f"project_{uuid.uuid4().hex[:8]}"
        base_dir = Path(output_dir).expanduser() if output_dir else self.root_dir
        project_dir = base_dir / f"{safe_project_name(name)}_{project_id[-8:]}.mimoproject"
        project = ProductionProject(
            project_id=project_id,
            name=name or "project",
            project_dir=str(project_dir),
        )
        state = WorkflowState(project=project, output_dir=str(base_dir))
        self._states[project_id] = state
        self.save_project(project_id)
        return state

    def load_project(self, path: str | Path) -> WorkflowState:
        state = WorkflowState.load_project(path)
        self._states[state.project.project_id] = state
        return state

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
        project_path = Path(state.project.project_dir).expanduser() / "project.json"
        return state.save_project(project_path)

    def update_project(self, project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        state = self.get_state(project_id)
        name = str(payload.get("name") or "").strip()
        if name:
            state.project.name = name
        state._touch()
        self.save_project(project_id)
        return self.to_payload(project_id)

    def delete_project(self, project_id: str) -> Dict[str, Any]:
        state = self.get_state(project_id)
        project_dir = Path(state.project.project_dir).expanduser()
        if not project_dir.exists():
            self._states.pop(project_id, None)
            return {"deleted": True, "project_id": project_id, "trash_path": ""}

        self.trash_dir.mkdir(parents=True, exist_ok=True)
        target = self.trash_dir / project_dir.name
        if target.exists():
            target = self.trash_dir / f"{project_dir.name}.{uuid.uuid4().hex[:8]}"
        shutil.move(str(project_dir), str(target))
        self._states.pop(project_id, None)
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
            "has_reference": bool(state.reference_text.strip()),
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
        for project_path in self.root_dir.glob("*.mimoproject/project.json"):
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
        for project_path in self.root_dir.glob("*.mimoproject/project.json"):
            try:
                data = load_json(project_path)
            except Exception:
                continue
            project_id = data.get("project_id")
            if project_id and project_id not in result:
                state = WorkflowState.from_dict(data)
                self._states[project_id] = state
                result[project_id] = self.project_status(project_id)
        return dict(
            sorted(
                result.items(),
                key=lambda item: item[1].get("updated_at") or "",
                reverse=True,
            )
        )
