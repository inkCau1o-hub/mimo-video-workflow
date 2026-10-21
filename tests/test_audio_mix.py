from array import array
from copy import deepcopy
import io
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
import wave
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audio_mix import DEFAULT_AUDIO_SETTINGS, validate_audio_settings, voice_processing
from backend.jobs import JobRecord
from backend.project_store import ProjectStore
from backend.server import create_server
from backend.workflow_api import WorkflowBackend
from media_assembler import FFmpegAssemblyRenderer
from media_render import RenderError, discover_executable
from production_schema import MaterialAsset, VoiceoverSegment
from workflow_state import WorkflowState


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(array("h", (int(2000 * math.sin(i * 2 * math.pi * 220 / 8000)) for i in range(8000))).tobytes())
    return buffer.getvalue()


class AudioSettingsTests(unittest.TestCase):
    def test_old_projects_have_no_processing_or_music_by_default(self):
        self.assertEqual(validate_audio_settings(), DEFAULT_AUDIO_SETTINGS)
        self.assertFalse(validate_audio_settings()["normalize_voice"])

    def test_invalid_options_are_rejected(self):
        values = [[], "music", {"unknown": True}, {"normalize_voice": "false"}, {"duck_music": 1},
                  {"loop_music": None}, {"voice_gain_db": True}, {"voice_gain_db": float("nan")},
                  {"music_gain_db": 1}, {"target_lufs": -25}, {"fade_in_seconds": -1},
                  {"fade_out_seconds": float("inf")}, {"music_path": []}, {"music_path": "bad\npath"}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_audio_settings(value)

    def test_silent_loudness_does_not_boost_noise_or_emit_nonfinite_json(self):
        runner = Mock()
        runner.run.return_value = json.dumps({"input_i": "-inf", "input_tp": "-inf", "input_lra": "0", "input_thresh": "-70", "target_offset": "inf"}).encode()
        filters, metrics = voice_processing(runner, "ffmpeg", {"path": "silence.wav", "start": 0, "available": 2},
                                           validate_audio_settings({"normalize_voice": True}), Path.cwd())
        self.assertEqual(filters, [])
        self.assertTrue(metrics["silent_or_too_quiet"])
        json.dumps(metrics, allow_nan=False)

    def test_malformed_loudness_response_is_redacted(self):
        runner = Mock()
        runner.run.return_value = b"private path and credentials: not-json"
        with self.assertRaises(RenderError) as raised:
            voice_processing(runner, "ffmpeg", {"path": "secret.wav", "start": 0, "available": 2},
                             validate_audio_settings({"normalize_voice": True}), Path.cwd())
        self.assertNotIn("private", str(raised.exception))


class BackendAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="audio_backend_")
        self.addCleanup(self.temp.cleanup)
        self.backend = WorkflowBackend(ProjectStore(self.temp.name), run_inline_jobs=True)
        self.pid = self.backend.create_project({"name": "audio"})["status"]["project_id"]
        self.state = self.backend.store.get_state(self.pid)
        self.aid = self.state.current_activity.activity_id
        self.state.set_material_assets([MaterialAsset(asset_id="m1", file_path="source.mp4")])
        self.state.set_storyboard_shots([{"shot_id": "s1", "tts_text": "Voice", "duration_seconds": 4}])
        self.state.set_voiceover_segments([VoiceoverSegment(shot_id="s1", audio_path="voice.wav")])
        self.state.current_activity.metadata.update(render_result={"output_path": "old.mp4"}, exports=[{"output_path": "old.mp4"}])
        self.backend.store.save_project(self.pid)

    def upload(self, filename="背景 音乐.wav", content=None, **extra):
        return self.backend.upload_music(self.pid, {"activity_id": self.aid, "files": [
            {"filename": filename, "content": wav_bytes() if content is None else content}], **extra})

    def save(self, settings, **extra):
        return self.backend.save_audio_settings(self.pid, {"activity_id": self.aid, "audio_settings": settings,
            "base_revision": self.state.current_activity.metadata.get("audio_revision", ""), **extra})

    def test_save_preserves_voice_shots_history_and_invalidates_only_current_output(self):
        voices = deepcopy(self.state.voiceover_segments)
        shots = deepcopy(self.state.storyboard_analysis)
        self.save({"normalize_voice": True})
        self.assertEqual(self.state.voiceover_segments, voices)
        self.assertEqual(self.state.storyboard_analysis, shots)
        self.assertNotIn("render_result", self.state.current_activity.metadata)
        self.assertEqual(len(self.state.current_activity.metadata["exports"]), 1)
        self.assertTrue(self.state.current_activity.metadata["audio_revision"])

    def test_conflict_and_activity_switch_do_not_overwrite(self):
        self.save({"normalize_voice": True})
        before = self.state.to_dict()
        for payload in ({"base_revision": "old"}, {"activity_id": "other"}):
            with self.assertRaises(ValueError):
                self.save({"normalize_voice": False}, **payload)
            self.assertEqual(self.state.to_dict(), before)

    def test_save_failure_restores_project(self):
        before = self.state.to_dict()
        with patch.object(self.backend.store, "save_project", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.save({"normalize_voice": True})
        self.assertEqual(self.state.to_dict(), before)

    def test_missing_settings_payload_cannot_reset_saved_settings(self):
        self.save({"normalize_voice": True})
        before = self.state.to_dict()
        with self.assertRaises(ValueError):
            self.backend.save_audio_settings(self.pid, {"base_revision": self.state.current_activity.metadata["audio_revision"]})
        self.assertEqual(self.state.to_dict(), before)

    def test_upload_copies_real_audio_and_does_not_change_current_mix(self):
        first = self.upload()["uploaded_music"]
        second = self.upload()["uploaded_music"]
        self.assertNotEqual(first["file_path"], second["file_path"])
        self.assertEqual(Path(first["file_path"]).read_bytes(), wav_bytes())
        self.assertEqual(first["duration_seconds"], 1)
        self.assertIn("render_result", self.state.current_activity.metadata)
        self.assertNotIn("audio_settings", self.state.current_activity.metadata)

    def test_corrupt_and_unsupported_uploads_leave_no_asset_or_file(self):
        before = self.state.to_dict()
        for filename, content in (("bad.wav", b"bad"), ("bad.exe", b"bad"), ("empty.wav", b""),
                                   ("playlist.mp3", b"#EXTM3U\nhttps://example.invalid/music\n")):
            with self.subTest(filename=filename), self.assertRaises((ValueError, RenderError)):
                self.upload(filename, content)
            self.assertEqual(self.state.to_dict(), before)
        self.assertEqual(list((self.state.project_dir / "media/music").glob("*")), [])

    def test_upload_disk_failure_rolls_back_asset_and_owned_file(self):
        before = self.state.to_dict()
        with patch.object(self.backend.store, "save_project", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.upload()
        self.assertEqual(self.state.to_dict(), before)
        self.assertEqual(list((self.state.project_dir / "media/music").glob("*")), [])

    def test_busy_activity_blocks_save_and_upload(self):
        record = JobRecord("running", "assembly.render", self.pid, self.aid, status="running")
        self.backend.jobs._jobs[record.job_id] = record
        with self.assertRaises(ValueError):
            self.save({"normalize_voice": True})
        with self.assertRaises(ValueError):
            self.upload()

    def test_external_or_missing_music_is_rejected_before_save_or_render(self):
        outside = Path(self.temp.name) / "outside.wav"
        outside.write_bytes(wav_bytes())
        for path in (str(outside), "missing.wav", "https://example.invalid/music.wav"):
            with self.subTest(path=path), self.assertRaises((ValueError, FileNotFoundError)):
                self.save({"music_path": path})
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.backend.render_assembly(self.pid, {"audio_settings": {"music_path": path}})

    def test_saved_settings_reach_renderer_and_stale_export_is_rejected(self):
        self.save({"normalize_voice": True, "voice_gain_db": -2})
        with patch("backend.workflow_api.FFmpegAssemblyRenderer") as cls:
            cls.return_value.render.return_value = {"output_path": "out.mp4"}
            result = self.backend.render_assembly(self.pid, {})["job"]
            self.assertEqual(result["status"], "succeeded", result.get("error"))
            self.assertTrue(cls.return_value.render.call_args.kwargs["audio_settings"]["normalize_voice"])
        with self.assertRaises(ValueError):
            self.backend.render_assembly(self.pid, {"audio_revision": "old"})

    def test_music_settings_and_assets_survive_move_and_reopen(self):
        item = self.upload()["uploaded_music"]
        self.save({"music_path": item["file_path"], "normalize_voice": True})
        moved = Path(self.temp.name) / "搬迁 空格" / self.state.project_dir.name
        shutil.copytree(self.state.project_dir, moved)
        loaded = WorkflowState.load_project(moved)
        settings = loaded.current_activity.metadata["audio_settings"]
        path = Path(settings["music_path"])
        self.assertTrue(path.is_relative_to(moved) and path.is_file())
        self.assertEqual(loaded.current_activity.metadata["music_assets"][0]["file_path"], str(path))


class RealAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="mix_media_")
        cls.root = Path(cls.temp.name)
        cls.ffmpeg = discover_executable("ffmpeg")
        cls.video = cls.root / "source.mp4"
        cls.music = cls.root / "音乐 空格 'test'.wav"
        cls.voice = cls.root / "voice.wav"
        cls.quiet = cls.root / "quiet.wav"
        cls.loud = cls.root / "loud.wav"
        cls.ff(["-f", "lavfi", "-i", "color=green:s=160x90:r=20:d=6", "-c:v", "libx264", "-preset", "ultrafast", str(cls.video)])
        cls.ff(["-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=1", str(cls.music)])
        cls.ff(["-f", "lavfi", "-i", "sine=frequency=800:sample_rate=48000:duration=6",
                "-af", "volume='if(between(t,2,4),2,0)':eval=frame", str(cls.voice)])
        for path, volume in ((cls.quiet, ".1"), (cls.loud, "1")):
            cls.ff(["-f", "lavfi", "-i", "sine=frequency=800:sample_rate=48000:duration=3", "-af", "volume=" + volume, str(path)])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def ff(cls, args):
        return subprocess.run([cls.ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-y", *args], capture_output=True, check=True).stdout

    def render(self, name, sound, voices=None, **kwargs):
        voices = [self.voice] if voices is None else voices
        rows = [{"video_clip": {"source_path": str(self.video)},
                 "voiceover_clip": {"source_path": str(voice)} if voice else None,
                 "duration": 6 / len(voices)} for voice in voices]
        return FFmpegAssemblyRenderer(self.ffmpeg).render({"segments": rows}, str(self.root / name),
            dry_run=False, resolution=(160, 90), fps=20, subtitles=False, preset="ultrafast", audio_settings=sound, **kwargs)

    def samples(self, path, start, duration):
        return array("f", self.ff(["-ss", str(start), "-i", str(path), "-t", str(duration), "-map", "0:a:0", "-af", "pan=mono|c0=c0", "-ar", "48000", "-f", "f32le", "pipe:1"]))

    def tone(self, path, start, frequency=220):
        values = self.samples(path, start, .5)
        real = sum(value * math.cos(i * 2 * math.pi * frequency / 48000) for i, value in enumerate(values))
        imag = sum(value * math.sin(i * 2 * math.pi * frequency / 48000) for i, value in enumerate(values))
        return 2 * math.hypot(real, imag) / len(values)

    def test_ducking_lowers_only_music_during_voice_then_recovers(self):
        common = {"music_path": str(self.music), "music_gain_db": -8, "fade_in_seconds": 0, "fade_out_seconds": 0}
        ducked = self.render("ducked.mp4", {**common, "duck_music": True})
        flat = self.render("flat.mp4", {**common, "duck_music": False})
        path = ducked["output_path"]
        before, during, after = [self.tone(path, start) for start in (.7, 2.8, 5.1)]
        self.assertLess(during, before * .5)
        self.assertGreater(after, before * .8)
        self.assertGreater(self.tone(flat["output_path"], 2.8), during * 2)
        self.assertAlmostEqual(self.tone(path, 2.8, 800) / self.tone(flat["output_path"], 2.8, 800), 1, delta=.08)
        self.assertAlmostEqual(ducked["duration_seconds"], 6, delta=.05)

    def test_two_pass_normalization_equalizes_different_levels_without_changing_sources(self):
        originals = [path.read_bytes() for path in (self.quiet, self.loud)]
        result = self.render("normalized.mp4", {"normalize_voice": True}, [self.quiet, self.loud])
        rms = []
        for start in (.8, 3.8):
            values = self.samples(result["output_path"], start, 1)
            rms.append(math.sqrt(sum(value * value for value in values) / len(values)))
            self.assertLess(max(abs(value) for value in values), .93)
        self.assertLess(abs(20 * math.log10(rms[0] / rms[1])), 1)
        self.assertTrue(all(item["normalized"] for item in result["audio_processing"]))
        self.assertEqual(originals, [path.read_bytes() for path in (self.quiet, self.loud)])

    def test_music_loop_fades_and_no_loop_silence_do_not_shorten_video(self):
        faded = self.render("faded.mp4", {"music_path": str(self.music), "music_gain_db": -3, "fade_in_seconds": 2, "fade_out_seconds": 2}, [None])
        path = faded["output_path"]
        self.assertGreater(self.tone(path, 2.7), self.tone(path, .05) * 4)
        self.assertGreater(self.tone(path, 2.7), self.tone(path, 5.45) * 3)
        once = self.render("once.mp4", {"music_path": str(self.music), "loop_music": False, "fade_in_seconds": 0, "fade_out_seconds": 0}, [None])
        self.assertLess(self.tone(once["output_path"], 4), 1e-6)
        self.assertAlmostEqual(once["duration_seconds"], 6, delta=.05)

    def test_gain_peak_protection_and_corrupt_music_preserve_old_output(self):
        result = self.render("gain.mp4", {"normalize_voice": True, "target_lufs": -12, "voice_gain_db": 12}, [self.loud])
        self.assertLess(max(abs(value) for value in self.samples(result["output_path"], .3, 2)), .93)
        old = self.root / "old.mp4"
        old.write_bytes(b"keep")
        corrupt = self.root / "bad.wav"
        corrupt.write_bytes(b"bad audio")
        with self.assertRaises(RenderError):
            self.render("old.mp4", {"music_path": str(corrupt)}, overwrite=True)
        self.assertEqual(old.read_bytes(), b"keep")
        self.assertEqual(list(self.root.glob(".mimo-render-*")), [])

    def test_real_silent_narration_is_not_boosted_and_warning_is_explicit(self):
        silence = self.root / "silent.wav"
        self.ff(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1", str(silence)])
        result = self.render("silent.mp4", {"normalize_voice": True}, [silence])
        self.assertTrue(result["audio_processing"][0]["silent_or_too_quiet"])
        self.assertFalse(result["audio_processing"][0]["normalized"])
        self.assertTrue(result["warnings"])
        self.assertLess(max(abs(value) for value in self.samples(result["output_path"], .1, .5)), 1e-6)


class AudioHTTPTests(unittest.TestCase):
    def test_real_multipart_upload_and_save_routes(self):
        with tempfile.TemporaryDirectory() as root:
            server = create_server(port=0, projects_dir=root, quiet=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                pid = server.backend.create_project({"name": "http"})["status"]["project_id"]
                boundary = "mimo-audio-test-boundary"
                body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"music\"; filename=\"test.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode()
                        + wav_bytes() + f"\r\n--{boundary}--\r\n".encode())
                url = f"http://127.0.0.1:{server.server_port}/projects/{pid}/audio/"
                request = urllib.request.Request(url + "upload-music", data=body, headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
                with urllib.request.urlopen(request) as response:
                    music = json.load(response)["uploaded_music"]
                request = urllib.request.Request(url + "settings", data=json.dumps({"audio_settings": {"music_path": music["file_path"], "normalize_voice": True}}).encode(), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request) as response:
                    saved = json.load(response)["project"]["activities"][0]["metadata"]["audio_settings"]
                self.assertTrue(saved["normalize_voice"])
                self.assertTrue(Path(saved["music_path"]).is_file())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
