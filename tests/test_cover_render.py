import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backend.project_store import ProjectStore
from backend.workflow_api import WorkflowBackend
from cover_render import render_cover, validate_cover_settings
from media_render import RenderCancelled, RenderError, discover_executable
from media_render import _probe


class CoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = discover_executable("ffmpeg")
        cls.temp = tempfile.TemporaryDirectory(prefix="mimo covers ")
        cls.source = Path(cls.temp.name) / "source.mp4"
        subprocess.run([cls.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=red:s=360x640:r=20:d=1,drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='gte(t,0.5)'",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.source)], check=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="cover case ", dir=self.temp.name)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def pixels(self, path):
        return subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path),
                               "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30).stdout

    def test_first_and_last_frame_png_without_font_or_source_changes(self):
        before = hashlib.sha256(self.source.read_bytes()).digest()
        for stamp, channel, index in ((0, 0, 0), (.999, 2, 19)):
            output = self.root / f"{index}.png"
            with patch("cover_render.discover_font", side_effect=AssertionError("unneeded font")):
                result = render_cover(self.source, output, {"time_seconds": stamp})
            self.assertEqual((result["width"], result["height"]), (360, 640))
            self.assertEqual(result["frame_index"], index)
            self.assertAlmostEqual(result["frame_time_seconds"], index / 20)
            self.assertGreater(self.pixels(output)[channel], 240)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), before)

    def test_literal_chinese_title_changes_pixels_in_selected_position(self):
        plain, titled = self.root / "plain.png", self.root / "titled.png"
        render_cover(self.source, plain, {})
        render_cover(self.source, titled, {"title": "杜亚 {\\pos(0,0)}\n智能窗帘", "position": "top"})
        a, b = self.pixels(plain), self.pixels(titled)
        self.assertNotEqual(a[:360 * 250 * 3], b[:360 * 250 * 3])
        self.assertEqual(a[360 * 400 * 3:], b[360 * 400 * 3:])

    def test_exact_boundary_uses_nominal_fps_not_rounded_average(self):
        def rounded_probe(*args):
            info = _probe(*args)
            for stream in info["streams"]:
                if stream.get("codec_name") == "h264":
                    stream["avg_frame_rate"] = "20000/1001"
            return info
        output = self.root / "boundary.png"
        with patch("cover_render._probe", side_effect=rounded_probe):
            result = render_cover(self.source, output, {"time_seconds": .5})
        self.assertEqual(result["frame_index"], 10)
        self.assertEqual(result["frame_time_seconds"], .5)
        self.assertGreater(self.pixels(output)[2], 240)

    def test_reject_invalid_settings_out_of_range_and_overwrite(self):
        for settings in ({"title": "a" * 81}, {"title": "a\nb\nc\nd"}, {"time_seconds": True},
                         {"time_seconds": float("nan")}, {"font_size": 97}, {"color": "red"},
                         {"position": "unsafe"}, {"background": 1}, {"extra": 1}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                validate_cover_settings(settings)
        output = self.root / "kept.png"
        with self.assertRaises(ValueError):
            render_cover(self.source, output, {"time_seconds": 1})
        self.assertFalse(output.exists())
        output.write_bytes(b"keep")
        with self.assertRaises(ValueError):
            render_cover(self.source, output, {})
        self.assertEqual(output.read_bytes(), b"keep")

    def test_cancel_before_publish_removes_temporary_artifacts(self):
        output = self.root / "cancelled.png"
        state = {"cancel": False}
        def progress(value):
            if value >= .95:
                state["cancel"] = True
        with self.assertRaises(RenderCancelled):
            render_cover(self.source, output, {}, cancel_check=lambda: state["cancel"], progress_callback=progress)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob(".mimo-cover-*")))

    def test_api_only_current_activity_exports_and_portable_reopen(self):
        backend = WorkflowBackend(ProjectStore(self.root), run_inline_jobs=True)
        pid = backend.create_project({"name": "cover"})["status"]["project_id"]
        state = backend.store.get_state(pid)
        aid = state.current_activity.activity_id
        source = state.project_dir / "export.mp4"
        shutil.copyfile(self.source, source)
        payload = {"activity_id": aid, "source_video_path": str(source), "settings": {"time_seconds": .55}}
        with self.assertRaises(ValueError):
            backend.generate_cover(pid, payload)
        state.current_activity.metadata["exports"] = [{"output_path": str(source)}]
        state.current_activity.metadata["render_result"] = {"output_path": str(source)}
        backend.store.save_project(pid)
        with self.assertRaises(ValueError):
            backend.generate_cover(pid, {**payload, "activity_id": "wrong"})
        job = backend.generate_cover(pid, payload)["job"]
        self.assertEqual(job["status"], "succeeded", job)
        self.assertTrue(Path(job["result"]["image_path"]).exists())
        self.assertEqual(state.current_activity.metadata["render_result"]["output_path"], str(source))
        moved = self.root / "搬迁 空格" / state.project_dir.name
        shutil.copytree(state.project_dir, moved)
        backend.open_project({"path": str(moved)})
        reopened = backend.store.get_state(pid)
        cover = reopened.current_activity.metadata["covers"][0]
        for key in ("image_path", "source_video_path"):
            self.assertTrue(Path(cover[key]).is_relative_to(moved))
            self.assertTrue(Path(cover[key]).exists())
        again = backend.generate_cover(pid, {**payload, "source_video_path": cover["source_video_path"]})["job"]
        self.assertEqual(again["status"], "succeeded", again)
        before = reopened.project.to_dict()
        with patch("backend.workflow_api.render_cover", side_effect=RenderError("failed", "fixture")):
            failed = backend.generate_cover(pid, {**payload, "source_video_path": cover["source_video_path"]})["job"]
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(reopened.project.to_dict(), before)
