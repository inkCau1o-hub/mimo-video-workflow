"""Opt-in real MiMo/Whisper acceptance run; never prints or saves API keys."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Required: authorizes real API calls")
    parser.add_argument("--media-dir", type=Path, default=ROOT.parent / "测试文件")
    parser.add_argument("--key-file", type=Path, default=ROOT.parent / "mimo密钥.txt")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--through", choices=["transcribe", "voiceovers", "render"], default="render")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to enable the real API acceptance run")
    lines = [line.strip() for line in args.key_file.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    keys = [line for line in lines if not line.startswith(("http://", "https://"))]
    urls = [line for line in lines if line.startswith("https://")]
    if len(keys) != 1 or not keys[0].isascii() or any(c.isspace() for c in keys[0]):
        raise ValueError("Credential file format is invalid")
    os.environ["API_KEY"] = keys[0]
    if urls:
        os.environ["API_BASE_URL"] = urls[0]
    os.environ["PYTHONUTF8"] = "1"
    del keys, lines

    from backend.workflow_api import WorkflowBackend
    from backend.project_store import ProjectStore
    from backend.security import redact
    from production_schema import save_json

    report_dir = ROOT / "output" / "acceptance"
    backend = WorkflowBackend(ProjectStore(report_dir), run_inline_jobs=True)
    if args.project:
        state = backend.store.load_project(args.project)
    else:
        data = backend.create_project({"name": "实战验收_" + datetime.now().strftime("%Y%m%d_%H%M%S"),
            "product_profile": {
                "product_name": "杜亚智能窗帘", "features": "智能控制",
                "selling_points": "日常开合便利", "rewrite_requirements": "只用参考中可验证的事实，不夸大，约50字",
                "forbidden_claims": "不虚构价格、认证或效果承诺"}})
        state = backend.store.get_state(data["status"]["project_id"])
    project_id = state.project.project_id
    print("PROJECT=" + str(state.project_dir / "project.json"), flush=True)
    report_path = state.project_dir / "acceptance_report.json"
    report = {"project_id": project_id, "started_at": datetime.now().isoformat(), "real_api": True,
              "stages": [], "outputs": []}
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))

    def stage(name, action):
        started = time.monotonic()
        print("START=" + name, flush=True)
        try:
            value = action()
            job = value.get("job") if isinstance(value, dict) else None
            if job and job["status"] != "succeeded":
                raise RuntimeError(job.get("error") or job["status"])
            result = job["result"] if job else value
            report["stages"].append({"name": name, "ok": True, "seconds": round(time.monotonic() - started, 2)})
            save_json(report, report_path)
            print("PASS=" + name, flush=True)
            return result
        except Exception as exc:
            report["stages"].append({"name": name, "ok": False, "error": redact(str(exc)),
                                      "seconds": round(time.monotonic() - started, 2)})
            save_json(report, report_path)
            print("FAIL=" + name + ": " + redact(str(exc)), flush=True)
            raise SystemExit(1)

    videos = []
    for path in args.media_dir.glob("*.mp4"):
        raw = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
        videos.append((float(json.loads(raw)["format"]["duration"]), path))
    videos.sort()
    if len(videos) < 2:
        raise ValueError("At least two source videos are required")
    report["source_videos_checked"] = len(videos)
    if not state.reference_text:
        file = videos[0][1]
        imported = stage("reference_import", lambda: backend.upload_reference_videos(project_id,
                         [{"filename": file.name, "content": file.read_bytes()}]))
        stage("real_whisper_transcription", lambda: backend.transcribe_reference(project_id,
              {"videos": [imported["uploaded_videos"][0]["file_path"]], "model_size": "base"}))
    if args.through == "transcribe":
        return 0
    if not state.current_activity.copywriting.get("rewrite", {}).get("text", "").strip():
        stage("real_copywriting", lambda: backend.rewrite_copy(project_id, {"max_chars": 55}))
    if not state.storyboard_analysis:
        stage("real_storyboard", lambda: backend.analyze_storyboard(project_id, {"shot_count": 3, "target_duration_seconds": 15}))
    if not state.material_assets:
        stage("real_vision_materials", lambda: backend.tag_materials(project_id,
              {"material_paths": [str(item[1]) for item in videos[:2]], "max_frames": 1}))
    if not state.voiceover_segments:
        stage("real_tts", lambda: backend.generate_voiceovers(project_id, {}))
    if args.through == "voiceovers":
        return 0
    first_shot = state.storyboard_analysis.shots[0].shot_id
    stage("real_single_voiceover_regeneration", lambda: backend.regenerate_voiceover(project_id, {"shot_id": first_shot}))
    for ratio in ("9:16", "16:9"):
        result = stage("real_render_" + ratio, lambda ratio=ratio: backend.render_assembly(project_id,
                       {"aspect_ratio": ratio, "subtitles": True}))
        output = Path(result["output_path"])
        metadata = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)]))
        assert any(stream["codec_type"] == "video" for stream in metadata["streams"])
        assert any(stream["codec_type"] == "audio" for stream in metadata["streams"])
        assert float(metadata["format"]["duration"]) > 1
        report["outputs"].append({"path": str(output), "aspect_ratio": ratio,
                                  "duration": float(metadata["format"]["duration"]), "bytes": output.stat().st_size})
        save_json(report, report_path)
        print("OUTPUT=" + str(output), flush=True)
    original_dir = state.project_dir
    moved = report_dir / ("迁移 中文 空格 " + datetime.now().strftime("%H%M%S")) / original_dir.name
    shutil.copytree(original_dir, moved)
    reopened = stage("relocated_project_open", lambda: backend.open_project({"path": str(moved)}))
    moved_state = backend.store.get_state(project_id)
    for segment in moved_state.voiceover_segments:
        media = backend.resolve_project_media(project_id, segment.audio_path)
        assert media.is_relative_to(moved.resolve()) and media.stat().st_size > 0
    for export in moved_state.current_activity.metadata.get("exports", []):
        assert backend.resolve_project_media(project_id, export["output_path"]).is_relative_to(moved.resolve())
    backend.store.save_project(project_id)
    report["relocation_check"] = {"ok": True, "project_path": str(moved / "project.json")}
    report["completed_at"] = datetime.now().isoformat()
    save_json(report, report_path)
    print("REPORT=" + str(report_path), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
