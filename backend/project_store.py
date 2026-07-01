from __future__ import annotations

import re
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

    def project_status(self, project_id: str) -> Dict[str, Any]:
        state = self.get_state(project_id)
        project = state.project
        return {
            "project_id": project.project_id,
            "name": project.name,
            "project_dir": project.project_dir,
            "has_reference": bool(state.reference_text.strip()),
            "storyboard_shots": len(project.storyboard_analysis.shots) if project.storyboard_analysis else 0,
            "material_assets": len(project.material_assets),
            "voiceover_segments": len(project.voiceover_segments),
            "has_assembly_plan": project.assembly_plan is not None,
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
                result[project_id] = {
                    "project_id": state.project.project_id,
                    "name": state.project.name,
                    "project_dir": state.project.project_dir,
                    "has_reference": bool(state.reference_text.strip()),
                    "storyboard_shots": len(state.project.storyboard_analysis.shots)
                    if state.project.storyboard_analysis
                    else 0,
                    "material_assets": len(state.project.material_assets),
                    "voiceover_segments": len(state.project.voiceover_segments),
                    "has_assembly_plan": state.project.assembly_plan is not None,
                    "updated_at": state.project.updated_at,
                }
        return result
