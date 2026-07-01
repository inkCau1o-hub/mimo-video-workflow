import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend.workflow_api as workflow_api
from backend.jobs import JobManager
from backend.server import create_server
from production_schema import AssemblyItem, AssemblyPlan


class FakeStoryboardAnalyzer:
    def analyze(self, reference, target_duration_seconds=None, shot_count=None, model=None):
        return {
            "summary": "测试分镜摘要",
            "target_duration_seconds": target_duration_seconds or 6,
            "model": model or "fake-model",
            "shots": [
                {
                    "shot_id": "S01",
                    "title": "测试镜头",
                    "script_text": "测试字幕",
                    "tts_text": "这是一段测试配音。",
                    "duration_seconds": 6,
                    "visual_tags": ["家居", "窗帘"],
                    "material_requirements": ["窗帘素材"],
                }
            ],
        }


class FakeMaterialCatalog:
    def analyze_batch(self, paths, output_dir=None, as_schema=True, **kwargs):
        return [
            {
                "asset_id": "asset_01",
                "file_path": paths[0],
                "material_type": "lifestyle",
                "scene_summary": "明亮家居窗帘素材",
                "visual_tags": ["家居", "窗帘", "明亮"],
                "duration_seconds": 8,
            }
        ]


class FakeTTSClient:
    def synthesize_segments(self, request_segments, output_dir, **kwargs):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        results = []
        for segment in request_segments:
            audio_path = Path(output_dir) / f"{segment['shot_id']}.mp3"
            audio_path.write_bytes(b"fake-mp3")
            results.append(
                {
                    "segment_id": segment["segment_id"],
                    "shot_id": segment["shot_id"],
                    "audio_path": str(audio_path),
                    "duration_seconds": segment["duration_seconds"],
                }
            )
        return results


class FakeAssemblyPlanner:
    def create_plan(self, storyboard_analysis, material_assets, voiceover_segments=None, output_path=None, as_schema=True):
        return AssemblyPlan(
            plan_id="plan_01",
            output_path=output_path or "",
            total_duration_seconds=6,
            items=[
                AssemblyItem(
                    item_id="item_01",
                    shot_id="S01",
                    material_asset_id="asset_01",
                    voiceover_segment_id="voiceover_S01",
                    start_seconds=0,
                    duration_seconds=6,
                )
            ],
        )


class FakeAssemblyRenderer:
    def validate_plan(self, raw_plan, output_path, require_existing_inputs=False):
        return {"ok": True, "warnings": []}

    def build_command(self, raw_plan, output_path, **kwargs):
        return ["ffmpeg", "-y", "-i", "asset.mp4", output_path]


class FakeAIRewriter:
    def rewrite_with_prompt(self, prompt):
        assert "后端转写文本" in prompt
        return "这是后端服务化旧流程改写结果"


class FakeTitleGenerator:
    def generate_titles_from_transcript(self, transcript_text, count=5):
        assert "后端转写文本" in transcript_text
        return ["后端标题一", "后端标题二"]


def fake_transcribe_video(video_path, output_dir, model_size):
    return {
        "ok": True,
        "video": video_path,
        "video_name": Path(video_path).stem,
        "audio_path": str(Path(output_dir) / "fake.wav"),
        "transcript": f"后端转写文本: {Path(video_path).name} / {model_size}",
    }


def request_json(base_url, method, path, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body) if body else {}


def request_with_origin(base_url, path, origin):
    request = urllib.request.Request(f"{base_url}{path}", headers={"Origin": origin}, method="GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.headers.get("Access-Control-Allow-Origin")


def assert_succeeded_job(base_url, job_payload):
    job = job_payload["job"]
    status, payload = request_json(base_url, "GET", f"/jobs/{job['job_id']}")
    assert status == 200, payload
    job = payload["job"]
    assert job["status"] == "succeeded", job
    assert job["progress"] == 100.0, job
    return job


def test_job_manager_reports_failures():
    manager = JobManager(run_inline=True)

    def fail(_context):
        raise RuntimeError("boom")

    job = manager.submit("test.failure", "project_x", fail)
    assert job.status == "failed"
    assert "boom" in job.error
    assert job.logs


def test_backend_http_workflow_chain():
    with tempfile.TemporaryDirectory() as tmpdir:
        original_transcribe_video = workflow_api.transcribe_video
        original_ai_rewriter = workflow_api.AIRewriter
        original_title_generator = workflow_api.TitleGenerator
        workflow_api.transcribe_video = fake_transcribe_video
        workflow_api.AIRewriter = FakeAIRewriter
        workflow_api.TitleGenerator = FakeTitleGenerator

        server = create_server(port=0, projects_dir=tmpdir, run_inline_jobs=True, quiet=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        try:
            status, health = request_json(base_url, "GET", "/health")
            assert status == 200
            assert health["ok"] is True
            status, allow_origin = request_with_origin(base_url, "/health", "http://127.0.0.1:5173")
            assert status == 200
            assert allow_origin == "http://127.0.0.1:5173"

            status, project_payload = request_json(base_url, "POST", "/projects", {"name": "服务化测试"})
            assert status == 201, project_payload
            project_id = project_payload["status"]["project_id"]

            state = server.backend.store.get_state(project_id)
            state.storyboard_analyzer = FakeStoryboardAnalyzer()
            state.material_catalog = FakeMaterialCatalog()
            state.tts_client = FakeTTSClient()
            state.assembly_planner = FakeAssemblyPlanner()
            state.assembly_renderer = FakeAssemblyRenderer()

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/reference/transcribe",
                {
                    "videos": ["/tmp/reference.mp4"],
                    "model_size": "tiny",
                    "product_name": "杜亚智能窗帘",
                    "keywords": ["智能", "安静"],
                    "output_dir": str(Path(tmpdir) / "transcripts"),
                },
            )
            assert status == 202, payload
            reference_job = assert_succeeded_job(base_url, payload)
            assert reference_job["result"]["status"]["has_reference"] is True
            assert "后端转写文本" in reference_job["result"]["reference_text"]

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/reference/text",
                {
                    "text": "这是参考文案，强调智能窗帘的安静、方便和品质感。",
                    "product_name": "杜亚智能窗帘",
                    "keywords": ["智能", "安静"],
                },
            )
            assert status == 200, payload
            assert payload["status"]["has_reference"] is True

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/storyboard/analyze",
                {"target_duration_seconds": 6, "shot_count": 1, "model": "fake-model"},
            )
            assert status == 202, payload
            storyboard_job = assert_succeeded_job(base_url, payload)
            assert storyboard_job["result"]["status"]["storyboard_shots"] == 1

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/materials/tag",
                {"material_paths": ["/tmp/material.mp4"], "max_frames": 1},
            )
            assert status == 202, payload
            material_job = assert_succeeded_job(base_url, payload)
            assert material_job["result"]["status"]["material_assets"] == 1

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/voiceovers/generate",
                {"output_dir": str(Path(tmpdir) / "voiceovers")},
            )
            assert status == 202, payload
            voiceover_job = assert_succeeded_job(base_url, payload)
            assert voiceover_job["result"]["status"]["voiceover_segments"] == 1

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/assembly/plan",
                {"output_path": str(Path(tmpdir) / "final.mp4")},
            )
            assert status == 202, payload
            assembly_job = assert_succeeded_job(base_url, payload)
            assert assembly_job["result"]["status"]["has_assembly_plan"] is True

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/legacy/generate",
                {
                    "videos": ["/tmp/legacy.mp4"],
                    "output_dir": str(Path(tmpdir) / "legacy"),
                    "model_size": "tiny",
                    "product_name": "测试产品",
                    "selling_points": "稳定、好用",
                    "requirements": "口语化",
                    "enable_cleaning": False,
                    "enable_titles": True,
                },
            )
            assert status == 202, payload
            legacy_job = assert_succeeded_job(base_url, payload)
            output = legacy_job["result"]["outputs"][0]
            assert "后端转写文本" in output["transcript"]
            assert "旧流程改写结果" in output["rewrite"]
            assert output["titles"] == ["后端标题一", "后端标题二"]

            status, payload = request_json(base_url, "GET", f"/projects/{project_id}/status")
            assert status == 200
            assert payload["has_reference"] is True
            assert payload["storyboard_shots"] == 1
            assert payload["material_assets"] == 1
            assert payload["voiceover_segments"] == 1
            assert payload["has_assembly_plan"] is True

            status, payload = request_json(
                base_url,
                "POST",
                "/agent/command",
                {"project_id": project_id, "command": "状态"},
            )
            assert status == 202
            assert payload["status"]["project_id"] == project_id
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            workflow_api.transcribe_video = original_transcribe_video
            workflow_api.AIRewriter = original_ai_rewriter
            workflow_api.TitleGenerator = original_title_generator


def main():
    test_job_manager_reports_failures()
    test_backend_http_workflow_chain()
    print("backend_api_smoke: ok")


if __name__ == "__main__":
    main()
