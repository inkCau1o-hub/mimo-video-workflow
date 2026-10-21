"""Real-media integration tests. Run: python -m unittest discover -s tests -p test_media_render.py -v"""

from __future__ import annotations

import array
import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import media_render
from media_assembler import AssemblyPlanner, FFmpegAssemblyRenderer, RenderCancelled, RenderError
from production_schema import AssemblyPlan, MaterialAsset, StoryboardShot, VoiceoverSegment


class PlannerContractTests(unittest.TestCase):
    def test_voiceover_durations_drive_tracks_schema_items_and_total(self):
        shots = [{"shot_id": f"s{i}", "duration_seconds": 5, "start": i * 5, "end": (i + 1) * 5}
                 for i in range(3)]
        voices = [
            {"shot_id": "s0", "audio_path": "one.wav", "duration_seconds": 1.2},
            {"shot_id": "s1", "audio_path": "two.wav", "duration": 2.3},
            {"shot_id": "s2", "audio_path": "three.wav", "duration_seconds": 4.2},
        ]
        before = copy.deepcopy((shots, voices))
        for schema in (False, True):
            with self.subTest(schema=schema):
                plan = AssemblyPlanner().create_plan(shots, [{"file_path": "video.mp4"}], voices, as_schema=schema)
                if schema:
                    self.assertEqual(plan.total_duration_seconds, 7.7)
                    self.assertEqual([item.duration_seconds for item in plan.items], [1.2, 2.3, 4.2])
                    self.assertEqual([item.start_seconds for item in plan.items], [0, 1.2, 3.5])
                    for item in plan.items:
                        self.assertEqual(item.metadata["video_clip"]["duration"], item.duration_seconds)
                        self.assertEqual(item.metadata["voiceover_clip"]["duration"], item.duration_seconds)
                    plan = plan.metadata["dict_plan"]
                self.assertEqual(plan["timeline"]["duration"], 7.7)
                for track in plan["tracks"]:
                    self.assertEqual([clip["duration"] for clip in track["clips"]], [1.2, 2.3, 4.2])
                    self.assertEqual([clip["timeline_start"] for clip in track["clips"]], [0, 1.2, 3.5])
                    self.assertEqual([clip["timeline_end"] for clip in track["clips"]], [1.2, 3.5, 7.7])
                self.assertEqual([s["duration"] for s in plan["segments"]], [1.2, 2.3, 4.2])
        self.assertEqual((shots, voices), before)

    def test_voiceover_duration_accepts_positive_alias_and_rejects_invalid_values(self):
        invalid = (None, "", 0, -1, float("nan"), float("inf"), -float("inf"), "invalid")
        for value in invalid:
            for invalid_key, valid_key in (("duration_seconds", "duration"), ("duration", "duration_seconds")):
                with self.subTest(value=value, key=invalid_key):
                    voice = {"shot_id": "s1", "audio_path": "one.wav", invalid_key: value, valid_key: "6.25"}
                    plan = AssemblyPlanner().create_plan(
                        [StoryboardShot(shot_id="s1", duration_seconds=5)],
                        [{"file_path": "video.mp4"}], [voice],
                    )
                    self.assertEqual(plan["timeline"]["duration"], 6.25)
            with self.subTest(invalid_duration=value):
                plan = AssemblyPlanner().create_plan(
                    [StoryboardShot(shot_id="s1", duration_seconds=5)], [{"file_path": "video.mp4"}],
                    [{"shot_id": "s1", "audio_path": "one.wav", "duration_seconds": value, "duration": value}],
                )
                self.assertEqual(plan["timeline"]["duration"], 5)

    def test_voiceover_seconds_take_precedence_over_legacy_duration(self):
        plan = AssemblyPlanner().create_plan(
            [StoryboardShot(shot_id="s1", duration_seconds=5)], [{"file_path": "video.mp4"}],
            [{"shot_id": "s1", "audio_path": "one.wav", "duration_seconds": 2, "duration": 9}],
        )
        self.assertEqual(plan["timeline"]["duration"], 2)

    def test_no_valid_voiceover_preserves_storyboard_and_default_fallbacks(self):
        planner = AssemblyPlanner(default_scene_duration=4)
        shots = [{"shot_id": "s1", "duration_seconds": 5}, {"shot_id": "s2", "duration_seconds": 0}]
        for voices in (None, [VoiceoverSegment(shot_id="s1"), VoiceoverSegment(shot_id="s2")]):
            with self.subTest(voices=voices):
                plan = planner.create_plan(shots, [{"file_path": "video.mp4"}], voices)
                self.assertEqual([s["duration"] for s in plan["segments"]], [5, 4])
                self.assertEqual(plan["timeline"]["duration"], 9)
        plan = planner.create_plan(
            [{"shot_id": "s1", "start": 2, "end": 6, "duration_seconds": 5}],
            [{"file_path": "video.mp4"}],
        )
        self.assertEqual(plan["segments"][0]["timeline_start"], 2)
        self.assertEqual(plan["segments"][0]["duration"], 4)

    def test_manual_asset_wins_even_below_minimum_match_score(self):
        shot = StoryboardShot(shot_id="s1", visual_tags=["red"], metadata={"material_asset_id": "blue"})
        assets = [MaterialAsset(asset_id="red", file_path="red.mp4", visual_tags=["red"]),
                  MaterialAsset(asset_id="blue", file_path="blue.mp4", visual_tags=["blue"])]
        plan = AssemblyPlanner(min_match_score=1).create_plan([shot], assets)
        self.assertEqual(plan["segments"][0]["video_clip"]["asset_id"], "blue")

    def test_invalid_manual_asset_fails_instead_of_falling_back(self):
        with self.assertRaisesRegex(ValueError, "metadata.material_asset_id.*does not exist"):
            AssemblyPlanner().create_plan(
                [{"shot_id": "s1", "metadata": {"material_asset_id": "deleted"}}],
                [{"asset_id": "exists", "file_path": "test.mp4"}],
            )

    def edit_plan(self, edit, duration=1.5, asset_duration=5, voices=None, schema=False):
        return AssemblyPlanner().create_plan(
            [StoryboardShot(shot_id="s1", duration_seconds=duration, script_text="script",
                            tts_text="narration", metadata={"edit": edit})],
            [MaterialAsset(asset_id="video", file_path="video.mp4", duration_seconds=asset_duration)],
            voices, as_schema=schema,
        )

    def test_edit_fields_survive_dict_schema_and_track_only_plans(self):
        edit = dict(source_in=.5, source_out=3, fit="contain", crop_x=.1, crop_y=.9,
                    shortage="error", duration_mode="manual",
                    subtitle=dict(text="independent\nsubtitle", position="top", font_size=64,
                                  color="#12aBeF", background=True))
        before = copy.deepcopy(edit)
        for schema in (False, True):
            plan = self.edit_plan(edit, schema=schema)
            if schema:
                self.assertEqual(plan.items[0].metadata["edit"], edit)
                payload = plan.to_dict()
            else:
                self.assertEqual(plan["segments"][0]["edit"], edit)
                payload = plan
            rows, _ = media_render._plan_rows(payload)
            row = rows[0]
            self.assertEqual(row["edit"], edit)
            self.assertEqual(row["duration"], 1.5)
            self.assertEqual(row["text"], edit["subtitle"]["text"])
            for key in ("source_in", "source_out", "fit", "crop_x", "crop_y", "shortage"):
                self.assertEqual(row["video"][key], edit[key])
        track_plan = {"tracks": self.edit_plan(edit)["tracks"]}
        rows, _ = media_render._plan_rows(track_plan)
        self.assertEqual(rows[0]["edit"], edit)
        self.assertEqual(rows[0]["text"], edit["subtitle"]["text"])
        self.assertEqual(edit, before)

    def test_subtitle_missing_text_falls_back_but_explicit_empty_is_preserved(self):
        for subtitle, expected in (({}, "narration"), ({"text": ""}, ""), ({"text": "only subtitle"}, "only subtitle")):
            with self.subTest(subtitle=subtitle):
                plan = self.edit_plan({"subtitle": subtitle}, voices=[
                    VoiceoverSegment(shot_id="s1", text="stale voice text", audio_path="voice.wav")])
                rows, _ = media_render._plan_rows(plan)
                self.assertEqual(rows[0]["text"], expected)
        plan = self.edit_plan({"subtitle": {}})
        plan["segments"][0]["tts_text"] = ""
        self.assertEqual(media_render._plan_rows(plan)[0][0]["text"], "script")

    def test_schema_edit_is_authoritative_over_snapshot_and_video_copy(self):
        plan = self.edit_plan({"source_in": .5, "subtitle": {"text": "old"}}, schema=True)
        plan.items[0].metadata["edit"] = {"source_in": 1, "subtitle": {"text": ""}}
        row = media_render._plan_rows(plan.to_dict())[0][0]
        self.assertEqual(row["video"]["source_in"], 1)
        self.assertEqual(row["text"], "")

    def test_manual_and_narration_duration_modes(self):
        voice = VoiceoverSegment(shot_id="s1", audio_path="voice.wav", duration_seconds=1.2)
        self.assertEqual(self.edit_plan({}, voices=[voice])["segments"][0]["duration"], 1.2)
        plan = self.edit_plan({"duration_mode": "manual"}, voices=[voice], schema=True)
        self.assertEqual(plan.total_duration_seconds, 1.5)
        self.assertEqual(plan.items[0].duration_seconds, 1.5)
        with self.assertRaisesRegex(ValueError, "shorter than existing narration"):
            self.edit_plan({"duration_mode": "manual"}, duration=1, voices=[voice])
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "duration_seconds"):
                self.edit_plan({"duration_mode": "manual"}, duration=value)
        # A duration estimate without a usable narration file must not drive timing.
        voice.audio_path = ""
        self.assertEqual(self.edit_plan({}, voices=[voice])["segments"][0]["duration"], 1.5)

    def test_edited_shots_follow_array_order_without_narration(self):
        plan = AssemblyPlanner().create_plan(
            [{"shot_id": "second", "duration_seconds": 2, "start": 5, "end": 7, "metadata": {"edit": {}}},
             {"shot_id": "first", "duration_seconds": 1, "start": 0, "end": 1, "metadata": {"edit": {}}}],
            [{"file_path": "video.mp4"}],
        )
        self.assertEqual([s["shot_id"] for s in plan["segments"]], ["second", "first"])
        self.assertEqual([s["timeline_start"] for s in plan["segments"]], [0, 2])
        self.assertEqual(plan["timeline"]["duration"], 3)

    def test_manual_plan_preserves_submillisecond_duration(self):
        duration = 1.1304
        plan = self.edit_plan({"duration_mode": "manual"}, duration=duration, schema=True, voices=[
            VoiceoverSegment(shot_id="s1", audio_path="voice.wav", duration_seconds=duration)])
        self.assertEqual(plan.total_duration_seconds, duration)
        self.assertEqual(plan.items[0].duration_seconds, duration)
        self.assertEqual(plan.items[0].metadata["video_clip"]["duration"], duration)
        self.assertEqual(plan.items[0].metadata["voiceover_clip"]["duration"], duration)

    def test_missing_narration_does_not_borrow_another_shots_audio(self):
        plan = AssemblyPlanner().create_plan(
            [StoryboardShot(shot_id="s0", duration_seconds=3, metadata={"edit": {}}),
             StoryboardShot(shot_id="s1", duration_seconds=4, metadata={"edit": {"duration_mode": "manual"}})],
            [{"file_path": "video.mp4"}],
            [VoiceoverSegment(shot_id="s1", audio_path="s1.wav", duration_seconds=1.2)],
        )
        self.assertIsNone(plan["segments"][0]["voiceover_clip"])
        self.assertEqual([s["duration"] for s in plan["segments"]], [3, 4])
        self.assertEqual(plan["timeline"]["duration"], 7)
        self.assertEqual(plan["segments"][1]["voiceover_clip"]["source_path"], "s1.wav")

    def test_legacy_unlinked_voiceover_keeps_positional_matching(self):
        plan = AssemblyPlanner().create_plan(
            [StoryboardShot(shot_id="s1", duration_seconds=4)], [{"file_path": "video.mp4"}],
            [{"segment_id": "legacy_voice", "audio_path": "voice.wav", "duration_seconds": 1.2}],
        )
        self.assertEqual(plan["segments"][0]["voiceover_clip"]["source_path"], "voice.wav")
        self.assertEqual(plan["timeline"]["duration"], 1.2)

    def test_known_source_bounds_and_shortage_are_checked_for_auto_matches(self):
        for edit in ({"source_in": 5}, {"source_out": 5.01}, {"source_in": 2, "source_out": 1}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.edit_plan(edit)
        plan = self.edit_plan({"source_in": 1, "source_out": 1.5})
        self.assertIn("last frame", plan["warnings"][0])
        with self.assertRaisesRegex(ValueError, "shorter than the target"):
            self.edit_plan({"source_in": 1, "source_out": 1.5, "shortage": "error"})
        self.assertIsNone(self.edit_plan({})["segments"][0]["video_clip"]["source_out"])
        self.assertEqual(self.edit_plan({}, asset_duration=None)["warnings"], [])

    def test_invalid_edit_and_ass_style_fields_are_rejected(self):
        invalid = [None, [], {"source_in": -1}, {"source_in": True}, {"source_in": "0"},
                   {"source_out": float("inf")}, {"source_out": 0}, {"fit": "fit"},
                   {"crop_x": 1.1}, {"crop_y": float("nan")}, {"crop_y": "0.5,crop=1"},
                   {"shortage": "loop"}, {"duration_mode": "custom"}, {"subtitle": None}]
        invalid += [{"subtitle": {key: value}} for key, value in (
            ("text", None), ("text", 3), ("position", "top\nDialogue: injected"),
            ("font_size", 15), ("font_size", 97), ("font_size", True), ("font_size", float("nan")),
            ("color", "#FFFFFF,ass=injected"), ("color", "red"), ("background", "false"))]
        for edit in invalid:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.edit_plan(edit)

    def test_ass_style_scaling_newlines_and_escaping(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "subtitle.ass"
            text = "one\ntwo{\\pos(0,0)}\r\nDialogue: evil\\N\x00"
            media_render._write_subtitles(path, text, 2, 1280, 720, "Test Font", {
                "position": "top", "font_size": 64, "color": "#123456", "background": True})
            content = path.read_text(encoding="utf-8")
            style = next(line for line in content.splitlines() if line.startswith("Style:"))
            self.assertIn("Test Font,64,&H00563412", style)
            self.assertIn(",3,10,0,8,", style)
            events = [line for line in content.splitlines() if line.startswith("Dialogue:")]
            self.assertEqual(len(events), 2)
            self.assertIn(r"one\Ntwo", events[0])
            self.assertNotIn("{", content)
            self.assertNotIn(r"\pos", content)
            self.assertNotIn("\x00", content)
            for family in ("bad\nDialogue:", r"bad{\pos}", "bad,Font"):
                with self.assertRaises(RenderError):
                    media_render._write_subtitles(path, "text", 1, 180, 320, family)

    def test_dry_run_keeps_legacy_command_and_performs_no_execution(self):
        plan = AssemblyPlanner().create_plan(
            [{"shot_id": "one"}, {"shot_id": "two"}],
            [{"file_path": "video with spaces.mp4"}],
        )
        renderer = FFmpegAssemblyRenderer()
        with patch("media_render.subprocess.Popen", side_effect=AssertionError("Must not execute")):
            self.assertEqual(renderer.render(plan, "out.mp4"), renderer.build_command(plan, "out.mp4"))
            self.assertIsInstance(renderer.render(plan, "out.mp4", False, True, False, dry_run=True), list)

    @unittest.skipUnless(os.name == "nt", "Windows WinGet discovery")
    def test_ffmpeg_discovery_handles_stale_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "WinGet/Links/ffmpeg.exe"
            binary.parent.mkdir(parents=True)
            binary.touch()
            with patch("media_render.os.name", "nt"), patch("media_render.shutil.which", return_value=None), \
                 patch.dict(os.environ, {"ProgramFiles": str(root), "LOCALAPPDATA": str(root)}):
                self.assertEqual(media_render.discover_executable("ffmpeg"), str(binary.resolve()))

    def test_long_subtitles_are_paged_and_ass_injection_is_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            subtitle = Path(temp) / "test.ass"
            text = "{\\pos(0,0)}" + "\u4e2d\u6587\u5b57\u5e55" * 40
            media_render._write_subtitles(subtitle, text, 10, 180, 320, "Test Font")
            content = subtitle.read_text(encoding="utf-8")
            events = [line for line in content.splitlines() if line.startswith("Dialogue:")]
            self.assertGreater(len(events), 1)
            self.assertNotIn("{\\pos", content)
            self.assertTrue(all(line.count(r"\N") <= 1 for line in events))
            self.assertIn("0:00:10.00", events[-1])

    def test_system_ffmpeg_is_preferred_and_ass_capability_is_checked(self):
        runner = media_render._Runner(None, None, 10)
        def discover(name, requested=None):
            return "bundled" if requested else "system"
        with patch("media_render.discover_executable", side_effect=discover), \
             patch.object(runner, "run", return_value=b" .. ass V->V Render ASS subtitles.\n") as execute:
            self.assertEqual(media_render._select_ffmpeg(runner, "bundled", True, ROOT), "system")
            self.assertEqual(execute.call_args.args[0][0], "system")
        with patch("media_render.discover_executable", side_effect=discover), \
             patch.object(runner, "run", side_effect=[b"no subtitles", b" ... ass V->V Render subtitles\n"]):
            self.assertEqual(media_render._select_ffmpeg(runner, "bundled", True, ROOT), "bundled")
        with patch("media_render.discover_executable", side_effect=discover), \
             patch.object(runner, "run", return_value=b"no subtitles"):
            with self.assertRaises(RenderError) as caught:
                media_render._select_ffmpeg(runner, "bundled", True, ROOT)
        self.assertEqual(caught.exception.code, "missing_subtitle_filter")


class RealRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg = media_render.discover_executable("ffmpeg")
            cls.ffprobe = media_render.discover_executable("ffprobe", sibling=Path(cls.ffmpeg))
        except RenderError as exc:
            raise unittest.SkipTest(str(exc))
        cls.workspace = tempfile.TemporaryDirectory(prefix="mimo real media ")
        cls.root = Path(cls.workspace.name) / "\u4e2d\u6587 \u6e32\u67d3 ' [test]"
        cls.root.mkdir()
        cls.red = cls.root / "\u7ea2\u8272 \u955c\u5934.mp4"
        cls.blue = cls.root / "\u84dd\u8272 \u955c\u5934.mp4"
        cls.voice1 = cls.root / "\u914d\u97f3 \u4e00.wav"
        cls.voice2 = cls.root / "\u914d\u97f3 \u4e8c.mp3"
        for color, path, size in (("red", cls.red, "320x180"), ("blue", cls.blue, "180x320")):
            cls.ff([
                "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=24:d=0.4",
                "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=0.4",
                "-af", "volume=6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(path),
            ])
        for path, frequency, duration in ((cls.voice1, 440, 1.13), (cls.voice2, 660, .87)):
            cls.ff(["-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={duration}", str(path)])
        cls.renderer = FFmpegAssemblyRenderer(cls.ffmpeg)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    @classmethod
    def ff(cls, args):
        return subprocess.run([cls.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"] + args,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="case ", dir=self.root)
        self.addCleanup(self.temp.cleanup)
        self.outdir = Path(self.temp.name)
        self.output = self.outdir / "\u6210\u7247 \u591a\u5206\u955c.mp4"

    def plan(self, schema=False):
        return AssemblyPlanner(fps=24, resolution=(180, 320)).create_plan(
            [StoryboardShot(shot_id="s1", duration_seconds=.25, script_text="\u4e2d\u6587\u5b57\u5e55\u6d4b\u8bd5"),
             StoryboardShot(shot_id="s2", duration_seconds=7, script_text="\u7b2c\u4e8c\u4e2a\u955c\u5934",
                            metadata={"material_asset_id": "blue"})],
            [MaterialAsset(asset_id="red", file_path=str(self.red), duration_seconds=.4),
             MaterialAsset(asset_id="blue", file_path=str(self.blue), duration_seconds=.4)],
            [VoiceoverSegment(segment_id="v1", shot_id="s1", audio_path=str(self.voice1)),
             VoiceoverSegment(segment_id="v2", shot_id="s2", audio_path=str(self.voice2))],
            as_schema=schema,
        )

    def render(self, plan=None, **options):
        settings = dict(dry_run=False, resolution=(180, 320), fps=24,
                        video_bitrate="350k", preset="ultrafast", ffprobe_path=self.ffprobe)
        settings.update(options)
        return self.renderer.render(self.plan() if plan is None else plan, str(self.output), **settings)

    def probe(self):
        return json.loads(subprocess.check_output([
            self.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(self.output),
        ]))

    def frame(self, seconds, width=180, height=320):
        data = self.ff(["-ss", str(seconds), "-i", str(self.output), "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
        self.assertEqual(len(data), width * height * 3)
        return data

    def audio(self, seconds=.1):
        data = self.ff(["-ss", str(seconds), "-i", str(self.output), "-t", "0.25", "-vn", "-ac", "1",
                        "-ar", "48000", "-f", "f32le", "pipe:1"])
        samples = array.array("f")
        samples.frombytes(data)
        if sys.byteorder != "little":
            samples.byteswap()
        return samples

    def assert_clean(self):
        self.assertFalse(list(self.outdir.glob(".mimo-render-*")))

    def test_multishot_portrait_actual_narration_chinese_path_audio_and_subtitles(self):
        try:
            media_render.discover_font()
        except RenderError as exc:
            self.skipTest(str(exc))
        progress = []
        plan = self.plan()
        before = copy.deepcopy(plan)
        result = self.render(plan, progress_callback=progress.append)
        self.assertEqual(plan, before)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["output_path"], str(self.output.resolve()))
        self.assertEqual(result["segment_count"], 2)
        self.assertGreaterEqual(result["duration_seconds"], 2.0)
        self.assertLess(result["duration_seconds"], 2.2)
        self.assertGreater(result["size_bytes"], 1000)
        self.assertEqual(progress, sorted(set(progress)))
        self.assertEqual((progress[0], progress[-1]), (0.0, 1.0))
        self.assertTrue(any(.1 < value < .9 for value in progress))
        streams = self.probe()["streams"]
        video = next(s for s in streams if s["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (180, 320))
        self.assertTrue(any(s["codec_type"] == "audio" and s["codec_name"] == "aac" for s in streams))
        for seconds, color in ((.2, "red"), (.95, "red"), (1.6, "blue")):
            frame = self.frame(seconds)
            x, y = 90, 80
            r, g, b = frame[(y * 180 + x) * 3:(y * 180 + x) * 3 + 3]
            self.assertTrue(r > 180 and b < 40 if color == "red" else b > 180 and r < 40)
        frame = self.frame(.2)
        bottom = frame[240 * 180 * 3:]
        self.assertGreater(sum(min(bottom[i:i + 3]) > 170 for i in range(0, len(bottom), 3)), 20)
        audio = self.audio()
        self.assertGreater(sum(x * x for x in audio) / len(audio), .001)
        def amplitude(frequency):
            real = sum(x * math.cos(2 * math.pi * frequency * i / 48000) for i, x in enumerate(audio))
            imag = sum(x * math.sin(2 * math.pi * frequency * i / 48000) for i, x in enumerate(audio))
            return math.hypot(real, imag)
        self.assertGreater(amplitude(440), amplitude(1000) * 20)
        self.assert_clean()

    def test_measured_voiceover_plan_matches_real_output_within_frame_rounding(self):
        voices = []
        for index, path in enumerate((self.voice1, self.voice2)):
            info = json.loads(subprocess.check_output([
                self.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path),
            ]))
            voices.append(VoiceoverSegment(
                shot_id=f"s{index}", audio_path=str(path), duration_seconds=float(info["format"]["duration"]),
            ))
        plan = AssemblyPlanner(fps=24).create_plan(
            [StoryboardShot(shot_id=f"s{i}", duration_seconds=5) for i in range(2)],
            [MaterialAsset(asset_id="red", file_path=str(self.red))], voices, as_schema=True,
        )
        self.assertEqual(plan.total_duration_seconds, round(sum(v.duration_seconds for v in voices), 3))
        self.assertLess(plan.total_duration_seconds, 3)
        result = self.render(plan, subtitles=False)
        self.assertGreaterEqual(result["duration_seconds"], plan.total_duration_seconds)
        self.assertLess(result["duration_seconds"] - plan.total_duration_seconds, len(voices) / 24 + .005)
        self.assert_clean()

    def test_landscape_schema_and_file_uri(self):
        plan = self.plan(schema=True)
        self.assertIsInstance(plan, AssemblyPlan)
        plan.items[1].metadata["video_clip"]["source_path"] = self.blue.as_uri()
        result = self.render(plan, aspect_ratio="16:9", resolution=(320, 180), subtitles=False)
        self.assertEqual((result["width"], result["height"]), (320, 180))
        frame = self.frame(1.6, 320, 180)
        r, g, b = frame[(40 * 320 + 100) * 3:(40 * 320 + 100) * 3 + 3]
        self.assertGreater(b, 180)
        self.assertLess(r, 40)
        self.assert_clean()

    def test_schema_items_override_stale_snapshot(self):
        plan = self.plan(schema=True)
        plan.items[1].metadata["video_clip"]["source_path"] = str(self.red)
        self.render(plan, subtitles=False)
        frame = self.frame(1.6)
        self.assertGreater(frame[(80 * 180 + 90) * 3], 180)

    def test_track_only_plan_and_relative_inputs(self):
        plan = self.plan()
        plan = {"tracks": plan["tracks"], "timeline": plan["timeline"]}
        for track in plan["tracks"]:
            for clip in track["clips"]:
                clip["source_path"] = Path(clip["source_path"]).name
        self.render(plan, subtitles=False, base_dir=str(self.root))
        self.assertGreater(int(self.probe()["format"]["size"]), 1000)

    def test_no_voiceover_is_silent_and_uses_planned_duration(self):
        plan = self.plan()
        for segment in plan["segments"]:
            segment["duration"] = .4
            segment["voiceover_clip"]["source_path"] = "not required.wav"
        result = self.render(plan, include_voiceover=False, subtitles=False)
        self.assertLess(abs(result["duration_seconds"] - 20 / 24), .08)
        self.assertLess(max(abs(x) for x in self.audio()), .0001)

    def test_existing_output_not_overwritten_by_default(self):
        self.output.write_bytes(b"old output")
        with self.assertRaises(RenderError) as caught:
            self.render(subtitles=False)
        self.assertEqual(caught.exception.code, "output_exists")
        self.assertEqual(self.output.read_bytes(), b"old output")
        self.assert_clean()

    def test_timeout_terminates_process_and_preserves_previous_output(self):
        self.output.write_bytes(b"old output")
        with self.assertRaises(RenderError) as caught:
            self.render(overwrite=True, subtitles=False, process_timeout=.001)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertEqual(self.output.read_bytes(), b"old output")
        self.assert_clean()

    def test_corrupt_later_narration_is_not_silently_skipped(self):
        broken = self.outdir / "private_SECRET.wav"
        broken.write_bytes(b"not audio")
        plan = self.plan()
        plan["segments"][1]["voiceover_clip"]["source_path"] = str(broken)
        with self.assertRaises(RenderError) as caught:
            self.render(plan, subtitles=False)
        self.assertEqual(caught.exception.code, "process_failed")
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertFalse(self.output.exists())
        self.assert_clean()

    def test_source_trim_and_missing_second_voiceover(self):
        plan = self.plan()
        row = plan["segments"][0]
        row["video_clip"].update(source_in=.1, source_out=.3, fit="contain")
        row["voiceover_clip"]["source_in"] = .25
        plan["segments"][1].update(voiceover_clip=None, duration=.5)
        result = self.render(plan, subtitles=False)
        self.assertLess(abs(result["duration_seconds"] - (22 / 24 + .5)), .06)
        self.assertLess(max(abs(x) for x in self.audio(1.1)), .0001)
        self.assert_clean()

    def test_missing_later_shot_preserves_previous_output_and_redacts(self):
        self.output.write_bytes(b"old output")
        plan = self.plan()
        plan["segments"][1]["video_clip"]["source_path"] = str(self.root / "private_api_key_SECRET.mp4")
        with self.assertRaises(RenderError) as caught:
            self.render(plan, overwrite=True, subtitles=False)
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertNotIn(str(self.root), str(caught.exception))
        self.assertEqual(caught.exception.code, "missing_input")
        self.assertEqual(self.output.read_bytes(), b"old output")
        self.assert_clean()

    def test_output_cannot_be_any_input(self):
        with self.assertRaises(RenderError) as caught:
            self.renderer.render(self.plan(), str(self.blue), dry_run=False, overwrite=True, subtitles=False)
        self.assertEqual(caught.exception.code, "input_output_conflict")

    def test_actual_ffmpeg_failure_is_clean_and_redacted(self):
        self.output.write_bytes(b"old output")
        original = media_render._Runner.run
        def fail_encoding(runner, argv, cwd, phase, tick=None):
            if phase == "shot encoding":
                argv = list(argv)
                argv[argv.index("-vf") + 1] = "invalid_filter_private_SECRET"
            return original(runner, argv, cwd, phase, tick)
        with patch.object(media_render._Runner, "run", fail_encoding):
            with self.assertRaises(RenderError) as caught:
                self.render(overwrite=True, subtitles=False)
        self.assertEqual(caught.exception.code, "process_failed")
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(self.output.read_bytes(), b"old output")
        self.assert_clean()

    def test_cancel_during_real_encoder_terminates_child_and_cleans(self):
        self.output.write_bytes(b"old output")
        plan = self.plan()
        for row in plan["segments"]:
            row["duration"] = 20
            row["voiceover_clip"] = None
        processes = []
        active = []
        original = subprocess.Popen
        def watch(argv, *args, **kwargs):
            process = original(argv, *args, **kwargs)
            processes.append(process)
            if "libx264" in argv:
                active.append((process, time.monotonic()))
            return process
        def cancel():
            return bool(active and time.monotonic() - active[0][1] > .12)
        with patch("media_render.subprocess.Popen", side_effect=watch):
            with self.assertRaises(RenderCancelled):
                self.render(plan, overwrite=True, subtitles=False, preset="slow", resolution=(720, 1280), cancel_callback=cancel)
        self.assertTrue(active)
        self.assertTrue(all(process.poll() is not None for process in processes))
        self.assertEqual(self.output.read_bytes(), b"old output")
        self.assert_clean()

    def test_precancel_starts_no_child_and_creates_no_output(self):
        with patch("media_render.subprocess.Popen", side_effect=AssertionError("No child expected")):
            with self.assertRaises(RenderCancelled):
                self.render(cancel_callback=lambda: True)
        self.assertFalse(self.output.exists())
        self.assert_clean()

    def test_cancel_check_alias_and_conflicting_callbacks(self):
        with self.assertRaises(RenderCancelled):
            self.render(cancel_check=lambda: True)
        with self.assertRaises(RenderError) as caught:
            self.render(cancel_check=lambda: False, cancel_callback=lambda: False)
        self.assertEqual(caught.exception.code, "invalid_options")
        self.assertFalse(self.output.exists())

    def test_cancel_at_commit_keeps_previous_output(self):
        self.output.write_bytes(b"old output")
        values = []
        with self.assertRaises(RenderCancelled):
            self.render(overwrite=True, subtitles=False, progress_callback=values.append,
                        cancel_callback=lambda: bool(values and values[-1] >= .99))
        self.assertEqual(self.output.read_bytes(), b"old output")
        self.assertLess(values[-1], 1)
        self.assert_clean()

    def test_callback_failure_is_redacted_and_cleans(self):
        def fail(value):
            if value >= .1:
                raise ValueError("private_SECRET")
        with self.assertRaises(RenderError) as caught:
            self.render(progress_callback=fail, subtitles=False)
        self.assertEqual(caught.exception.code, "callback_failed")
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertFalse(self.output.exists())
        self.assert_clean()

    def test_commit_race_does_not_overwrite_another_jobs_output(self):
        def race(value):
            if value >= .99:
                self.output.write_bytes(b"another job")
        with self.assertRaises(RenderError) as caught:
            self.render(progress_callback=race, subtitles=False)
        self.assertEqual(caught.exception.code, "output_exists")
        self.assertEqual(self.output.read_bytes(), b"another job")
        self.assert_clean()

    def test_successful_overwrite_and_final_callback_failure_are_committed(self):
        self.output.write_bytes(b"old output")
        def final_callback(value):
            if value == 1:
                raise ValueError("observer failed after commit")
        result = self.render(overwrite=True, subtitles=False, progress_callback=final_callback)
        self.assertGreater(result["size_bytes"], 1000)
        self.assert_clean()


if __name__ == "__main__":
    unittest.main(verbosity=2)
