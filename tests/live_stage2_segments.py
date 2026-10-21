"""Opt-in real vision analysis and same-source before/after clip-range acceptance."""

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
        parser.error("--live is required for real vision API calls")
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
    root = ROOT / "output" / "acceptance"
    pid = "project_" + uuid.uuid4().hex[:8]
    name = "阶段二片段实战_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    target = root / f"{name}_{pid[-8:]}.mimoproject"
    shutil.copytree(base, target)
    state = WorkflowState.load_project(target / "project.json")
    state.project.project_id, state.project.name = pid, name
    state.save_project(target / "project.json")
    backend = WorkflowBackend(ProjectStore(root), run_inline_jobs=True)
    backend.open_project({"path": str(target)})
    state = backend.store.get_state(pid)
    aid = state.current_activity.activity_id
    report_path = target / "stage2_segments_report.json"
    report = {"project_id": pid, "started_at": datetime.now().isoformat(), "real_vision_api": True,
              "stages": [], "outputs": [], "user_quality_acceptance": "pending"}
    print("PROJECT=" + str(target / "project.json"), flush=True)

    def stage(name, action):
        started = time.monotonic()
        try:
            result = action()
            job = result.get("job") if isinstance(result, dict) else None
            if job and job["status"] != "succeeded":
                raise RuntimeError(job.get("error") or job["status"])
            report["stages"].append({"name": name, "ok": True, "seconds": round(time.monotonic() - started, 3)})
            save_json(report, report_path)
            print("PASS=" + name, flush=True)
            return job["result"] if job else result
        except Exception as exc:
            report["stages"].append({"name": name, "ok": False, "error": redact(str(exc))})
            save_json(report, report_path)
            print("FAIL=" + name + ": " + redact(str(exc)), flush=True)
            raise SystemExit(1)

    original_voice = {voice.shot_id: voice.audio_path for voice in state.voiceover_segments}
    report["before"] = deepcopy(state.current_activity.metadata.get("render_result"))
    assets = state.material_assets[:2]
    indexed = stage("real_vision_segment_index", lambda: backend.index_material_segments(pid, {"asset_ids": [asset.asset_id for asset in assets], "activity_id": aid}))
    report["segment_count"] = indexed["segment_count"]
    report["indexes"] = [{"asset_id": asset.asset_id, "segments": len(asset.metadata.get("segment_index", {}).get("segments", [])),
                           "coverage_ratio": asset.metadata.get("segment_index", {}).get("coverage_ratio")}
                          for asset in state.material_assets]
    impossible = stage("missing_visual_requirement", lambda: backend.recommend_material_segments(pid, {
        "shot": {"shot_id": "check", "visual_tags": ["手机APP控制窗帘开合的演示"], "duration_seconds": 4}, "activity_id": aid}))
    assert impossible["needs_material"] and all(item["requires_confirmation"] for item in impossible["candidates"])
    shots = [shot.to_dict() for shot in state.storyboard_analysis.shots]
    adopted = []
    for shot in shots:
        result = backend.recommend_material_segments(pid, {"shot": shot, "target_duration": shot["duration_seconds"], "limit": 10, "activity_id": aid})
        candidates = [item for item in result["candidates"] if item["shortage_seconds"] <= .001]
        assert candidates, "No sufficiently long test window"
        chosen = next((item for item in candidates if item["asset_id"] == shot["metadata"].get("material_asset_id")), candidates[0])
        adopted.append({"shot_id": shot["shot_id"], **chosen, "test_selection_only": True})
        shot["metadata"]["material_asset_id"] = chosen["asset_id"]
        shot["metadata"]["edit"].update(source_in=chosen["source_in"], source_out=chosen["source_out"], shortage="error")
    report["test_adoptions"] = adopted
    stage("save_longer_ranges_without_regenerating_audio", lambda: backend.set_storyboard_shots(pid, {"shots": shots, "activity_id": aid, "label": "片段候选实战比较"}))
    assert {voice.shot_id: voice.audio_path for voice in state.voiceover_segments} == original_voice
    for ratio in ("9:16", "16:9"):
        result = stage("render_" + ratio, lambda ratio=ratio: backend.render_assembly(pid, {"aspect_ratio": ratio, "subtitles": True, "activity_id": aid}))
        assert result["duration_seconds"] == 39.5
        assert not any("frozen" in warning or "shorter" in warning for warning in result.get("warnings", []))
        output = Path(result["output_path"])
        probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)]))
        assert abs(float(probe["format"]["duration"]) - 39.5) < .1
        assert any(item["codec_type"] == "audio" for item in probe["streams"])
        report["outputs"].append({"path": str(output), "aspect_ratio": ratio, "duration_seconds": 39.5, "warnings": result.get("warnings", [])})
        save_json(report, report_path)
        print("OUTPUT=" + str(output), flush=True)
    moved = root / ("阶段二 搬迁 空格 " + uuid.uuid4().hex[:8]) / target.name
    shutil.copytree(target, moved)
    stage("relocate_index_and_reopen", lambda: backend.open_project({"path": str(moved)}))
    result = stage("recommend_after_relocation", lambda: backend.recommend_material_segments(pid, {"shot": shots[0], "target_duration": 8, "activity_id": aid}))
    assert result["candidates"] and not result["unavailable_assets"]
    assert all(Path(item["thumbnail_path"]).is_relative_to(moved) and Path(item["thumbnail_path"]).is_file() for item in result["candidates"])
    report["relocation"] = str(moved)
    report["completed_at"] = datetime.now().isoformat()
    save_json(report, report_path)
    print("REPORT=" + str(report_path), flush=True)


if __name__ == "__main__":
    main()
