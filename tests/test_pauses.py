import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# macOS keeps temp dirs behind the /var -> /private/var symlink; stored project paths are resolved.
tempfile.tempdir = str(Path(tempfile.gettempdir()).resolve())

import config
from alignment import align_voiceovers, detect_silences, internal_pauses
from audio_mix import shorten_pauses
from backend.project_store import ProjectStore
from backend.workflow_api import WorkflowBackend
from media_render import discover_executable
from production_schema import VoiceoverSegment
from test_alignment import fake_aligner, tone_with_gaps

FFMPEG = discover_executable("ffmpeg")
TEXT = "一二三四，五六七八。"
LONG = [(0.1, False), (0.6, True), (0.9, False), (0.6, True), (0.1, False)]  # 2.3 s with a 0.9 s pause
SHORT = [(0.1, False), (0.6, True), (0.2, False), (0.6, True), (0.1, False)]


def timed_aligner(directory, chars):
    """A stand-in aligner that answers every item with the given character times."""
    settings = fake_aligner(directory)
    script = Path(settings["python"])
    script.write_text(f"""#!{sys.executable}
import json, sys
request = json.load(sys.stdin)
json.dump({{"items": [{{"chars": {chars!r}}} for _ in request["items"]]}}, sys.stdout, ensure_ascii=False)
""", encoding="utf-8")
    return settings


# How a real aligner times TEXT over LONG: no character inside the pause.
CHARS = [["一", 0.1, 0.25], ["二", 0.25, 0.4], ["三", 0.4, 0.55], ["四", 0.55, 0.7], ["，", None, None],
         ["五", 1.6, 1.75], ["六", 1.75, 1.9], ["七", 1.9, 2.05], ["八", 2.05, 2.2], ["。", None, None]]


def seconds(path):
    with wave.open(str(path)) as handle:
        return handle.getnframes() / handle.getframerate()


class PauseDetectionTests(unittest.TestCase):
    def test_only_silences_inside_the_speech_count(self):
        self.assertEqual(internal_pauses([(0.0, 0.1), (0.7, 1.6), (2.2, 2.3)], 2.3), [(0.7, 1.6)])
        self.assertEqual(internal_pauses([(1.0, float("inf"))], 2.0), [])

    @unittest.skipIf(os.name == "nt", "the fake aligner is a POSIX script")
    def test_alignment_reports_each_pause_and_where_it_falls_in_the_text(self):
        with tempfile.TemporaryDirectory() as directory:
            voice = tone_with_gaps(Path(directory) / "voice.wav", LONG)
            [result] = align_voiceovers([{"audio": str(voice), "text": TEXT, "duration": 2.3}], timed_aligner(directory, CHARS),
                                        ffmpeg=FFMPEG)
        self.assertEqual(result["source"], "aligned", result["warnings"])
        [pause] = result["pauses"]
        self.assertAlmostEqual(pause["start"], 0.7, delta=0.03)
        self.assertAlmostEqual(pause["seconds"], 0.9, delta=0.05)
        self.assertEqual((pause["before"], pause["after"][:1]), ("一二三四", "五"))

    def test_without_an_aligner_pauses_are_still_found(self):
        with tempfile.TemporaryDirectory() as directory:
            voice = tone_with_gaps(Path(directory) / "voice.wav", LONG)
            [result] = align_voiceovers([{"audio": str(voice), "text": TEXT, "duration": 2.3}], None, ffmpeg=FFMPEG)
        self.assertEqual(result["source"], "estimated")
        self.assertEqual([round(p["seconds"], 1) for p in result["pauses"]], [0.9])
        self.assertEqual(result["pauses"][0]["before"], "")


class ShortenTests(unittest.TestCase):
    def test_a_long_pause_is_cut_to_the_limit_and_the_speech_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            source = tone_with_gaps(Path(directory) / "voice.wav", LONG)
            output = Path(directory) / "short.wav"
            self.assertTrue(shorten_pauses(FFMPEG, source, output, [(0.7, 1.6)], 0.3))
            length = seconds(output)
            inside = internal_pauses(detect_silences(output, FFMPEG), length)
        self.assertAlmostEqual(length, 2.3 - 0.6, delta=0.01)
        self.assertEqual(len(inside), 1)
        self.assertAlmostEqual(inside[0][0], 0.7, delta=0.03)  # half of the limit stays on each side
        self.assertAlmostEqual(inside[0][1] - inside[0][0], 0.3, delta=0.04)

    def test_pauses_within_the_limit_leave_the_voice_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            source = tone_with_gaps(Path(directory) / "voice.wav", SHORT)
            output = Path(directory) / "short.wav"
            self.assertFalse(shorten_pauses(FFMPEG, source, output, [(0.7, 0.9)], 0.3))
            self.assertFalse(output.exists())


@unittest.skipIf(os.name == "nt", "the fake aligner is a POSIX script")
class BackendPauseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="pauses_backend_")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.backend = WorkflowBackend(ProjectStore(self.root / "store"), run_inline_jobs=True)
        self.pid = self.backend.create_project({"name": "停顿"})["status"]["project_id"]
        self.state = self.backend.store.get_state(self.pid)
        self.settings = fake_aligner(self.root)
        voices = self.state.project_dir / "voiceovers"
        voices.mkdir(parents=True)
        shots, segments = [], []
        for shot_id, parts in (("S01", LONG), ("S02", SHORT)):
            shots.append({"shot_id": shot_id, "tts_text": TEXT, "duration_seconds": 3})
            audio = tone_with_gaps(voices / f"{shot_id}.wav", parts)
            segments.append(VoiceoverSegment(shot_id=shot_id, segment_id=f"voiceover_{shot_id}", text=TEXT,
                                             audio_path=str(audio), duration_seconds=seconds(audio)))
        self.state.set_storyboard_shots(shots)
        self.state.set_voiceover_segments(segments)
        self.backend.store.save_project(self.pid)

    def job(self, method, payload=None):
        with patch.object(config, "aligner_settings", return_value=self.settings):
            job = getattr(self.backend, method)(self.pid, payload or {})["job"]
        self.assertEqual(job["status"], "succeeded", job.get("error"))
        return job["result"]

    def voice(self, shot_id):
        return next(v for v in self.state.voiceover_segments if v.shot_id == shot_id)

    def test_long_pauses_become_a_new_file_the_subtitles_follow_and_undo_restores_the_original(self):
        self.job("align_voiceovers")
        original, untouched = self.voice("S01").audio_path, self.voice("S02").audio_path
        result = self.job("shorten_voiceover_pauses", {"pause_limit_seconds": 0.4})
        self.assertEqual([item["shot_id"] for item in result["shortened"]], ["S01"])
        voice = self.voice("S01")
        self.assertEqual(result["shortened"][0]["audio_path"], voice.audio_path)
        self.assertNotEqual(voice.audio_path, original)
        self.assertEqual(Path(voice.audio_path).parent, Path(original).parent)
        self.assertTrue(Path(original).is_file())
        self.assertEqual(voice.metadata["original_audio_path"], original)
        self.assertAlmostEqual(voice.duration_seconds, 2.3 - 0.5, delta=0.02)
        self.assertTrue(all(p["seconds"] <= 0.45 for p in voice.metadata["subtitle_alignment"]["pauses"]))
        self.assertEqual(self.voice("S02").audio_path, untouched)
        history = self.backend.get_editor_history(self.pid)["history"]
        self.assertEqual(history["entries"][history["cursor"]]["label"], "压短句内停顿")
        self.assertEqual(self.job("shorten_voiceover_pauses", {"pause_limit_seconds": 0.4})["shortened"], [])
        self.backend.edit_history(self.pid, {"action": "undo"})
        self.assertEqual(self.voice("S01").audio_path, original)
        self.assertAlmostEqual(self.voice("S01").duration_seconds, 2.3, delta=0.02)

    def test_the_limit_must_be_between_a_tenth_and_three_seconds(self):
        for limit in (0.05, 5, "long"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.backend.shorten_voiceover_pauses(self.pid, {"pause_limit_seconds": limit})


if __name__ == "__main__":
    unittest.main()
