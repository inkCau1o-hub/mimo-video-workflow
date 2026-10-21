import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from backend.jobs import JobManager
from backend.project_store import ProjectStore
from backend.server import create_server
from backend.workflow_api import WorkflowBackend, _path_from_reference
from tts_interfaces import MimoTTSClient
from ai_client import OpenAICompatibleClient, parse_chat_text, text_generation_options
from ai_rewriter import AIRewriter
from title_generator import TitleGenerator
from production_schema import MaterialAsset, VoiceoverSegment
from media_assembler import FFmpegAssemblyRenderer


def finish(backend, job_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        record = backend.jobs.get(job_id)
        if record.status in {"succeeded", "failed", "cancelled"}:
            return record
        time.sleep(0.01)
    raise AssertionError("job did not finish")


class JobIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_jobs_")
        self.backend = WorkflowBackend(ProjectStore(self.tmp.name))
        self.project_id = self.backend.create_project({"name": "test"})["status"]["project_id"]
        self.state = self.backend.store.get_state(self.project_id)
        self.activity_id = self.state.current_activity.activity_id
        self.backend.set_reference_text(self.project_id, {"text": "original"})
        self.release = threading.Event()
        self.started = threading.Event()

    def tearDown(self):
        self.release.set()
        for job in self.backend.jobs.list():
            finish(self.backend, job.job_id)
        self.tmp.cleanup()

    def submit(self):
        def work(state, context):
            self.started.set()
            if not self.release.wait(6):
                raise RuntimeError("test timed out")
            state.set_copywriting("rewrite", {"text": "generated"})
            return {"text": "generated"}
        job = self.backend._submit_activity_job("test.rewrite", self.project_id, work)["job"]
        self.assertTrue(self.started.wait(2))
        return job["job_id"]

    def test_switch_does_not_redirect_completion(self):
        job_id = self.submit()
        second = self.backend.create_activity(self.project_id, {"name": "second"})["activity"]["activity_id"]
        self.release.set()
        self.assertEqual(finish(self.backend, job_id).status, "succeeded")
        self.assertEqual(self.state.current_activity.activity_id, second)
        self.assertFalse(self.state.current_activity.copywriting)
        self.assertEqual(self.state.get_activity(self.activity_id).copywriting["rewrite"]["text"], "generated")

    def test_edit_during_job_preserved(self):
        job_id = self.submit()
        self.backend.set_reference_text(self.project_id, {"text": "new edit"})
        self.release.set()
        self.assertEqual(finish(self.backend, job_id).status, "failed")
        self.assertEqual(self.state.reference_text, "new edit")
        self.assertFalse(self.state.current_activity.copywriting)

    def test_cancel_does_not_commit(self):
        job_id = self.submit()
        self.backend.cancel_job(job_id)
        self.release.set()
        self.assertEqual(finish(self.backend, job_id).status, "cancelled")
        self.assertFalse(self.state.current_activity.copywriting)

    def test_failed_commit_restores_in_memory_project(self):
        job_id = self.submit()
        with patch.object(self.backend.store, "save_project", side_effect=OSError("disk unavailable")):
            self.release.set()
            self.assertEqual(finish(self.backend, job_id).status, "failed")
        self.assertFalse(self.state.current_activity.copywriting)
        restored = ProjectStore(self.tmp.name).get_state(self.project_id)
        self.assertFalse(restored.current_activity.copywriting)

    def test_duplicate_task_and_deletion_rejected(self):
        self.submit()
        with self.assertRaises(ValueError):
            self.backend._submit_activity_job("test.other", self.project_id, lambda s, c: {})
        with self.assertRaises(ValueError):
            self.backend.delete_project(self.project_id)

    def test_stale_activity_submission_rejected(self):
        self.backend.create_activity(self.project_id, {"name": "other"})
        with self.assertRaises(ValueError):
            self.backend.set_reference_text(self.project_id, {"text": "wrong", "activity_id": self.activity_id})

    def test_job_history_and_redaction(self):
        secret = "test-private-credential-123456"
        with patch.object(config, "API_KEY", secret):
            def failure(context):
                context.log("Authorization: Bearer " + secret)
                raise ValueError("invalid header " + secret)
            job = self.backend.jobs.submit("test.failure", self.project_id, failure, run_inline=True)
            self.assertNotIn(secret, json.dumps(job.to_dict()))
            path = Path(self.tmp.name) / ".jobs.json"
            self.assertNotIn(secret, path.read_text(encoding="utf-8"))
            restarted = JobManager(storage_path=path)
            self.assertEqual(restarted.get(job.job_id).status, "failed")

    def test_unfinished_history_marked_interrupted(self):
        job_id = self.submit()
        restarted = JobManager(storage_path=Path(self.tmp.name) / ".jobs.json")
        self.assertEqual(restarted.get(job_id).status, "failed")
        self.assertEqual(restarted.get(job_id).message, "interrupted")


class MediaHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_http_")
        self.server = create_server(port=0, projects_dir=self.tmp.name, quiet=True, frontend_port=5283)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        payload = self.server.backend.create_project({"name": "media"})
        self.project_id = payload["status"]["project_id"]
        self.file = Path(payload["project"]["project_dir"]) / "sample.mp3"
        self.file.write_bytes(b"0123456789")
        self.media = f"/projects/{self.project_id}/media?path=sample.mp3"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, method="GET", headers=None, data=None):
        request = urllib.request.Request(self.base + path, method=method, headers=headers or {}, data=data)
        try:
            return urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            return error

    def test_media_ranges_and_head(self):
        for value, body in [("bytes=2-5", b"2345"), ("bytes=-3", b"789"), ("bytes=7-", b"789")]:
            with self.request(self.media, headers={"Range": value}) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), body)
        with self.request(self.media, method="HEAD") as response:
            self.assertEqual(response.headers["Content-Length"], "10")
            self.assertEqual(response.read(), b"")

    def test_invalid_range_and_traversal(self):
        with self.request(self.media, headers={"Range": "bytes=99-100"}) as response:
            self.assertEqual(response.status, 416)
        with self.request(self.media.replace("sample.mp3", "../.jobs.json")) as response:
            self.assertEqual(response.status, 400)

    def test_download_attachment_preserves_unicode_filename_and_bytes(self):
        name = "成片 空格 &.mp4"
        self.file.with_name(name).write_bytes(b"0123456789")
        query = urllib.parse.urlencode({"path": name, "download": "1"})
        with self.request(f"/projects/{self.project_id}/media?{query}") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Disposition"],
                             "attachment; filename*=UTF-8''" + urllib.parse.quote(name, safe=""))
            self.assertEqual(response.read(), b"0123456789")
        with self.request(self.media) as response:
            self.assertIsNone(response.headers.get("Content-Disposition"))

    def test_download_does_not_bypass_media_containment(self):
        with self.request(self.media.replace("sample.mp3", "../.jobs.json") + "&download=1") as response:
            self.assertEqual(response.status, 400)

    def test_custom_cors_and_untrusted_origin(self):
        origin = "http://127.0.0.1:5283"
        with self.request("/health", headers={"Origin": origin}) as response:
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
        with self.request("/projects", method="POST", headers={"Origin": "https://untrusted.example"}, data=b"{}") as response:
            self.assertEqual(response.status, 403)
        with self.request("/health", headers={"Origin": "http://127.0.0.1:5173"}) as response:
            self.assertEqual(response.status, 403)
        with self.request("/health", headers={"Host": "untrusted.example"}) as response:
            self.assertEqual(response.status, 403)

    def test_copy_edit_jobs_and_upload_routes(self):
        with self.request(f"/projects/{self.project_id}/copy/text", method="POST",
                          headers={"Content-Type": "application/json"}, data=b'{"text":"manual"}') as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response)["project"]["activities"][0]["copywriting"]["rewrite"]["text"], "manual")
        with self.request(f"/projects/{self.project_id}/jobs") as response:
            self.assertEqual(json.load(response), {"jobs": []})


class SpeechValidationTests(unittest.TestCase):
    def test_invalid_key_never_reaches_transport(self):
        with patch("tts_interfaces.requests.post") as request:
            with self.assertRaises(ValueError):
                MimoTTSClient(api_key="secret\nhttps://bad.example").synthesize("test", "unused.mp3")
            request.assert_not_called()

    def test_measured_duration_and_progress(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 16000)
        payload = {"choices": [{"message": {"audio": {"data": base64.b64encode(buffer.getvalue()).decode("ascii")}}}]}
        progress = []
        with tempfile.TemporaryDirectory(prefix="mimo_tts_") as output:
            with patch("tts_interfaces.requests.post") as request:
                request.return_value.status_code = 200
                request.return_value.json.return_value = payload
                segments = MimoTTSClient(api_key="test-key").synthesize_segments(
                    [{"segment_id": "S1", "text": "test"}], output, audio_format="wav",
                    progress_callback=lambda p, m: progress.append(p))
        self.assertAlmostEqual(segments[0]["duration_seconds"], 1, places=1)
        self.assertEqual(progress, [90])

    @unittest.skipUnless(sys.platform == "win32", "Windows URI handling")
    def test_localhost_and_unc_uri(self):
        self.assertEqual(_path_from_reference("file://localhost/C:/a%20b/c.mp4"), "C:/a b/c.mp4")
        self.assertEqual(_path_from_reference("file://nas/share/c.mp4"), "//nas/share/c.mp4")


class ModelResponseTests(unittest.TestCase):
    def test_empty_and_truncated_results_rejected(self):
        for payload in ({}, {"choices": [{"message": {"content": ""}}]},
                        {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}):
            with self.assertRaises(ValueError):
                parse_chat_text(payload)

    def test_mimo_options_do_not_change_other_providers(self):
        self.assertEqual(text_generation_options("mimo-v2.5-pro", 2000),
                         {"max_completion_tokens": 2000, "thinking": {"type": "disabled"}})
        self.assertEqual(text_generation_options("other", 500), {"max_tokens": 500})

    def test_clients_send_text_mode_and_validate_responses(self):
        with patch("requests.post") as request:
            request.return_value.status_code = 200
            request.return_value.json.return_value = {"choices": [{"message": {"content": "valid result"}, "finish_reason": "stop"}]}
            actions = [lambda: OpenAICompatibleClient(api_key="test-key", model="mimo-test").chat([]),
                       lambda: AIRewriter(api_key="test-key", model="mimo-test").rewrite_with_prompt("test"),
                       lambda: TitleGenerator(api_key="test-key", model="mimo-test").generate_titles("test")]
            for action in actions:
                self.assertTrue(action())
                self.assertEqual(request.call_args.kwargs["json"]["thinking"], {"type": "disabled"})
            request.return_value.json.return_value = {"choices": [{"message": {"content": ""}}]}
            for action in actions:
                with self.assertRaises(ValueError):
                    action()


class RenderAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_render_api_")
        self.backend = WorkflowBackend(ProjectStore(self.tmp.name), run_inline_jobs=True)
        self.project_id = self.backend.create_project({"name": "render"})["status"]["project_id"]
        self.state = self.backend.store.get_state(self.project_id)
        self.state.set_reference_text("test")
        self.state.set_storyboard_shots([{"shot_id": "S1", "script_text": "test", "duration_seconds": 2}])
        video = self.state.project_dir / "source.mp4"
        video.write_bytes(b"test fixture")
        voice = self.state.project_dir / "voice.wav"
        voice.write_bytes(b"test fixture")
        self.state.set_material_assets([MaterialAsset(asset_id="A1", file_path=str(video), duration_seconds=2)])
        self.state.set_voiceover_segments([VoiceoverSegment(segment_id="V1", shot_id="S1", text="test", audio_path=str(voice), duration_seconds=2)])

        class FakeRenderer(FFmpegAssemblyRenderer):
            def render(self, plan, output_path, **options):
                options["progress_callback"](.5)
                output = Path(output_path)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"rendered fixture")
                return {"output_path": str(output), "duration_seconds": 2, "segment_count": 1}
        self.state.assembly_renderer = FakeRenderer()
        self.backend.store.save_project(self.project_id)

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_persists_result_and_project_relative_export(self):
        job = self.backend.render_assembly(self.project_id, {"output_path": "exports/test.mp4"})["job"]
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(job["progress"], 100)
        result = self.state.current_activity.metadata["render_result"]
        self.assertEqual(Path(result["output_path"]), self.state.project_dir / "exports" / "test.mp4")
        restored = ProjectStore(self.tmp.name).get_state(self.project_id)
        self.assertTrue(Path(restored.current_activity.metadata["render_result"]["output_path"]).exists())

    def test_outside_export_and_missing_voice_fail_without_replacing_state(self):
        job = self.backend.render_assembly(self.project_id, {"output_path": "../outside.mp4"})["job"]
        self.assertEqual(job["status"], "failed")
        self.assertFalse((Path(self.tmp.name) / "outside.mp4").exists())
        self.state.set_voiceover_segments([])
        job = self.backend.render_assembly(self.project_id, {})["job"]
        self.assertEqual(job["status"], "failed")
        self.assertIsNone(self.state.assembly_plan)

    def test_shot_validation_rejects_invalid_duration_and_missing_asset(self):
        for changes in ({"duration_seconds": float("nan")}, {"duration_seconds": -1},
                        {"metadata": {"material_asset_id": "missing"}}, {"script_text": ""}):
            shot = {"shot_id": "S1", "script_text": "test", "duration_seconds": 2, **changes}
            with self.assertRaises(ValueError):
                self.backend.set_storyboard_shots(self.project_id, {"shots": [shot]})
        self.assertEqual(self.state.storyboard_analysis.shots[0].script_text, "test")

    def test_empty_copywriting_does_not_commit(self):
        with patch("backend.workflow_api.AIRewriter") as rewriter:
            rewriter.return_value.rewrite_with_prompt.return_value = ""
            job = self.backend.rewrite_copy(self.project_id, {})["job"]
        self.assertEqual(job["status"], "failed")
        self.assertFalse(self.state.current_activity.copywriting)


if __name__ == "__main__":
    unittest.main()
