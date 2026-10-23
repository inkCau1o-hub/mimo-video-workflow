import math
import subprocess
import sys
import tempfile
import unittest
from array import array
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# macOS keeps temp dirs behind the /var -> /private/var symlink; stored project paths are resolved.
tempfile.tempdir = str(Path(tempfile.gettempdir()).resolve())

from backend.project_store import ProjectStore
from backend.workflow_api import WorkflowBackend
from media_render import discover_executable
from production_schema import MaterialAsset, VoiceoverSegment, WorkflowActivity

# A no-narration cut keeps these durations exactly: 30 + 15 + 45 frames at 30 fps.
SHOTS = [
    {"shot_id": "s1", "tts_text": "一", "duration_seconds": 1.0, "metadata": {"material_asset_id": "m1"}},
    {"shot_id": "s2", "tts_text": "二", "duration_seconds": 0.5, "metadata": {"material_asset_id": "m2"}},
    {"shot_id": "s3", "tts_text": "三", "duration_seconds": 1.5, "metadata": {"material_asset_id": "m1"}},
]


def voiced(directory=""):
    return [VoiceoverSegment(shot_id=shot["shot_id"], audio_path=str(Path(directory) / f"{shot['shot_id']}.wav"),
                             duration_seconds=2.0) for shot in SHOTS]


class NarrationModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="narration_")
        self.addCleanup(self.temp.cleanup)
        self.backend = WorkflowBackend(ProjectStore(self.temp.name), run_inline_jobs=True)
        self.pid = self.backend.create_project({"name": "无旁白"})["status"]["project_id"]
        self.state = self.backend.store.get_state(self.pid)
        self.aid = self.state.current_activity.activity_id
        self.state.set_storyboard_shots(SHOTS)
        self.state.set_material_assets([MaterialAsset(asset_id="m1", file_path="media/a.mp4"),
                                        MaterialAsset(asset_id="m2", file_path="media/b.mp4")])
        self.backend.store.save_project(self.pid)

    def narration(self, mode):
        return self.backend.update_activity(self.pid, self.aid, {"narration_mode": mode})

    def test_activity_defaults_to_voiceover_and_mode_survives_reopen(self):
        self.assertEqual(WorkflowActivity().narration_mode, "voiceover")
        self.assertEqual(WorkflowActivity.from_dict({}).narration_mode, "voiceover")  # projects saved before the field
        self.assertEqual(self.narration("none")["activity"]["narration_mode"], "none")
        reopened = WorkflowBackend(ProjectStore(self.temp.name)).store.get_state(self.pid)
        self.assertEqual(reopened.current_activity.narration_mode, "none")

    def test_unknown_mode_is_rejected_and_renaming_keeps_the_mode(self):
        self.narration("none")
        with self.assertRaises(ValueError):
            self.narration("music")
        self.backend.update_activity(self.pid, self.aid, {"name": "改名"})
        self.assertEqual(self.state.current_activity.narration_mode, "none")

    def test_plan_without_narration_keeps_storyboard_durations(self):
        self.state.set_voiceover_segments(voiced())
        with_voice = self.state.build_assembly_plan(save=False)
        self.assertEqual([item.duration_seconds for item in with_voice.items], [2.0, 2.0, 2.0])
        self.narration("none")
        plan = self.state.build_assembly_plan(save=False)
        self.assertEqual([item.duration_seconds for item in plan.items], [1.0, 0.5, 1.5])
        self.assertTrue(all(not item.voiceover_segment_id for item in plan.items))
        self.assertEqual(self.state.current_activity.status["voiceover"], "skipped")

    def test_export_without_narration_needs_no_voiceover_and_cannot_be_forced_on(self):
        with patch("backend.workflow_api.FFmpegAssemblyRenderer") as renderer:
            renderer.return_value.render.return_value = {"output_path": "out.mp4"}
            refused = self.backend.render_assembly(self.pid, {})["job"]
            self.assertEqual(refused["status"], "failed")
            self.assertIn("缺少有效配音", refused["error"])
            self.narration("none")
            job = self.backend.render_assembly(self.pid, {"include_voiceover": True})["job"]
            self.assertEqual(job["status"], "succeeded", job.get("error"))
            self.assertIs(renderer.return_value.render.call_args.kwargs["include_voiceover"], False)


class RealNarrationRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = discover_executable("ffmpeg")
        cls.ffprobe = discover_executable("ffprobe", sibling=Path(cls.ffmpeg))

    def ff(self, *args):
        return subprocess.run([self.ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-y", *args],
                              capture_output=True, check=True).stdout

    def tone(self, path, start, frequency):
        values = array("f", self.ff("-ss", str(start), "-i", str(path), "-t", "0.5", "-map", "0:a:0",
                                    "-af", "pan=mono|c0=c0", "-ar", "48000", "-f", "f32le", "pipe:1"))
        real = sum(value * math.cos(i * 2 * math.pi * frequency / 48000) for i, value in enumerate(values))
        imag = sum(value * math.sin(i * 2 * math.pi * frequency / 48000) for i, value in enumerate(values))
        return 2 * math.hypot(real, imag) / len(values)

    def test_music_only_cut_is_frame_exact_and_drops_existing_narration(self):
        temp = tempfile.TemporaryDirectory(prefix="narration_render_")
        self.addCleanup(temp.cleanup)
        backend = WorkflowBackend(ProjectStore(temp.name), run_inline_jobs=True)
        pid = backend.create_project({"name": "图片预演"})["status"]["project_id"]
        state = backend.store.get_state(pid)
        aid = state.current_activity.activity_id
        media = state.project_dir / "media"
        media.mkdir(parents=True, exist_ok=True)
        self.ff("-f", "lavfi", "-i", "color=blue:s=320x180:r=30:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(media / "a.mp4"))
        self.ff("-f", "lavfi", "-i", "color=red:s=320x180:r=30:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(media / "b.mp4"))
        for shot in SHOTS:  # 800 Hz narration that must not reach the cut
            self.ff("-f", "lavfi", "-i", "sine=frequency=800:sample_rate=48000:duration=2",
                    str(state.project_dir / f"{shot['shot_id']}.wav"))
        self.ff("-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=4", str(Path(temp.name) / "music.wav"))
        state.set_storyboard_shots(SHOTS)
        state.set_material_assets([MaterialAsset(asset_id="m1", file_path=str(media / "a.mp4")),
                                   MaterialAsset(asset_id="m2", file_path=str(media / "b.mp4"))])
        state.set_voiceover_segments(voiced(state.project_dir))
        backend.store.save_project(pid)
        music = backend.upload_music(pid, {"activity_id": aid, "files": [
            {"filename": "music.wav", "content": (Path(temp.name) / "music.wav").read_bytes()}]})["uploaded_music"]
        backend.save_audio_settings(pid, {"activity_id": aid, "base_revision": "", "audio_settings": {
            "music_path": music["file_path"], "music_gain_db": -6, "fade_in_seconds": 0, "fade_out_seconds": 0}})
        backend.update_activity(pid, aid, {"narration_mode": "none"})

        job = backend.render_assembly(pid, {"aspect_ratio": "16:9", "width": 320, "height": 180, "subtitles": False})["job"]
        self.assertEqual(job["status"], "succeeded", job.get("error"))
        output = job["result"]["output_path"]
        frames = subprocess.run([self.ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
                                 "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", output],
                                capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(frames, "90")
        music_level, voice_level = self.tone(output, 1.0, 220), self.tone(output, 1.0, 800)
        self.assertGreater(music_level, .01)
        self.assertLess(voice_level, music_level * .02)


if __name__ == "__main__":
    unittest.main()
