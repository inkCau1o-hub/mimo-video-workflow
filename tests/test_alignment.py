import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# macOS keeps temp dirs behind the /var -> /private/var symlink; stored project paths are resolved.
tempfile.tempdir = str(Path(tempfile.gettempdir()).resolve())

import config
from alignment import AlignmentError, align_voiceovers, clauses, cues_from_chars, detect_silences, estimate_cues, spoken_text
from backend.project_store import ProjectStore
from backend.workflow_api import WorkflowBackend
from media_render import discover_executable
from production_schema import VoiceoverSegment

FFMPEG = discover_executable("ffmpeg")
TEXT = "全新智能窗帘，现在只要 1999 元，XT60 接口更稳。"


def tone_with_gaps(path, parts, rate=16000):
    """Write a mono WAV of (seconds, audible) parts: a 440 Hz tone or digital silence."""
    samples = array("h")
    for seconds, audible in parts:
        count = int(seconds * rate)
        samples.extend(int(8000 * math.sin(2 * math.pi * 440 * i / rate)) if audible else 0 for i in range(count))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())
    return path


def even_chars(text, start, end):
    """What a perfect aligner returns for the spoken text spread evenly over [start, end]."""
    spoken = spoken_text(text)
    step = (end - start) / len(spoken)
    return [[char, round(start + i * step, 3), round(start + (i + 1) * step, 3)] for i, char in enumerate(spoken)]


def fake_aligner(directory, behaviour="even"):
    """An executable standing in for the aligner Python: it reads the worker request from stdin."""
    script = Path(directory) / "fake_python"
    script.write_text(f"""#!{sys.executable}
import json, sys
request = json.load(sys.stdin)
if {behaviour!r} == "crash":
    sys.stderr.write("RuntimeError: model exploded\\n")
    sys.exit(1)
items = []
for item in request["items"]:
    text, length = item["text"], len(item["text"])
    if {behaviour!r} == "short":
        length -= 1
    step = (item["duration"] - 0.1) / max(length, 1)
    items.append({{"chars": [[text[i], 0.05 + i * step, 0.05 + (i + 1) * step] for i in range(length)]}})
json.dump({{"items": items, "model": request["model"]}}, sys.stdout, ensure_ascii=False)
""", encoding="utf-8")
    script.chmod(0o755)
    model = Path(directory) / "model"
    model.mkdir(exist_ok=True)
    return {"python": str(script), "model": str(model), "nltk_data": None}


class SpokenTextTests(unittest.TestCase):
    def test_numbers_are_aligned_the_way_they_are_read(self):
        self.assertEqual(spoken_text("只要 1999 元"), "只要 一千九百九十九 元")
        self.assertEqual(spoken_text("2026 年，第 3 代"), "二零二六 年，第 三 代")
        self.assertEqual(spoken_text("提升 1.5 倍，打 50% 折扣"), "提升 一点五 倍，打 百分之五十 折扣")
        self.assertEqual(spoken_text("限量 10 台，库存 6063"), "限量 十 台，库存 六千零六十三")
        self.assertEqual(spoken_text("XT60 接口"), "XT六十 接口")


class ClauseAndEstimateTests(unittest.TestCase):
    def test_clauses_follow_punctuation_and_keep_offsets(self):
        parts = clauses(TEXT)
        self.assertEqual([text for text, _, _ in parts], ["全新智能窗帘，", "现在只要 1999 元，", "XT60 接口更稳。"])
        self.assertEqual([TEXT[a:b] for _, a, b in parts], [text for text, _, _ in parts])

    def test_estimate_splits_the_voice_by_character_count_without_gaps(self):
        cues = estimate_cues("一二三四，五六。", 3.0)
        self.assertEqual([cue["text"] for cue in cues], ["一二三四", "五六"])
        self.assertEqual((cues[0]["start"], cues[0]["end"], cues[1]["end"]), (0.0, 2.0, 3.0))
        self.assertEqual(cues[0]["end"], cues[1]["start"])


class CuesFromCharsTests(unittest.TestCase):
    def test_clause_starts_become_continuous_cues(self):
        chars = even_chars("一二三四，五六七八。", 0.1, 2.1)
        cues, warnings = cues_from_chars("一二三四，五六七八。", chars, 2.2)
        self.assertEqual([cue["text"] for cue in cues], ["一二三四", "五六七八"])
        self.assertAlmostEqual(cues[0]["start"], 0.1, places=2)
        self.assertAlmostEqual(cues[1]["start"], cues[0]["end"], places=6)
        self.assertAlmostEqual(cues[1]["start"], 1.1, places=1)
        self.assertAlmostEqual(cues[1]["end"], 1.9, places=2)  # the last character, not the full stop after it
        self.assertEqual(warnings, [])

    def test_boundaries_next_to_english_snap_to_the_nearest_silence(self):
        chars = even_chars(TEXT, 0.0, 4.0)
        start_of_english = cues_from_chars(TEXT, chars, 4.2)[0][2]["start"]
        cues, warnings = cues_from_chars(TEXT, chars, 4.2, silences=[(start_of_english - 0.3, start_of_english - 0.2)])
        self.assertAlmostEqual(cues[2]["start"], start_of_english - 0.2, places=2)
        self.assertEqual(cues[1]["end"], cues[2]["start"])
        self.assertEqual(warnings, [])

    def test_english_boundary_without_a_silence_is_flagged_for_review(self):
        cues, warnings = cues_from_chars(TEXT, even_chars(TEXT, 0.0, 4.0), 4.2)
        self.assertEqual(len(cues), 3)
        self.assertTrue(any("第 3 句" in warning and "英文" in warning for warning in warnings))

    def test_an_aligner_answer_for_other_text_is_rejected(self):
        with self.assertRaises(AlignmentError):
            cues_from_chars(TEXT, even_chars(TEXT, 0.0, 4.0)[:-1], 4.2)


class SilenceTests(unittest.TestCase):
    def test_silences_inside_the_voice_are_found(self):
        with tempfile.TemporaryDirectory() as directory:
            path = tone_with_gaps(Path(directory) / "voice.wav", [(0.5, True), (0.4, False), (0.5, True)])
            silences = detect_silences(path, FFMPEG)
        self.assertEqual(len(silences), 1)
        self.assertAlmostEqual(silences[0][0], 0.5, delta=0.03)
        self.assertAlmostEqual(silences[0][1], 0.9, delta=0.03)


@unittest.skipIf(os.name == "nt", "the fake aligner is a POSIX script")
class WorkerProtocolTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="align_worker_")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.voice = tone_with_gaps(self.root / "voice.wav", [(1.0, True), (0.3, False), (1.0, True)])
        self.items = [{"audio": str(self.voice), "text": "一二三四，五六七八。", "duration": 2.3}]

    def test_aligned_cues_come_back_from_the_separate_python(self):
        [result] = align_voiceovers(self.items, fake_aligner(self.root), ffmpeg=FFMPEG)
        self.assertEqual(result["source"], "aligned")
        self.assertEqual([cue["text"] for cue in result["cues"]], ["一二三四", "五六七八"])

    def test_missing_environment_or_drive_falls_back_to_estimates(self):
        settings = fake_aligner(self.root)
        for broken, reason in (({**settings, "python": str(self.root / "nowhere")}, "对齐环境"),
                               ({**settings, "model": str(self.root / "no-model")}, "对齐模型"), (None, "未配置")):
            with self.subTest(reason=reason):
                [result] = align_voiceovers(self.items, broken, ffmpeg=FFMPEG)
                self.assertEqual(result["source"], "estimated")
                self.assertTrue(any(reason in warning for warning in result["warnings"]), result["warnings"])
                self.assertEqual(len(result["cues"]), 2)

    def test_a_failing_or_inconsistent_worker_falls_back_to_estimates(self):
        for behaviour in ("crash", "short"):
            with self.subTest(behaviour=behaviour):
                [result] = align_voiceovers(self.items, fake_aligner(self.root, behaviour), ffmpeg=FFMPEG)
                self.assertEqual(result["source"], "estimated")
                self.assertTrue(result["warnings"])


@unittest.skipIf(os.name == "nt", "the fake aligner is a POSIX script")
class BackendAlignmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="align_backend_")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.backend = WorkflowBackend(ProjectStore(self.root / "store"), run_inline_jobs=True)
        self.pid = self.backend.create_project({"name": "对齐"})["status"]["project_id"]
        self.state = self.backend.store.get_state(self.pid)
        self.settings = fake_aligner(self.root)
        voices = self.state.project_dir / "voiceovers"
        voices.mkdir(parents=True)
        shots, segments = [], []
        for shot_id, subtitle in (("S01", None), ("S02", {"timing_mode": "cues", "timing_source": "manual",
                                                           "cues": [{"start": 0, "end": 1, "text": "我自己定的"}]}),
                                  ("S03", {"text": "改过的字幕"})):
            text = "一二三四，五六七八。"
            edit = {"lead_in_seconds": 0.5, **({"subtitle": subtitle} if subtitle else {})}
            shots.append({"shot_id": shot_id, "tts_text": text, "duration_seconds": 3, "metadata": {"edit": edit}})
            audio = tone_with_gaps(voices / f"{shot_id}.wav", [(1.0, True), (0.3, False), (1.0, True)])
            segments.append(VoiceoverSegment(shot_id=shot_id, segment_id=f"voiceover_{shot_id}", text=text,
                                             audio_path=str(audio), duration_seconds=2.3))
        self.state.set_storyboard_shots(shots)
        self.state.set_voiceover_segments(segments)
        self.backend.store.save_project(self.pid)

    def align(self):
        with patch.object(config, "aligner_settings", return_value=self.settings):
            job = self.backend.align_voiceovers(self.pid, {})["job"]
        self.assertEqual(job["status"], "succeeded", job.get("error"))
        return job

    def subtitle(self, shot_id):
        shot = next(s for s in self.state.storyboard_analysis.shots if s.shot_id == shot_id)
        return shot.metadata["edit"].get("subtitle", {})

    def test_default_subtitles_get_measured_cues_and_manual_ones_are_left_alone(self):
        job = self.align()
        first = self.subtitle("S01")
        self.assertEqual((first["timing_mode"], first["timing_source"]), ("cues", "aligned"))
        self.assertEqual([cue["text"] for cue in first["cues"]], ["一二三四", "五六七八"])
        self.assertAlmostEqual(first["cues"][0]["start"], 0.55, places=2)  # 0.5 s lead-in plus the voice onset
        self.assertEqual(self.subtitle("S02")["cues"], [{"start": 0.0, "end": 1.0, "text": "我自己定的"}])
        self.assertNotIn("cues", self.subtitle("S03"))
        voice = next(v for v in self.state.voiceover_segments if v.shot_id == "S01")
        self.assertEqual(voice.metadata["subtitle_alignment"]["source"], "aligned")
        self.assertEqual({item["shot_id"] for item in job["result"]["alignment"]}, {"S01", "S02", "S03"})
        history = self.backend.get_editor_history(self.pid)["history"]
        self.assertEqual(history["entries"][history["cursor"]]["label"], "按配音对齐字幕")

    def test_a_stale_voice_length_is_replaced_by_the_measured_one_so_the_last_cue_is_not_cut(self):
        voice = next(v for v in self.state.voiceover_segments if v.shot_id == "S01")
        voice.duration_seconds = 1.5  # what a TTS service may report; the file is 2.3 s long
        self.backend.store.save_project(self.pid)
        self.align()
        voice = next(v for v in self.state.voiceover_segments if v.shot_id == "S01")
        self.assertAlmostEqual(voice.duration_seconds, 2.3, delta=0.02)
        # ends with its last character (the 9th of ten spread over 0.05-2.25 s), not cut at the stale 0.5 + 1.5 s
        self.assertAlmostEqual(self.subtitle("S01")["cues"][-1]["end"], 0.5 + 0.05 + 9 * 0.22, delta=0.02)

    def test_a_manual_shot_shorter_than_its_voice_keeps_its_subtitles_and_is_reported(self):
        shots = [shot.to_dict() for shot in self.state.storyboard_analysis.shots]
        shots[0]["duration_seconds"] = 2.0
        shots[0]["metadata"]["edit"]["duration_mode"] = "manual"
        self.state.set_storyboard_shots(shots)
        self.backend.store.save_project(self.pid)
        job = self.align()
        self.assertNotIn("cues", self.subtitle("S01"))
        report = next(item for item in job["result"]["alignment"] if item["shot_id"] == "S01")
        self.assertFalse(report["applied"])
        self.assertTrue(any("手动时长" in warning for warning in report["warnings"]))

    def test_without_an_aligner_cues_are_estimated_and_flagged(self):
        self.settings = None
        self.align()
        self.assertEqual(self.subtitle("S01")["timing_source"], "estimated")

    def test_generated_voiceovers_are_aligned_right_away(self):
        class TTS:
            def synthesize_segments(self, segments, output_dir, **kwargs):
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                return [{"segment_id": s["segment_id"], "shot_id": s["shot_id"], "duration_seconds": 2.3,
                         "audio_path": str(tone_with_gaps(Path(output_dir) / f"{s['shot_id']}.wav",
                                                          [(1.0, True), (0.3, False), (1.0, True)]))} for s in segments]

        self.state.tts_client = TTS()
        with patch.object(config, "aligner_settings", return_value=self.settings):
            job = self.backend.generate_voiceovers(self.pid, {})["job"]
        self.assertEqual(job["status"], "succeeded", job.get("error"))
        self.assertEqual(self.subtitle("S01")["timing_source"], "aligned")


class RealAlignerTests(unittest.TestCase):
    """Runs only where the aligner environment and model are configured (see docs/WhisperX中文对齐实测.md)."""

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("say") and config.aligner_settings(),
                         "needs macOS speech and a configured aligner")
    def test_clause_starts_land_within_150_ms_on_synthesized_speech(self):
        with tempfile.TemporaryDirectory() as directory:
            root, parts = Path(directory), ["这款窗帘电机", "支持语音控制"]
            pcm, starts = array("h"), []
            for index, part in enumerate(parts):
                path = root / f"part{index}.wav"
                subprocess.run(["say", "-v", "Tingting", "-o", str(path), "--file-format=WAVE",
                                "--data-format=LEI16@16000", part], check=True, timeout=60)
                with wave.open(str(path)) as handle:
                    if pcm:
                        pcm.extend([0] * 4800)  # 0.3 s apart
                    starts.append(len(pcm) / 16000)
                    pcm.extend(array("h", handle.readframes(handle.getnframes())))
            voice = root / "voice.wav"
            with wave.open(str(voice), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(pcm.tobytes())
            [result] = align_voiceovers([{"audio": str(voice), "text": "，".join(parts) + "。", "duration": len(pcm) / 16000}],
                                        config.aligner_settings(), ffmpeg=FFMPEG)
        self.assertEqual(result["source"], "aligned", result["warnings"])
        self.assertAlmostEqual(result["cues"][1]["start"], starts[1], delta=0.15)


if __name__ == "__main__":
    unittest.main()
