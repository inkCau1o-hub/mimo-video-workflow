"""Non-destructive real-footage acceptance for framing, fades and PNG covers."""

import argparse
from datetime import datetime
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.project_store import ProjectStore
from backend.workflow_api import WorkflowBackend
from media_render import _Runner, _probe, discover_executable
from production_schema import save_json
from workflow_state import WorkflowState


def signatures(state):
    return {v.shot_id: hashlib.sha256(Path(v.audio_path).read_bytes()).hexdigest() for v in state.voiceover_segments}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-project", type=Path, required=True)
    args = parser.parse_args()
    root, identifier = ROOT / "output/acceptance", uuid.uuid4().hex[:8]
    name = "阶段二构图封面实战_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    target = root / (name + "_" + identifier + ".mimoproject")
    shutil.copytree(args.base_project.resolve(), target)
    state = WorkflowState.load_project(target)
    pid = "project_" + identifier
    state.project.project_id, state.project.name = pid, name
    state.save_project()
    backend = WorkflowBackend(ProjectStore(root), run_inline_jobs=True)
    backend.open_project({"path": str(target)})
    state = backend.store.get_state(pid)
    aid = state.current_activity.activity_id
    expected_duration = state.current_activity.metadata["render_result"]["duration_seconds"]
    hashes = signatures(state)
    old_output = Path(state.current_activity.metadata["render_result"]["output_path"])
    old_hash = hashlib.sha256(old_output.read_bytes()).hexdigest()
    report = {"project_id": pid, "started_at": datetime.now().isoformat(), "ai_api_calls": 0,
              "framing": "manual per aspect ratio", "transition": "fade through black, no audio crossfade",
              "user_quality_acceptance": "pending", "stages": [], "outputs": [], "covers": []}
    report_path = target / "stage2_visual_report.json"
    print("PROJECT=" + str(target), flush=True)

    def stage(label, action):
        started = time.monotonic()
        try:
            result = action()
            report["stages"].append({"name": label, "ok": True, "seconds": round(time.monotonic() - started, 3)})
            save_json(report, report_path)
            print("PASS=" + label, flush=True)
            return result
        except Exception:
            report["stages"].append({"name": label, "ok": False})
            save_json(report, report_path)
            raise

    shots = [shot.to_dict() for shot in state.storyboard_analysis.shots]
    for index, shot in enumerate(shots):
        edit = shot["metadata"].setdefault("edit", {})
        edit["framing"] = {"9:16": {"fit": "cover", "crop_x": .8, "crop_y": .5},
                           "16:9": {"fit": "contain", "crop_x": .35, "crop_y": .5}}
        edit["transition"] = {"kind": "fade_black" if index < 3 else "cut", "duration_seconds": .2}
    stage("save_independent_framing_and_transitions", lambda: backend.set_storyboard_shots(pid,
          {"activity_id": aid, "shots": shots, "label": "分画幅构图与基础转场实战"}))
    stage("undo_visual_edit", lambda: backend.edit_history(pid, {"activity_id": aid, "action": "undo"}))
    stage("redo_visual_edit", lambda: backend.edit_history(pid, {"activity_id": aid, "action": "redo"}))
    ffprobe, ffmpeg = discover_executable("ffprobe"), discover_executable("ffmpeg")

    def render(ratio):
        job = backend.render_assembly(pid, {"activity_id": aid, "aspect_ratio": ratio, "subtitles": True})["job"]
        assert job["status"] == "succeeded", job.get("error")
        result = job["result"]
        path = Path(result["output_path"])
        info = _probe(_Runner(None, None, 60), ffprobe, path, path.parent)
        assert abs(float(info["format"]["duration"]) - expected_duration) < .05
        assert any(stream["codec_type"] == "audio" for stream in info["streams"])
        assert len(result["shot_timing"]) == 8
        assert result["visual_processing"][0]["framing"] == shots[0]["metadata"]["edit"]["framing"][ratio]
        assert result["visual_processing"][1]["fade_in_seconds"] == .2
        # Boundary frames go black while the total timeline stays unchanged.
        boundary = result["shot_timing"][0]["duration_seconds"]
        raw = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(boundary), "-i", str(path),
                              "-frames:v", "1", "-vf", "scale=32:32", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30).stdout
        assert len(raw) == 32 * 32 * 3 and max(raw) < 12, max(raw)
        report["outputs"].append({key: result[key] for key in ("output_path", "duration_seconds", "width", "height", "visual_processing", "warnings")})
        print("OUTPUT=" + str(path), flush=True)
        return result

    def cover(result, title):
        before = hashlib.sha256(Path(result["output_path"]).read_bytes()).hexdigest()
        job = backend.generate_cover(pid, {"activity_id": aid, "source_video_path": result["output_path"],
              "settings": {"time_seconds": .65, "title": title, "position": "top", "font_size": 64}})["job"]
        assert job["status"] == "succeeded", job.get("error")
        image = job["result"]
        assert (image["width"], image["height"]) == (result["width"], result["height"])
        assert image["frame_index"] == 19
        assert image["size_bytes"] > 1000
        assert hashlib.sha256(Path(result["output_path"]).read_bytes()).hexdigest() == before
        report["covers"].append({key: image[key] for key in ("image_path", "source_video_path", "width", "height", "frame_time_seconds", "settings")})
        print("COVER=" + image["image_path"], flush=True)

    portrait = stage("portrait_framing_fade_and_intact_timeline", lambda: render("9:16"))
    stage("portrait_cover_without_rerender", lambda: cover(portrait, "青禾智能窗帘\n日常家居实拍"))
    moved = root / ("构图 封面 搬迁 " + identifier) / target.name
    shutil.copytree(target, moved)
    stage("reopen_relocated_project", lambda: backend.open_project({"path": str(moved)}))

    def validate_relocation():
        state = backend.store.get_state(pid)
        assert signatures(state) == hashes
        assert Path(state.current_activity.metadata["audio_settings"]["music_path"]).is_relative_to(moved)
        saved = state.current_activity.metadata["covers"][-1]
        for key in ("source_video_path", "image_path"):
            assert Path(saved[key]).is_relative_to(moved) and Path(saved[key]).exists()
        for shot in state.storyboard_analysis.shots:
            assert shot.metadata["edit"]["framing"] == shots[0]["metadata"]["edit"]["framing"]
        assert hashlib.sha256(old_output.read_bytes()).hexdigest() == old_hash

    stage("relocated_covers_music_and_unchanged_voices", validate_relocation)
    landscape = stage("landscape_after_relocation", lambda: render("16:9"))
    stage("landscape_cover_after_relocation", lambda: cover(landscape, "青禾智能窗帘"))
    assert signatures(backend.store.get_state(pid)) == hashes
    report.update(relocation=str(moved), voice_hashes_unchanged=True, old_export_unchanged=True,
                  completed_at=datetime.now().isoformat())
    save_json(report, report_path)
    save_json(report, moved / report_path.name)
    print("REPORT=" + str(report_path), flush=True)


if __name__ == "__main__":
    main()
