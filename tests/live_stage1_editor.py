"""Opt-in phase-one acceptance using real source media and one real TTS request."""

import argparse
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--base-project", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, default=ROOT.parent / "mimo密钥.txt")
    args = parser.parse_args()
    if not args.live:
        parser.error("Use --live to authorize a real TTS call")
    lines = [line.strip() for line in args.key_file.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    keys = [line for line in lines if not line.startswith(("http://", "https://"))]
    urls = [line for line in lines if line.startswith("https://")]
    if len(keys) != 1 or not keys[0].isascii() or any(char.isspace() for char in keys[0]):
        raise ValueError("Invalid credential file")
    os.environ["API_KEY"] = keys[0]
    if urls:
        os.environ["API_BASE_URL"] = urls[0]
    del keys, lines

    from backend.project_store import ProjectStore
    from backend.security import redact
    from backend.workflow_api import WorkflowBackend
    from production_schema import save_json
    from workflow_state import WorkflowState

    base = args.base_project.resolve()
    if base.is_file():
        base = base.parent
    project_id = "project_" + uuid.uuid4().hex[:8]
    name = "阶段一实战_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    root = ROOT / "output" / "acceptance"
    target = root / f"{name}_{project_id[-8:]}.mimoproject"
    shutil.copytree(base, target)
    initial = WorkflowState.load_project(target / "project.json")
    initial.project.project_id = project_id
    initial.project.name = name
    initial.current_activity.metadata.pop("edit_history", None)
    initial.save_project(target / "project.json")
    backend = WorkflowBackend(ProjectStore(root), run_inline_jobs=True)
    backend.open_project({"path": str(target)})
    state = backend.store.get_state(project_id)
    aid = state.current_activity.activity_id
    report_path = target / "stage1_report.json"
    report = {"project_id": project_id, "started_at": datetime.now().isoformat(),
              "real_api": True, "stages": [], "outputs": []}
    print("PROJECT=" + str(target / "project.json"), flush=True)

    def stage(name, action):
        started = time.monotonic()
        try:
            value = action()
            job = value.get("job") if isinstance(value, dict) else None
            if job and job["status"] != "succeeded":
                raise RuntimeError(job.get("error") or job["status"])
            report["stages"].append({"name": name, "ok": True, "seconds": round(time.monotonic() - started, 3)})
            save_json(report, report_path)
            print("PASS=" + name, flush=True)
            return job["result"] if job else value
        except Exception as exc:
            report["stages"].append({"name": name, "ok": False, "error": redact(str(exc))})
            save_json(report, report_path)
            print("FAIL=" + name + ": " + redact(str(exc)), flush=True)
            raise SystemExit(1)

    originals = [shot.to_dict() for shot in state.storyboard_analysis.shots]
    assert len(originals) >= 3 and len(state.voiceover_segments) >= 3 and len(state.material_assets) >= 2
    original_audio = {voice.shot_id: voice.audio_path for voice in state.voiceover_segments}
    original_exports = [Path(item["output_path"]) for item in state.current_activity.metadata.get("exports", [])]
    shots = []
    for index in range(8):
        source = originals[index % 3]
        shot = deepcopy(source)
        if index >= 3:
            shot["shot_id"] = f"S{index + 1:02d}"
            shot["metadata"]["duplicate_of"] = source["shot_id"]
        asset = state.material_assets[index % len(state.material_assets)]
        shot["metadata"]["material_asset_id"] = asset.asset_id
        shot["duration_seconds"] = 8 if index == 0 else 4.5
        shot["metadata"]["edit"] = {
            "source_in": 0.5 + index % 3, "source_out": 3.5 + index % 3,
            "fit": "contain" if index == 1 else "cover", "crop_x": 0.2 if index % 2 else 0.8,
            "crop_y": 0.5, "duration_mode": "manual", "shortage": "freeze",
            "subtitle": {"text": source["tts_text"] + ("\n实景展示" if index == 0 else ""),
                         "position": "top" if index == 1 else "bottom", "font_size": 36,
                         "color": "#FFE766" if index == 0 else "#FFFFFF", "background": index == 0},
        }
        shots.append(shot)
    stage("save_eight_selected_clips_and_subtitles", lambda: backend.set_storyboard_shots(project_id, {"shots": shots, "activity_id": aid}))
    assert len(state.voiceover_segments) == 8
    assert state.voiceover_segments[3].audio_path == original_audio[originals[0]["shot_id"]]
    reordered = [shots[1], shots[0], *shots[2:]]
    stage("reorder_and_delete", lambda: backend.set_storyboard_shots(project_id, {"shots": reordered[:-1], "activity_id": aid}))
    assert len(state.voiceover_segments) == 7
    stage("undo_delete", lambda: backend.edit_history(project_id, {"action": "undo", "activity_id": aid}))
    assert len(state.voiceover_segments) == 8
    stage("redo_delete", lambda: backend.edit_history(project_id, {"action": "redo", "activity_id": aid}))
    stage("undo_to_full_version", lambda: backend.edit_history(project_id, {"action": "undo", "activity_id": aid}))
    shots = [shot.to_dict() for shot in state.storyboard_analysis.shots]
    first = shots[0]
    first["tts_text"] = "这款窗帘适合日常家居使用，先看看实际画面。"
    stage("edit_one_narration", lambda: backend.set_storyboard_shots(project_id, {"shots": shots, "activity_id": aid}))
    assert len(state.voiceover_segments) == 7
    stage("real_single_tts", lambda: backend.regenerate_voiceover(project_id, {"shot_id": first["shot_id"], "activity_id": aid}))
    assert len(state.voiceover_segments) == 8
    assert state.voiceover_segments[0].audio_path != original_audio[first["shot_id"]]
    for voice in state.voiceover_segments:
        if voice.shot_id in original_audio and voice.shot_id != first["shot_id"]:
            assert voice.audio_path == original_audio[voice.shot_id]
    stage("named_version", lambda: backend.edit_history(project_id, {"action": "checkpoint", "label": "39.5秒编辑验收", "activity_id": aid}))
    stage("save_reopen", lambda: backend.open_project({"path": str(target)}))
    state = backend.store.get_state(project_id)
    assert state.storyboard_analysis.shots[0].metadata["edit"]["subtitle"]["text"] != state.storyboard_analysis.shots[0].tts_text
    for ratio in ("9:16", "16:9"):
        result = stage("render_" + ratio, lambda ratio=ratio: backend.render_assembly(project_id, {"aspect_ratio": ratio, "subtitles": True, "activity_id": aid}))
        output = Path(result["output_path"])
        probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)]))
        duration = float(probe["format"]["duration"])
        assert 30 <= duration <= 60 and abs(duration - 39.5) < 0.25
        assert result["segment_count"] == 8
        assert any(item["codec_type"] == "audio" for item in probe["streams"])
        report["outputs"].append({"path": str(output), "duration_seconds": duration, "aspect_ratio": ratio,
                                  "bytes": output.stat().st_size, "warnings": result.get("warnings", [])})
        save_json(report, report_path)
        print("OUTPUT=" + str(output), flush=True)
    assert all(path.is_file() for path in original_exports)
    moved = root / ("阶段一 搬迁 空格 " + uuid.uuid4().hex[:8]) / target.name
    shutil.copytree(target, moved)
    stage("relocate_and_open", lambda: backend.open_project({"path": str(moved)}))
    state = backend.store.get_state(project_id)
    stage("history_after_relocation", lambda: backend.edit_history(project_id, {"action": "undo", "activity_id": aid}))
    for voice in state.voiceover_segments:
        assert Path(voice.audio_path).is_relative_to(moved) and Path(voice.audio_path).is_file()
    for entry in state.current_activity.metadata["edit_history"]["entries"]:
        for voice in entry["snapshot"]["voiceover_segments"]:
            assert Path(voice["audio_path"]).is_relative_to(moved)
    report["relocation"] = {"ok": True, "project_path": str(moved / "project.json")}
    report["completed_at"] = datetime.now().isoformat()
    save_json(report, report_path)
    print("REPORT=" + str(report_path), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
