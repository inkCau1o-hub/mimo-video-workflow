import json
import sys
import tempfile
import threading
import time
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
        assert reference.source_text
        return {
            "summary": f"测试分镜摘要: {reference.source_text[:12]}",
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
        if "标题" in prompt and "每行一个标题" in prompt:
            assert "旧流程改写结果" in prompt
            return "杜亚智能窗帘真香\n全屋智能窗帘攻略"
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


def sample_product_profile(**overrides):
    profile = {
        "product_name": "杜亚智能窗帘",
        "brand_name": "杜亚官方旗舰店",
        "features": "静音电机、智能控制、稳定耐用",
        "selling_points": "安装方便、全屋智能联动、性价比高",
        "target_audience": "装修用户、智能家居用户",
        "scenarios": "卧室、客厅、全屋智能",
        "promotion": "源头工厂活动",
        "rewrite_requirements": "保留核心信息，适合短视频口播，控制在300字以内",
        "tone": "口语化、可信赖",
        "forbidden_claims": "不能夸大效果，不能虚假承诺",
    }
    profile.update(overrides)
    return profile


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


def request_multipart(base_url, path, files):
    boundary = "----mimo-smoke-boundary"
    body = bytearray()
    for field_name, filename, content, content_type in files:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        body.extend(content)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, json.loads(raw) if raw else {}


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


def test_job_manager_cancels_running_jobs():
    manager = JobManager(run_inline=False)
    started = threading.Event()

    def wait_for_cancel(context):
        started.set()
        while not context.is_cancelled():
            time.sleep(0.01)
        context.raise_if_cancelled()

    job = manager.submit("test.cancel", "project_x", wait_for_cancel)
    assert started.wait(timeout=2), "job did not start"
    cancelled = manager.cancel(job.job_id)
    assert cancelled.cancel_requested is True

    for _ in range(200):
        current = manager.get(job.job_id)
        if current.status == "cancelled":
            break
        time.sleep(0.01)

    current = manager.get(job.job_id)
    assert current.status == "cancelled", current
    assert current.cancel_requested is True


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

            status, project_payload = request_json(
                base_url,
                "POST",
                "/projects",
                {"name": "服务化测试", "product_profile": sample_product_profile()},
            )
            assert status == 201, project_payload
            project_id = project_payload["status"]["project_id"]
            first_activity_id = project_payload["status"]["current_activity_id"]
            assert project_payload["status"]["activity_count"] == 1
            assert project_payload["status"]["product_name"] == "杜亚智能窗帘"
            assert project_payload["status"]["product_profile_complete"] is True
            assert project_payload["project"]["product_profile"]["selling_points"] == "安装方便、全屋智能联动、性价比高"
            assert project_payload["project"]["activities"][0]["activity_id"] == first_activity_id

            status, rename_payload = request_json(
                base_url,
                "PATCH",
                f"/projects/{project_id}",
                {"name": "服务化测试重命名"},
            )
            assert status == 200, rename_payload
            assert rename_payload["status"]["name"] == "服务化测试重命名"

            status, profile_payload = request_json(
                base_url,
                "PATCH",
                f"/projects/{project_id}",
                {"product_profile": sample_product_profile(product_name="杜亚梦幻帘")},
            )
            assert status == 200, profile_payload
            assert profile_payload["status"]["product_name"] == "杜亚梦幻帘"
            assert profile_payload["project"]["product_profile"]["product_name"] == "杜亚梦幻帘"

            status, delete_candidate = request_json(base_url, "POST", "/projects", {"name": "删除测试"})
            assert status == 201, delete_candidate
            delete_id = delete_candidate["status"]["project_id"]
            status, delete_payload = request_json(base_url, "DELETE", f"/projects/{delete_id}")
            assert status == 200, delete_payload
            assert delete_payload["deleted"] is True
            assert Path(delete_payload["trash_path"]).exists()
            assert delete_id not in {item["project_id"] for item in delete_payload["projects"]}

            state = server.backend.store.get_state(project_id)
            state.storyboard_analyzer = FakeStoryboardAnalyzer()
            state.material_catalog = FakeMaterialCatalog()
            state.tts_client = FakeTTSClient()
            state.assembly_planner = FakeAssemblyPlanner()
            state.assembly_renderer = FakeAssemblyRenderer()

            agent_reference = Path(tmpdir) / "agent-reference.mp4"
            reference_video = Path(tmpdir) / "reference.mp4"
            agent_reference.write_bytes(b"fake-video")
            reference_video.write_bytes(b"fake-video")

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/reference/validate-videos",
                {"videos": [str(reference_video), "missing-reference.mp4"]},
            )
            assert status == 200, payload
            assert payload["ok"] is False
            assert payload["videos"][0]["exists"] is True
            assert payload["videos"][1]["exists"] is False

            status, payload = request_multipart(
                base_url,
                f"/projects/{project_id}/reference/upload-videos",
                [("videos", "@杜亚官方旗舰店 这才是品质生活该装的窗帘！Real6Pro 720P.mp4", b"fake-video", "video/mp4")],
            )
            assert status == 200, payload
            uploaded_path = Path(payload["uploaded_videos"][0]["file_path"])
            assert uploaded_path.exists()
            assert uploaded_path.suffix == ".mp4"
            assert "杜亚官方旗舰店" in uploaded_path.name
            assert uploaded_path.parent.name == "reference"
            assert uploaded_path.parent.parent.name == "media"
            assert payload["validation"]["ok"] is True
            assert payload["activity"]["reference_videos"] == [str(uploaded_path)]

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/reference/transcribe",
                {"videos": ["missing-reference.mp4"], "model_size": "tiny"},
            )
            assert status == 400, payload
            assert "路径不完整" in payload["error"]

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/reference/videos",
                {"videos": [str(agent_reference)]},
            )
            assert status == 200, payload
            assert payload["validation"]["ok"] is True
            assert payload["activity"]["reference_videos"] == [str(agent_reference.resolve())]

            status, payload = request_json(
                base_url,
                "POST",
                "/agent/command",
                {"project_id": project_id, "command": "转写当前活动"},
            )
            assert status == 202, payload
            agent_transcribe_job = assert_succeeded_job(base_url, payload)
            assert "agent-reference.mp4" in agent_transcribe_job["result"]["reference_text"]
            state_after_agent_transcribe = server.backend.store.get_state(project_id)
            assert state_after_agent_transcribe.current_activity.reference_copy.product_name == "杜亚梦幻帘"
            assert (
                state_after_agent_transcribe.current_activity.reference_copy.metadata["product_profile"]["rewrite_requirements"]
                == "保留核心信息，适合短视频口播，控制在300字以内"
            )

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/reference/transcribe",
                {
                    "videos": [str(reference_video)],
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
                f"/projects/{project_id}/copy/rewrite",
                {"max_chars": 180},
            )
            assert status == 202, payload
            rewrite_job = assert_succeeded_job(base_url, payload)
            assert rewrite_job["result"]["status"]["has_copywriting"] is True
            rewrite_text = rewrite_job["result"]["copywriting"]["rewrite"]["text"]
            assert "旧流程改写结果" in rewrite_text
            assert rewrite_job["result"]["copywriting"]["rewrite"]["product_profile"]["product_name"] == "杜亚梦幻帘"

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/copy/titles",
                {"count": 2},
            )
            assert status == 202, payload
            titles_job = assert_succeeded_job(base_url, payload)
            titles = titles_job["result"]["copywriting"]["titles"]["items"]
            assert titles == ["杜亚智能窗帘真香", "全屋智能窗帘攻略"]

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/activities",
                {"name": "第二条活动"},
            )
            assert status == 201, payload
            second_activity_id = payload["activity"]["activity_id"]
            assert second_activity_id != first_activity_id
            assert payload["status"]["current_activity_id"] == second_activity_id

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/reference/text",
                {
                    "text": "这是第二条活动文案，强调活动之间不能互相覆盖。",
                    "product_name": "杜亚智能窗帘",
                    "keywords": ["智能", "安静"],
                },
            )
            assert status == 200, payload
            assert payload["status"]["has_reference"] is True

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/activities/{first_activity_id}/select",
            )
            assert status == 200, payload
            assert payload["status"]["current_activity_id"] == first_activity_id
            assert "后端转写文本" in payload["project"]["reference_copy"]["source_text"]

            status, payload = request_json(
                base_url,
                "POST",
                f"/projects/{project_id}/storyboard/analyze",
                {"target_duration_seconds": 6, "shot_count": 1, "model": "fake-model"},
            )
            assert status == 202, payload
            storyboard_job = assert_succeeded_job(base_url, payload)
            assert storyboard_job["result"]["status"]["storyboard_shots"] == 1
            assert "这是后端服务化" in storyboard_job["result"]["storyboard_analysis"]["summary"]

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
            assert "当前项目" in payload["summary"]

            status, payload = request_json(
                base_url,
                "POST",
                "/agent/command",
                {"command": "帮助"},
            )
            assert status == 202
            assert "创建项目" in payload["summary"]

            status, payload = request_json(
                base_url,
                "POST",
                "/agent/command",
                {"command": "创建项目 Agent测试"},
            )
            assert status == 202
            assert payload["status"]["name"] == "Agent测试"
            assert payload["project"]["activities"]

            status, payload = request_json(
                base_url,
                "POST",
                "/agent/command",
                {"project_id": project_id, "command": "创建活动 Agent活动"},
            )
            assert status == 202
            agent_activity_id = payload["activity"]["activity_id"]
            assert payload["activity"]["name"] == "Agent活动"

            status, payload = request_json(
                base_url,
                "POST",
                "/agent/command",
                {"project_id": project_id, "command": f"切换活动 {first_activity_id}"},
            )
            assert status == 202
            assert payload["status"]["current_activity_id"] == first_activity_id
            assert payload["activity"]["activity_id"] == first_activity_id
            assert agent_activity_id != first_activity_id

            status, payload = request_json(
                base_url,
                "POST",
                "/agent/command",
                {"project_id": project_id, "command": "删除项目"},
            )
            assert status == 202
            assert payload["requires_confirmation"] is True
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            workflow_api.transcribe_video = original_transcribe_video
            workflow_api.AIRewriter = original_ai_rewriter
            workflow_api.TitleGenerator = original_title_generator


def main():
    test_job_manager_reports_failures()
    test_job_manager_cancels_running_jobs()
    test_backend_http_workflow_chain()
    print("backend_api_smoke: ok")


if __name__ == "__main__":
    main()
