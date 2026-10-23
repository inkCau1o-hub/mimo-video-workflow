from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# macOS keeps temp dirs behind the /var -> /private/var symlink; stored project paths are resolved.
tempfile.tempdir = str(Path(tempfile.gettempdir()).resolve())

from material_segments import SegmentIndexer, normalize_observations, recommend_segments, sample_windows, source_signature
from media_render import RenderCancelled, discover_executable
from backend.jobs import JobRecord
from backend.project_store import ProjectStore
from backend.server import create_server
from backend.workflow_api import WorkflowBackend
from production_schema import MaterialAsset, VoiceoverSegment


def observation(identifier="clip_01", **changes):
    return {"segment_id": identifier, "summary": "卧室窗帘与书桌", "visual_tags": ["卧室", "窗帘"],
            "burned_in_subtitles": "yes", "confidence": .9, "uncertainties": [], **changes}


def index_fixture(path):
    windows = sample_windows(12, 8)
    segments = normalize_observations({"segments": [observation(item["segment_id"]) for item in windows]}, windows)
    return {"version": 1, "index_id": "index1", "source_signature": source_signature(path),
            "duration_seconds": 12, "coverage_ratio": 1, "segments": segments}


class WindowTests(unittest.TestCase):
    def test_windows_cover_ends_without_exceeding_source_or_count(self):
        for duration in (.2, 4, 8, 11.73333, 60, 3600):
            windows = sample_windows(duration)
            self.assertLessEqual(len(windows), 8)
            self.assertEqual(windows[0]["source_in"], 0)
            self.assertAlmostEqual(windows[-1]["source_out"], duration, places=5)
            for window in windows:
                self.assertLess(window["source_in"], window["source_out"])
                self.assertLessEqual(window["source_out"], duration + .000001)
                self.assertTrue(all(window["source_in"] < t < window["source_out"] for t in window["sample_times"]))

    def test_invalid_window_options_reject_before_processing(self):
        for duration, window in ((0, 8), (float("nan"), 8), (True, 8), (12, 0), (12, 31), (12, float("inf"))):
            with self.subTest(duration=duration, window=window), self.assertRaises(ValueError):
                sample_windows(duration, window)

    def test_model_cannot_change_ranges_or_frame_paths(self):
        windows = sample_windows(4)
        windows[0]["thumbnail_path"] = "trusted.jpg"
        row = observation(source_in=99, source_out=400, thumbnail_path="/private.txt", sample_times=[99])
        result = normalize_observations({"segments": [row]}, windows)[0]
        self.assertEqual(result["source_in"], 0)
        self.assertEqual(result["source_out"], 4)
        self.assertEqual(result["thumbnail_path"], "trusted.jpg")
        self.assertEqual(result["sample_times"], windows[0]["sample_times"])

    def test_partial_duplicate_and_malformed_model_output_is_rejected(self):
        windows = sample_windows(12)
        bad = ["not JSON", {}, {"segments": [observation()]}, {"segments": [observation(), observation()]},
               {"segments": [observation(), observation("clip_02", confidence=float("nan"))]},
               {"segments": [observation(), observation("clip_02", burned_in_subtitles=True)]},
               {"segments": [observation(), observation("clip_02", visual_tags="窗帘")]},
               {"segments": [observation(), observation("clip_02", summary="")]},
               {"segments": [observation(), observation("clip_02", uncertainties=[{}])]}]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_observations(raw, windows)


class ObservationRetryTests(unittest.TestCase):
    def setUp(self):
        self.windows = sample_windows(12)
        for window in self.windows:
            window["frame_paths"] = [window["segment_id"] + f"_{i}.jpg" for i in range(3)]
        self.client = Mock(model="offline")
        self.indexer = SegmentIndexer(self.client)

    def test_incomplete_batch_retries_each_window_once_with_only_its_frames(self):
        self.client.analyze_images.side_effect = [
            {"segments": [observation()]}, {"segments": [observation()]},
            {"segments": [observation("clip_02")]},
        ]
        result, count = self.indexer._observe_complete(self.windows)
        self.assertEqual(count, 3)
        self.assertEqual([item["segment_id"] for item in result], ["clip_01", "clip_02"])
        self.assertEqual(self.client.analyze_images.call_args_list[1].args[1], self.windows[0]["frame_paths"])
        self.assertEqual(self.client.analyze_images.call_args_list[2].args[1], self.windows[1]["frame_paths"])

    def test_bad_single_result_stops_without_infinite_retries(self):
        self.client.analyze_images.return_value = {"segments": []}
        with self.assertRaises(ValueError):
            self.indexer._observe_complete(self.windows)
        self.assertEqual(self.client.analyze_images.call_count, 2)

    def test_network_failure_does_not_trigger_additional_calls(self):
        self.client.analyze_images.side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            self.indexer._observe_complete(self.windows)
        self.assertEqual(self.client.analyze_images.call_count, 1)

    def test_cancellation_prevents_fallback_requests(self):
        self.client.analyze_images.return_value = {"segments": []}
        cancel = Mock(side_effect=[None, None, RenderCancelled()])
        with self.assertRaises(RenderCancelled):
            self.indexer._observe_complete(self.windows, cancel_check=cancel)
        self.assertEqual(self.client.analyze_images.call_count, 1)


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "素材.mp4"
        self.path.write_bytes(b"original")
        self.asset = {"asset_id": "m1", "file_path": str(self.path), "visual_tags": ["手机", "APP", "认证"],
                      "metadata": {"segment_index": index_fixture(self.path)}}
        self.shot = {"shot_id": "s1", "duration_seconds": 4, "visual_tags": ["卧室", "窗帘"], "material_requirements": []}

    def recommend(self, **changes):
        return recommend_segments(changes.pop("shot", self.shot), changes.pop("assets", [self.asset]), changes.pop("target", 4), **changes)

    def test_no_whole_video_tag_leak_and_missing_material_is_explicit(self):
        result = self.recommend(shot={**self.shot, "visual_tags": ["手机", "APP"]})
        self.assertTrue(result["needs_material"])
        self.assertEqual(result["candidates"][0]["matched_terms"], [])
        self.assertTrue(result["candidates"][0]["requires_confirmation"])

    def test_shared_noun_does_not_prove_phone_control_or_compound_requirement(self):
        result = self.recommend(shot={**self.shot, "material_requirements": ["手机APP控制窗帘开合的演示"]})
        self.assertIn("手机app控制窗帘开合的演示", result["candidates"][0]["missing_terms"])
        self.assertTrue(result["needs_material"])

    def test_supported_terms_and_target_range_are_reported(self):
        result = self.recommend()
        self.assertFalse(result["needs_material"])
        first = result["candidates"][0]
        self.assertEqual(first["matched_terms"], ["卧室", "窗帘"])
        self.assertEqual(first["source_out"] - first["source_in"], 4)
        self.assertEqual(first["available_seconds"], 8)
        self.assertIn("原素材带字幕，新增字幕可能重复", first["warnings"])

    def test_insufficient_duration_requires_confirmation_and_does_not_extend_range(self):
        result = self.recommend(target=9)
        self.assertTrue(result["needs_material"])
        self.assertEqual(result["candidates"][0]["shortage_seconds"], 1)
        self.assertEqual(result["candidates"][0]["source_out"], 8)

    def test_unknown_subtitles_confidence_and_uncertainties_are_not_hidden(self):
        for segment in self.asset["metadata"]["segment_index"]["segments"]:
            segment.update(confidence=.2, burned_in_subtitles="unknown", uncertainties=["动作无法确认"])
        first = self.recommend()["candidates"][0]
        self.assertTrue(first["requires_confirmation"])
        self.assertIn("原字幕情况不确定", first["warnings"])
        self.assertEqual(first["uncertainties"], ["动作无法确认"])

    def test_partial_unindexed_and_unavailable_assets_are_reported(self):
        self.asset["metadata"]["segment_index"]["coverage_ratio"] = .5
        result = self.recommend(assets=[self.asset, {"asset_id": "m2"}])
        self.assertEqual(result["partial_assets"], ["m1"])
        self.assertEqual(result["unindexed_assets"], ["m2"])
        result = self.recommend(availability=lambda *_: "missing")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["unavailable_assets"], [{"asset_id": "m1", "reason": "missing"}])

    def test_query_and_numeric_input_limits(self):
        for changes in ({"query": "x" * 201}, {"query": []}, {"limit": 11}, {"limit": True}, {"target": float("nan")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.recommend(**changes)
        self.assertTrue(self.recommend(query="手机")["needs_material"])


class BackendSegmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = WorkflowBackend(ProjectStore(self.temp.name), run_inline_jobs=True)
        self.pid = self.backend.create_project({"name": "segments"})["status"]["project_id"]
        self.state = self.backend.store.get_state(self.pid)
        self.path = self.state.project_dir / "素材 空格.mp4"
        self.path.write_bytes(b"original")
        self.state.set_material_assets([MaterialAsset(asset_id="m1", file_path=str(self.path))])
        self.shot = {"shot_id": "s1", "tts_text": "窗帘", "duration_seconds": 4, "visual_tags": ["窗帘"], "metadata": {"material_asset_id": "m1"}}
        self.state.set_storyboard_shots([self.shot])
        self.state.set_voiceover_segments([VoiceoverSegment(shot_id="s1", segment_id="v1", text="窗帘", audio_path="voice.wav", duration_seconds=3)])
        self.backend.store.save_project(self.pid)

    def index(self):
        with patch("backend.workflow_api.SegmentIndexer") as cls:
            cls.return_value.analyze.return_value = index_fixture(self.path)
            return self.backend.index_material_segments(self.pid, {"asset_ids": ["m1"], "window_seconds": 8})

    def test_index_commits_metadata_and_preserves_shots_audio_and_exports(self):
        self.state.current_activity.metadata["exports"] = [{"output_path": "old.mp4"}]
        self.backend.store.save_project(self.pid)
        result = self.index()["job"]
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        self.assertEqual(result["result"]["segment_count"], 2)
        self.assertEqual(self.state.material_assets[0].duration_seconds, 12)
        self.assertEqual(self.state.storyboard_analysis.shots[0].tts_text, "窗帘")
        self.assertEqual(len(self.state.voiceover_segments), 1)
        self.assertEqual(len(self.state.current_activity.metadata["exports"]), 1)

    def test_failure_and_cancel_do_not_replace_existing_index(self):
        self.index()
        before = deepcopy(self.state.to_dict())
        for failure in (ValueError("bad analysis"), RenderCancelled()):
            with patch("backend.workflow_api.SegmentIndexer") as cls:
                cls.return_value.analyze.side_effect = failure
                result = self.backend.index_material_segments(self.pid, {"asset_ids": ["m1"]})["job"]
                self.assertIn(result["status"], ("failed", "cancelled"))
                self.assertEqual(self.state.to_dict(), before)

    def test_invalid_selection_and_other_activity_are_rejected(self):
        for payload in ({"asset_ids": []}, {"asset_ids": ["bad"]}, {"asset_ids": ["m1", "m1"]}, {"asset_ids": ["m1"], "activity_id": "other"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.backend.index_material_segments(self.pid, payload)

    def test_existing_job_blocks_indexing(self):
        record = JobRecord("active", "test", project_id=self.pid, activity_id=self.state.current_activity.activity_id)
        self.backend.jobs._jobs[record.job_id] = record
        with self.assertRaises(ValueError):
            self.backend.index_material_segments(self.pid, {"asset_ids": ["m1"]})

    def test_query_rejects_changed_and_missing_source(self):
        self.index()
        payload = {"shot": self.shot, "target_duration": 4}
        self.assertTrue(self.backend.recommend_material_segments(self.pid, payload)["candidates"])
        self.path.write_bytes(b"changed!")
        result = self.backend.recommend_material_segments(self.pid, payload)
        self.assertEqual(result["candidates"], [])
        self.assertIn("已变化", result["unavailable_assets"][0]["reason"])
        self.path.unlink()
        result = self.backend.recommend_material_segments(self.pid, payload)
        self.assertIn("缺失", result["unavailable_assets"][0]["reason"])

    def test_index_references_survive_chinese_directory_move(self):
        self.index()
        index = self.state.material_assets[0].metadata["segment_index"]
        frame = self.state.project_dir / "frames" / "frame.jpg"
        frame.parent.mkdir()
        frame.write_bytes(b"frame")
        index["segments"][0]["thumbnail_path"] = str(frame)
        index["segments"][0]["frame_paths"] = [str(frame)]
        self.backend.store.save_project(self.pid)
        destination = Path(self.temp.name) / "搬迁 空格" / self.state.project_dir.name
        destination.parent.mkdir()
        shutil.move(str(self.state.project_dir), destination)
        other = WorkflowBackend(ProjectStore(Path(self.temp.name) / "new-index"))
        other.open_project({"path": str(destination)})
        result = other.recommend_material_segments(self.pid, {"shot": self.shot, "target_duration": 4})
        self.assertTrue(result["candidates"])
        self.assertEqual(Path(result["candidates"][0]["thumbnail_path"]), destination / "frames" / "frame.jpg")


class SegmentHTTPTests(unittest.TestCase):
    def test_index_and_recommendation_routes_and_activity_validation(self):
        with tempfile.TemporaryDirectory(prefix="segments_http_") as root:
            server = create_server(port=0, projects_dir=root, quiet=True, run_inline_jobs=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                pid = server.backend.create_project({"name": "api"})["status"]["project_id"]
                state = server.backend.store.get_state(pid)
                path = state.project_dir / "source.mp4"
                path.write_bytes(b"source")
                state.set_material_assets([MaterialAsset(asset_id="m1", file_path=str(path))])
                server.backend.store.save_project(pid)

                def post(route, payload):
                    url = f"http://127.0.0.1:{server.server_port}/projects/{pid}/materials/{route}"
                    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(request) as response:
                        return response.status, json.load(response)

                with patch("backend.workflow_api.SegmentIndexer") as cls:
                    cls.return_value.analyze.return_value = index_fixture(path)
                    status, result = post("index", {"asset_ids": ["m1"]})
                    self.assertEqual(status, 202)
                    self.assertEqual(result["job"]["status"], "succeeded")
                status, result = post("recommendations", {"shot": {"shot_id": "s1", "visual_tags": ["窗帘"]}, "target_duration": 4})
                self.assertEqual(status, 200)
                self.assertEqual(len(result["candidates"]), 2)
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    post("recommendations", {"activity_id": "wrong", "shot": {"shot_id": "s1"}})
                self.assertEqual(raised.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


class RealSamplingTests(unittest.TestCase):
    def test_real_ffmpeg_samples_and_validates_index_without_network(self):
        with tempfile.TemporaryDirectory(prefix="segment_media_") as directory:
            root = Path(directory)
            path = root / "sample.mp4"
            ffmpeg = discover_executable("ffmpeg")
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:s=160x120:r=10:d=2", "-c:v", "libx264", str(path)], check=True)
            client = Mock(model="offline")
            client.analyze_images.return_value = json.dumps({"segments": [observation()]}, ensure_ascii=False)
            result = SegmentIndexer(client).analyze(path, root / "frames")
            self.assertEqual(result["coverage_ratio"], 1)
            self.assertEqual(len(result["segments"]), 1)
            self.assertEqual(result["source_signature"], source_signature(path))
            frames = result["segments"][0]["frame_paths"]
            self.assertEqual(len(frames), 3)
            for frame in frames:
                self.assertGreater(Path(frame).stat().st_size, 100)
                pixels = subprocess.run([ffmpeg, "-v", "error", "-i", frame, "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, check=True).stdout
                self.assertGreater(pixels[0], 180)
                self.assertLess(pixels[1], 50)
            self.assertIn("不能根据画面上的广告字幕", client.analyze_images.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
