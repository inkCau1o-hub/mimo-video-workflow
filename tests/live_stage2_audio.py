"""Real supplied footage + existing real TTS + a synthetic, non-production music fixture."""

import argparse
from array import array
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import time
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.project_store import ProjectStore
from backend.workflow_api import WorkflowBackend
from media_render import _Runner, _probe, discover_executable
from production_schema import save_json
from workflow_state import WorkflowState


def make_music(path):
    rate = 24000
    chords = [(261.63, 329.63, 392), (220, 261.63, 329.63), (174.61, 220, 261.63), (196, 246.94, 293.66)]
    data = array("h")
    for i in range(rate * 12):
        t = i / rate
        notes = chords[int(t // 3) % len(chords)]
        phase = t % .75
        envelope = min(1, phase / .012) * math.exp(-phase * 3)
        value = sum(math.sin(2 * math.pi * note * t) + .12 * math.sin(4 * math.pi * note * t) for note in notes) / 3
        data.append(int(32767 * .32 * envelope * value))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(data.tobytes())


def measure(path):
    raw = _Runner(None, None, 120).run([discover_executable("ffmpeg"), "-hide_banner", "-nostdin", "-v", "info", "-i", str(path),
        "-map", "0:a:0", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], path.parent, "acceptance loudness", capture_stderr=True)
    text = raw.decode("utf-8", errors="replace")
    value, _ = json.JSONDecoder().raw_decode(text[text.rindex("{"):].lstrip())
    result = {key: float(value[key]) for key in ("input_i", "input_tp", "input_lra")}
    assert all(math.isfinite(item) for item in result.values())
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-project", type=Path, required=True)
    args = parser.parse_args()
    root = ROOT / "output/acceptance"
    identifier = uuid.uuid4().hex[:8]
    name = "阶段二声音实战_" + datetime.now().strftime("%Y%m%d_%H%M%S")
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
              "music_source": "locally synthesized test fixture, not production music", "user_quality_acceptance": "pending", "stages": [], "outputs": []}
    report_path = target / "stage2_audio_report.json"
    print("PROJECT=" + str(target), flush=True)

    def stage(name, action):
        started = time.monotonic()
        try:
            result = action()
            report["stages"].append({"name": name, "ok": True, "seconds": round(time.monotonic() - started, 3)})
            save_json(report, report_path)
            print("PASS=" + name, flush=True)
            return result
        except Exception:
            report["stages"].append({"name": name, "ok": False})
            save_json(report, report_path)
            raise

    signatures = {voice.shot_id: hashlib.sha256(Path(voice.audio_path).read_bytes()).hexdigest() for voice in state.voiceover_segments}
    before = next(item for item in reversed(state.current_activity.metadata["exports"]) if item.get("aspect_ratio") == "9:16")
    report["before"] = {"path": before["output_path"], "loudness": measure(Path(before["output_path"]))}
    music = target / "合成试音轨_非正式配乐.wav"
    make_music(music)
    item = stage("import_real_audio_container", lambda: backend.upload_music(pid, {"activity_id": aid, "files": [{"filename": music.name, "content": music.read_bytes()}]}))["uploaded_music"]
    stage("save_audio_settings", lambda: backend.save_audio_settings(pid, {"activity_id": aid, "base_revision": "", "audio_settings": {
        "normalize_voice": True, "target_lufs": -16, "music_path": item["file_path"], "music_gain_db": -18,
        "duck_music": True, "loop_music": True, "fade_in_seconds": 1, "fade_out_seconds": 1.5}}))

    def render(ratio):
        job = backend.render_assembly(pid, {"activity_id": aid, "aspect_ratio": ratio, "subtitles": True})["job"]
        assert job["status"] == "succeeded", job.get("error")
        result = job["result"]
        path = Path(result["output_path"])
        info = _probe(_Runner(None, None, 60), discover_executable("ffprobe"), path, path.parent)
        assert abs(float(info["format"]["duration"]) - 39.5) < .1
        assert any(stream["codec_type"] == "audio" for stream in info["streams"])
        assert len(result["audio_processing"]) == 8 and all(item["normalized"] for item in result["audio_processing"])
        loudness = measure(path)
        assert loudness["input_tp"] < 0, loudness
        report["outputs"].append({"path": str(path), "aspect_ratio": ratio, "duration_seconds": result["duration_seconds"],
                                   "loudness": loudness, "warnings": result["warnings"], "audio_processing": result["audio_processing"]})
        print("OUTPUT=" + str(path), flush=True)

    stage("portrait_with_music_and_normalized_voice", lambda: render("9:16"))
    moved = root / ("声音 搬迁 空格 " + identifier) / target.name
    shutil.copytree(target, moved)
    stage("reopen_after_relocation", lambda: backend.open_project({"path": str(moved)}))
    state = backend.store.get_state(pid)
    def validate_moved():
        settings = state.current_activity.metadata["audio_settings"]
        assert Path(settings["music_path"]).is_relative_to(moved) and Path(settings["music_path"]).is_file()
        assert {voice.shot_id: hashlib.sha256(Path(voice.audio_path).read_bytes()).hexdigest() for voice in state.voiceover_segments} == signatures
        assert len(state.current_activity.metadata["music_assets"]) == 1
    stage("portable_music_and_unchanged_voices", validate_moved)
    stage("landscape_render_after_relocation", lambda: render("16:9"))
    report["relocation"] = str(moved)
    report["completed_at"] = datetime.now().isoformat()
    save_json(report, report_path)
    save_json(report, moved / report_path.name)
    print("REPORT=" + str(report_path), flush=True)
    print("UI_PROJECT=" + str(moved), flush=True)


if __name__ == "__main__":
    main()
