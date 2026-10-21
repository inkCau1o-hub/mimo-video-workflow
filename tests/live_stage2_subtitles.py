"""Non-destructive acceptance with supplied footage and existing real narration."""

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import math
from pathlib import Path
import shutil
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.project_store import ProjectStore
from backend.workflow_api import WorkflowBackend
from media_render import _Runner, _probe, _stream_duration, discover_executable
from production_schema import save_json
from workflow_state import WorkflowState


def signatures(state):
    return {v.shot_id: hashlib.sha256(Path(v.audio_path).read_bytes()).hexdigest() for v in state.voiceover_segments}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-project", type=Path, required=True)
    args = parser.parse_args()
    root = ROOT / "output/acceptance"
    identifier = uuid.uuid4().hex[:8]
    name = "阶段二字幕节奏实战_" + datetime.now().strftime("%Y%m%d_%H%M%S")
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
    report = {"project_id": pid, "started_at": datetime.now().isoformat(), "ai_api_calls": 0,
              "alignment": "manual cue times, not speech forced alignment", "music": "existing synthetic test fixture",
              "user_quality_acceptance": "pending", "stages": [], "outputs": []}
    report_path = target / "stage2_subtitle_report.json"
    print("PROJECT=" + str(target), flush=True)

    def stage(name, action):
        started = time.monotonic()
        try:
            value = action()
            report["stages"].append({"name": name, "ok": True, "seconds": round(time.monotonic() - started, 3)})
            save_json(report, report_path)
            print("PASS=" + name, flush=True)
            return value
        except Exception:
            report["stages"].append({"name": name, "ok": False})
            save_json(report, report_path)
            raise

    original_hashes = signatures(state)
    original_texts = [shot.tts_text for shot in state.storyboard_analysis.shots]
    before = deepcopy(state.current_activity.metadata.get("render_result"))
    report["before"] = {key: before[key] for key in ("output_path", "duration_seconds")} if before else None
    shots = [shot.to_dict() for shot in state.storyboard_analysis.shots]
    voices = {voice.shot_id: voice for voice in state.voiceover_segments}
    expected = 0
    ffprobe = discover_executable("ffprobe")
    for index, shot in enumerate(shots):
        voice = voices[shot["shot_id"]]
        info = _probe(_Runner(None, None, 30), ffprobe, Path(voice.audio_path), target)
        measured = _stream_duration(info, "audio")
        edit = shot["metadata"].setdefault("edit", {})
        edit.update(duration_mode="narration", lead_in_seconds=.25 if index == 0 else .1,
                    tail_out_seconds=.35 if index == 0 else .15)
        subtitle = edit.setdefault("subtitle", {})
        subtitle.update(timing_mode="narration")
        if index == 0:
            subtitle.update(timing_mode="cues", timing_source="manual", cues=[
                {"start": .25, "end": 2.1, "text": "这款窗帘适合日常家居使用"},
                {"start": 2.2, "end": round(min(4.15, measured + .25), 2), "text": "先看看实际画面"}])
        expected += math.ceil((measured + edit["lead_in_seconds"] + edit["tail_out_seconds"]) * 30 - 1e-7) / 30

    stage("save_cue_times_and_narration_gaps", lambda: backend.set_storyboard_shots(pid, {"activity_id": aid, "shots": shots, "label": "字幕定时与节奏实战"}))
    stage("undo_saved_timing", lambda: backend.edit_history(pid, {"activity_id": aid, "action": "undo"}))
    stage("redo_saved_timing", lambda: backend.edit_history(pid, {"activity_id": aid, "action": "redo"}))
    state = backend.store.get_state(pid)
    assert signatures(state) == original_hashes
    assert [shot.tts_text for shot in state.storyboard_analysis.shots] == original_texts
    assert state.voiceover_segments[0].start_seconds == .25
    report["expected_duration_seconds"] = expected

    def render(ratio):
        job = backend.render_assembly(pid, {"activity_id": aid, "aspect_ratio": ratio, "subtitles": True})["job"]
        assert job["status"] == "succeeded", job.get("error")
        result = job["result"]
        path = Path(result["output_path"])
        info = _probe(_Runner(None, None, 60), ffprobe, path, path.parent)
        assert abs(float(info["format"]["duration"]) - expected) < .1
        assert any(stream["codec_type"] == "audio" for stream in info["streams"])
        assert len(result["shot_timing"]) == len(shots)
        assert result["shot_timing"][0]["subtitle_events"] == shots[0]["metadata"]["edit"]["subtitle"]["cues"]
        assert result["shot_timing"][1]["subtitle_events"][0]["start"] == .1
        report["outputs"].append({key: result[key] for key in ("output_path", "duration_seconds", "width", "height", "shot_timing", "warnings")})
        print("OUTPUT=" + str(path), flush=True)

    stage("portrait_with_timed_subtitles_and_gaps", lambda: render("9:16"))
    moved = root / ("字幕 节奏 搬迁 " + identifier) / target.name
    shutil.copytree(target, moved)
    stage("reopen_after_relocation", lambda: backend.open_project({"path": str(moved)}))
    state = backend.store.get_state(pid)

    def validate_relocation():
        assert signatures(state) == original_hashes
        assert [shot.tts_text for shot in state.storyboard_analysis.shots] == original_texts
        assert state.storyboard_analysis.shots[0].metadata["edit"]["subtitle"]["cues"] == shots[0]["metadata"]["edit"]["subtitle"]["cues"]
        assert Path(state.current_activity.metadata["audio_settings"]["music_path"]).is_relative_to(moved)
        assert len(state.voiceover_segments) == len(shots)

    stage("relocated_cues_music_and_unchanged_narration", validate_relocation)
    stage("landscape_render_after_relocation", lambda: render("16:9"))
    report["relocation"] = str(moved)
    report["completed_at"] = datetime.now().isoformat()
    save_json(report, report_path)
    save_json(report, moved / report_path.name)
    print("REPORT=" + str(report_path), flush=True)


if __name__ == "__main__":
    main()
