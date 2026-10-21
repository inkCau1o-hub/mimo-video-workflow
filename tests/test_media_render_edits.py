"""Offline editor-contract tests using generated media and real FFmpeg output."""

from __future__ import annotations

import array
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import media_render
from media_assembler import AssemblyPlanner, FFmpegAssemblyRenderer, RenderError
from production_schema import MaterialAsset, StoryboardShot, VoiceoverSegment


class RealEditorRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg = media_render.discover_executable("ffmpeg")
            cls.ffprobe = media_render.discover_executable("ffprobe", sibling=Path(cls.ffmpeg))
        except RenderError as exc:
            raise unittest.SkipTest(str(exc))
        cls.workspace = tempfile.TemporaryDirectory(prefix="mimo edit media ")
        cls.root = Path(cls.workspace.name) / "\u9636\u6bb5\u4e00 \u771f\u5b9e\u6e32\u67d3 ' [test]"
        cls.root.mkdir()
        cls.temporal = cls.root / "\u9009\u6bb5 \u7ea2\u7eff\u84dd.mp4"
        cls.horizontal = cls.root / "\u6a2a\u5411 \u88c1\u526a.mp4"
        cls.vertical = cls.root / "\u7ad6\u5411 \u88c1\u526a.mp4"
        cls.gray = cls.root / "\u5b57\u5e55 \u5e95\u56fe.mp4"
        cls.voice = cls.root / "\u5b8c\u6574 \u65c1\u767d.wav"
        sources = (
            (cls.temporal, "color=c=red:s=240x120:r=20:d=1.8,"
             "drawbox=x=0:y=0:w=iw:h=ih:color=lime:t=fill:enable='gte(t,0.6)',"
             "drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='gte(t,1.2)'"),
            (cls.horizontal, "color=c=red:s=360x120:r=20:d=0.8,"
             "drawbox=x=120:y=0:w=120:h=120:color=lime:t=fill,"
             "drawbox=x=240:y=0:w=120:h=120:color=blue:t=fill"),
            (cls.vertical, "color=c=red:s=120x360:r=20:d=0.8,"
             "drawbox=x=0:y=120:w=120:h=120:color=lime:t=fill,"
             "drawbox=x=0:y=240:w=120:h=120:color=blue:t=fill"),
            (cls.gray, "color=c=0x606060:s=640x360:r=20:d=0.8"),
        )
        for path, source in sources:
            cls.ff(["-f", "lavfi", "-i", source, "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "16", "-pix_fmt", "yuv420p", str(path)])
        cls.ff(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.13", str(cls.voice)])
        cls.renderer = FFmpegAssemblyRenderer(cls.ffmpeg)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    @classmethod
    def ff(cls, args):
        return subprocess.run(
            [cls.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"] + args,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30,
        ).stdout

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="case ", dir=self.root)
        self.addCleanup(self.temp.cleanup)
        self.outdir = Path(self.temp.name)
        self.output = self.outdir / "\u6210\u7247 \u7ed3\u679c.mp4"

    def plan(self, edit=None, *, source=None, duration=.5, known_duration=None,
             voice=False, voice_duration=0, schema=False, script="script", tts="narration"):
        metadata = {} if edit is None else {"edit": edit}
        return AssemblyPlanner(fps=20, resolution=(120, 120)).create_plan(
            [StoryboardShot(shot_id="s1", duration_seconds=duration, script_text=script,
                            tts_text=tts, metadata=metadata)],
            [MaterialAsset(asset_id="video", file_path=str(source or self.temporal),
                           duration_seconds=known_duration)],
            [VoiceoverSegment(shot_id="s1", text="different voiceover words", audio_path=str(self.voice),
                              duration_seconds=voice_duration)] if voice else [],
            as_schema=schema,
        )

    def render(self, plan, *, size=(120, 120), subtitles=False):
        result = self.renderer.render(
            plan, str(self.output), dry_run=False, overwrite=True, fps=20, resolution=size,
            subtitles=subtitles, preset="ultrafast", crf=16, video_bitrate="2M",
            ffprobe_path=self.ffprobe,
        )
        self.assertFalse(list(self.outdir.glob(".mimo-render-*")))
        return result

    def frame(self, seconds=.1, size=(120, 120)):
        data = self.ff(["-ss", str(seconds), "-i", str(self.output), "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
        self.assertEqual(len(data), size[0] * size[1] * 3)
        return data

    def pixel(self, frame, x, y, width=120):
        offset = (y * width + x) * 3
        return tuple(frame[offset:offset + 3])

    def assert_color(self, frame, channel, x=60, y=60, width=120):
        pixel = self.pixel(frame, x, y, width)
        self.assertGreater(pixel[channel], 200, pixel)
        self.assertTrue(all(value < 45 for i, value in enumerate(pixel) if i != channel), pixel)

    def rms(self, start):
        samples = array.array("f")
        samples.frombytes(self.ff(["-ss", str(start), "-i", str(self.output), "-t", "0.08", "-vn",
                                  "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"]))
        if sys.byteorder != "little":
            samples.byteswap()
        self.assertTrue(samples)
        return (sum(x * x for x in samples) / len(samples)) ** .5

    def require_font(self):
        try:
            media_render.discover_font()
        except RenderError as exc:
            self.skipTest(str(exc))

    def test_source_selection_freezes_selected_end_not_later_frames(self):
        plan = self.plan({"source_in": .65, "source_out": 1.1}, duration=1.5)
        before = copy.deepcopy(plan)
        result = self.render(plan)
        self.assertEqual(plan, before)
        self.assertAlmostEqual(result["duration_seconds"], 1.5, places=2)
        self.assertTrue(any("last frame was frozen" in warning for warning in result["warnings"]))
        self.assert_color(self.frame(.1), 1)
        self.assert_color(self.frame(1.4), 1)
        self.render(self.plan({"source_in": 1.25, "source_out": 1.7}, duration=.4))
        self.assert_color(self.frame(), 2)

    def test_cover_crop_coordinates_both_axes(self):
        for source, axis in ((self.horizontal, "crop_x"), (self.vertical, "crop_y")):
            for coordinate, channel in ((0, 0), (.5, 1), (1, 2)):
                with self.subTest(axis=axis, coordinate=coordinate):
                    self.render(self.plan({"fit": "cover", axis: coordinate}, source=source))
                    frame = self.frame()
                    for x, y in ((15, 15), (60, 60), (105, 105)):
                        self.assert_color(frame, channel, x, y)

    def test_contain_ignores_crop_and_centers_black_bars(self):
        frames = []
        for coordinate in (0, 1):
            self.render(self.plan({"fit": "contain", "crop_x": coordinate, "crop_y": coordinate},
                                  source=self.horizontal))
            frames.append(self.frame())
        self.assertEqual(frames[0], frames[1])
        for y in (10, 110):
            self.assertLess(max(self.pixel(frames[0], 60, y)), 5)
        for x, channel in ((20, 0), (60, 1), (100, 2)):
            self.assert_color(frames[0], channel, x, 60)

    def test_per_ratio_framing_overrides_legacy_without_mutating_plan(self):
        edit = {"crop_x": .5, "framing": {"9:16": {"crop_x": 0}, "16:9": {"crop_x": 1}}}
        plan = self.plan(edit, source=self.horizontal)
        before = copy.deepcopy(plan)
        for size, channel in (((90, 160), 0), ((160, 90), 2), ((120, 120), 1)):
            result = self.render(plan, size=size)
            self.assert_color(self.frame(size=size), channel, size[0] // 2, size[1] // 2, size[0])
            self.assertEqual(result["visual_processing"][0]["framing"]["crop_x"], {0: 0, 2: 1, 1: .5}[channel])
        self.assertEqual(plan, before)

    def test_fade_black_preserves_duration_and_full_audio(self):
        shots = [StoryboardShot(shot_id=f"s{i}", duration_seconds=1.13, metadata={"edit": {
            "source_in": 0 if i == 0 else 1.2, "source_out": .55 if i == 0 else 1.75,
            "transition": {"kind": "fade_black", "duration_seconds": .3}}}) for i in range(2)]
        plan = AssemblyPlanner().create_plan(shots, [MaterialAsset(asset_id="m", file_path=str(self.temporal))],
            [VoiceoverSegment(shot_id=f"s{i}", audio_path=str(self.voice), duration_seconds=1.13) for i in range(2)])
        result = self.render(plan)
        self.assertAlmostEqual(result["duration_seconds"], 2.3, places=2)
        self.assert_color(self.frame(.1), 0)
        self.assertLess(max(self.pixel(self.frame(1.1), 60, 60)), 60)
        self.assertLess(max(self.pixel(self.frame(1.15), 60, 60)), 5)
        self.assert_color(self.frame(1.5), 2)
        self.assertGreater(self.rms(1.01), .03)
        self.assertGreater(self.rms(1.18), .03)
        self.assertEqual(result["visual_processing"][1]["fade_out_seconds"], 0)

    def test_probe_rejects_out_of_range_auto_match_before_any_encoding(self):
        original = media_render._Runner.run
        phases = []
        def watch(runner, argv, cwd, phase, tick=None):
            phases.append(phase)
            return original(runner, argv, cwd, phase, tick)
        for edit in ({"source_out": 2}, {"source_in": 1.9}, {"source_in": .2, "source_out": 1.9}):
            for known in (None, 10):
                with self.subTest(edit=edit, known=known):
                    self.output.write_bytes(b"previous export")
                    phases.clear()
                    with patch.object(media_render._Runner, "run", watch), self.assertRaises(RenderError) as caught:
                        self.render(self.plan(edit, known_duration=known))
                    self.assertEqual(caught.exception.code, "invalid_plan")
                    self.assertNotIn("shot encoding", phases)
                    self.assertEqual(self.output.read_bytes(), b"previous export")
                    self.assertFalse(list(self.outdir.glob(".mimo-render-*")))

    def test_shortage_error_uses_probe_not_optimistic_metadata(self):
        self.output.write_bytes(b"previous export")
        plan = self.plan({"shortage": "error"}, source=self.horizontal, duration=1.2, known_duration=10)
        with self.assertRaisesRegex(RenderError, "shorter than the target"):
            self.render(plan)
        self.assertEqual(self.output.read_bytes(), b"previous export")
        self.assertFalse(list(self.outdir.glob(".mimo-render-*")))
        # Rounding a valid target up to a video frame must not count as a shortage.
        result = self.render(self.plan({"shortage": "error"}, source=self.horizontal, duration=.799))
        self.assertEqual(result["warnings"], [])
        self.assertAlmostEqual(result["duration_seconds"], .8, places=2)

    def test_manual_duration_rejected_against_real_audio_before_rounding(self):
        self.output.write_bytes(b"previous export")
        plan = self.plan({"duration_mode": "manual"}, duration=1.12, voice=True, voice_duration=.2)
        with self.assertRaisesRegex(RenderError, "shorter than measured narration"):
            self.render(plan)
        self.assertEqual(self.output.read_bytes(), b"previous export")
        self.assertFalse(list(self.outdir.glob(".mimo-render-*")))

    def test_manual_duration_keeps_entire_narration_then_pads_silence(self):
        result = self.render(self.plan({"duration_mode": "manual"}, duration=1.6,
                                       voice=True, voice_duration=.2))
        self.assertAlmostEqual(result["duration_seconds"], 1.6, places=2)
        self.assertGreater(self.rms(1.03), .03)
        self.assertLess(self.rms(1.4), .0001)
        result = self.render(self.plan({"duration_mode": "manual"}, duration=1.13, voice=True))
        self.assertAlmostEqual(result["duration_seconds"], 1.15, places=2)
        self.assertGreater(self.rms(1.03), .03)

    def test_narration_duration_uses_measured_audio_despite_stale_estimate(self):
        result = self.render(self.plan({"duration_mode": "narration"}, duration=.2,
                                       voice=True, voice_duration=.1))
        self.assertAlmostEqual(result["duration_seconds"], 1.15, places=2)
        self.assertGreater(self.rms(1.03), .03)
        result = self.render(self.plan({"duration_mode": "narration"}, duration=.65))
        self.assertAlmostEqual(result["duration_seconds"], .65, places=2)

    def test_missing_first_narration_keeps_silence_and_next_shot_audio(self):
        plan = AssemblyPlanner(fps=20).create_plan(
            [StoryboardShot(shot_id="missing", duration_seconds=.5, metadata={"edit": {}}),
             StoryboardShot(shot_id="voiced", duration_seconds=4, metadata={"edit": {}})],
            [MaterialAsset(asset_id="video", file_path=str(self.temporal))],
            [VoiceoverSegment(shot_id="voiced", audio_path=str(self.voice), duration_seconds=1.13)],
        )
        result = self.render(plan)
        self.assertAlmostEqual(result["duration_seconds"], 1.65, places=2)
        self.assertLess(self.rms(.1), .0001)
        self.assertGreater(self.rms(.6), .03)
        self.assertGreater(self.rms(1.5), .03)

    def test_schema_and_track_only_plans_carry_edits_and_file_uri(self):
        edit = {"source_in": 1.25, "source_out": 1.75, "duration_mode": "manual"}
        for kind in ("schema", "tracks"):
            with self.subTest(kind=kind):
                plan = self.plan(edit, schema=kind == "schema")
                if kind == "schema":
                    plan.items[0].metadata["video_clip"]["source_path"] = self.temporal.as_uri()
                else:
                    plan = {"tracks": plan["tracks"], "timeline": plan["timeline"]}
                self.render(plan)
                self.assert_color(self.frame(), 2)

    def test_eight_manual_shots_render_full_39_5_seconds(self):
        shots = [StoryboardShot(shot_id=f"s{i}", duration_seconds=8 if i == 0 else 4.5,
                                metadata={"edit": {"duration_mode": "manual", "source_in": .5,
                                                   "source_out": 1.7, "shortage": "freeze"}})
                 for i in range(8)]
        voices = [VoiceoverSegment(shot_id=s.shot_id, audio_path=str(self.voice), duration_seconds=1.13)
                  for s in shots]
        plan = AssemblyPlanner(fps=20).create_plan(
            shots, [MaterialAsset(asset_id="video", file_path=str(self.temporal), duration_seconds=1.8)],
            voices, as_schema=True,
        )
        self.assertEqual(plan.total_duration_seconds, 39.5)
        self.assertEqual([item.duration_seconds for item in plan.items], [8] + [4.5] * 7)
        result = self.render(plan)
        self.assertAlmostEqual(result["duration_seconds"], 39.5, places=2)
        self.assertEqual(result["segment_count"], 8)
        self.assertEqual(len(result["warnings"]), 8)
        self.assertGreater(self.rms(8.05), .03)
        self.assertLess(self.rms(7.8), .0001)
        self.assert_color(self.frame(39.4), 2)

    def test_independent_subtitle_and_narration_fallback_render_identically(self):
        self.require_font()
        text = "\u72ec\u7acb\u5b57\u5e55\n\u4e0b\u4e00\u884c"
        style = {"position": "center", "font_size": 72, "color": "#00FF00"}
        edit = {"subtitle": dict(style, text=text)}
        plan = self.plan(edit, source=self.gray, voice=True, script="unrelated script", tts="spoken words")
        self.render(plan, size=(640, 360), subtitles=True)
        independent = self.frame(size=(640, 360))
        self.assertGreater(self.rms(1.03), .03)
        self.render(self.plan({"subtitle": style}, source=self.gray, voice=True, tts=text),
                    size=(640, 360), subtitles=True)
        self.assertEqual(independent, self.frame(size=(640, 360)))
        self.render(self.plan({"subtitle": dict(style, text="OTHER")}, source=self.gray, voice=True),
                    size=(640, 360), subtitles=True)
        self.assertNotEqual(independent, self.frame(size=(640, 360)))

    def test_explicit_empty_subtitle_hides_text_and_needs_no_font(self):
        for schema in (False, True):
            with self.subTest(schema=schema):
                plan = self.plan({"subtitle": {"text": ""}}, source=self.gray, voice=True, schema=schema)
                with patch("media_render.discover_font", side_effect=AssertionError("No font needed")):
                    self.render(plan, size=(640, 360), subtitles=True)
                hidden = self.frame(size=(640, 360))
                self.render(plan, size=(640, 360), subtitles=False)
                self.assertEqual(hidden, self.frame(size=(640, 360)))
                self.assertGreater(self.rms(1.03), .03)

    def subtitle_pixels(self, frame):
        return [(i // 3 % 640, i // 3 // 640) for i in range(0, len(frame), 3)
                if frame[i + 1] > 170 and frame[i] < 100 and frame[i + 2] < 100]

    def test_subtitle_position_font_size_color_and_background_pixels(self):
        self.require_font()
        centers = []
        sizes = []
        for position in ("top", "center", "bottom"):
            for font_size in (48, 96):
                with self.subTest(position=position, font_size=font_size):
                    style = dict(text="MMMM", position=position, font_size=font_size, color="#00FF00")
                    self.render(self.plan({"subtitle": style}, source=self.gray), size=(640, 360), subtitles=True)
                    frame = self.frame(size=(640, 360))
                    pixels = self.subtitle_pixels(frame)
                    self.assertGreater(len(pixels), 50)
                    if font_size == 96:
                        centers.append(sum(y for _, y in pixels) / len(pixels))
                    if position == "center":
                        sizes.append(len(pixels))
        self.assertLess(centers[0], 80)
        self.assertTrue(145 < centers[1] < 210, centers)
        self.assertGreater(centers[2], 280)
        self.assertGreater(sizes[1], sizes[0] * 2)
        dark_counts = []
        for background in (False, True):
            style = dict(text="M    M", position="center", font_size=96,
                         color="#00FF00", background=background)
            self.render(self.plan({"subtitle": style}, source=self.gray), size=(640, 360), subtitles=True)
            frame = self.frame(size=(640, 360))
            dark_counts.append(sum(max(frame[i:i + 3]) < 35 for i in range(0, len(frame), 3)))
        self.assertGreater(dark_counts[1], dark_counts[0] + 500)

    def test_subtitle_ass_injection_is_rendered_as_safe_literal_text(self):
        self.require_font()
        text = "{\\pos(0,0)\\alpha&HFF&}TEXT\\N\nDialogue: fake"
        safe = text.replace("\\", "\uff3c").replace("{", "\uff5b").replace("}", "\uff5d")
        frames = []
        for value in (text, safe):
            plan = self.plan({"subtitle": {"text": value, "font_size": 48}}, source=self.gray)
            self.render(plan, size=(640, 360), subtitles=True)
            frames.append(self.frame(size=(640, 360)))
        self.assertEqual(frames[0], frames[1])
        blank = self.plan({"subtitle": {"text": ""}}, source=self.gray)
        self.render(blank, size=(640, 360), subtitles=True)
        self.assertNotEqual(frames[0], self.frame(size=(640, 360)))

    def test_legacy_plan_retains_default_center_crop_and_subtitles(self):
        self.require_font()
        plan = self.plan(source=self.horizontal, voice=True, known_duration=.8)
        result = self.render(plan, subtitles=True)
        legacy = self.frame()
        self.assert_color(legacy, 1, y=25)
        self.assertAlmostEqual(result["duration_seconds"], 1.15, places=2)
        for row in plan["segments"]:
            for key in ("edit", "tts_text"):
                row.pop(key, None)
            for key in ("edit", "tts_text", "script_text", "crop_x", "crop_y", "shortage"):
                row["video_clip"].pop(key, None)
        self.render(plan, subtitles=True)
        self.assertEqual(legacy, self.frame())
        self.assertGreater(self.rms(1.03), .03)

    def test_timed_captions_have_real_visible_and_empty_intervals(self):
        self.require_font()
        style = {"timing_mode": "cues", "text": "", "position": "center", "font_size": 96, "color": "#00FF00",
                 "cues": [{"start": .2, "end": .4, "text": "MMMM"}, {"start": .6, "end": .75, "text": "NEXT"}]}
        result = self.render(self.plan({"subtitle": style}, source=self.gray, duration=.8), size=(640, 360), subtitles=True)
        self.assertEqual(len(result["shot_timing"][0]["subtitle_events"]), 2)
        for time in (.1, .5):
            self.assertEqual(len(self.subtitle_pixels(self.frame(time, size=(640, 360)))), 0)
        for time in (.3, .65):
            self.assertGreater(len(self.subtitle_pixels(self.frame(time, size=(640, 360)))), 50)

    def test_real_lead_tail_and_narration_caption_window(self):
        self.require_font()
        before = self.voice.read_bytes()
        edit = {"lead_in_seconds": .4, "tail_out_seconds": .3, "subtitle": {
            "text": "MMMM", "timing_mode": "narration", "font_size": 96, "color": "#00FF00"}}
        plan = self.plan(edit, source=self.gray, voice=True, voice_duration=1.13)
        self.assertAlmostEqual(plan["timeline"]["duration"], 1.83)
        self.assertEqual(plan["tracks"][1]["clips"][0]["timeline_start"], .4)
        result = self.render(plan, size=(640, 360), subtitles=True)
        self.assertAlmostEqual(result["duration_seconds"], 1.85)
        self.assertLess(self.rms(.15), .0001)
        self.assertGreater(self.rms(.6), .03)
        self.assertGreater(self.rms(1.4), .03)
        self.assertLess(self.rms(1.7), .0001)
        self.assertEqual(len(self.subtitle_pixels(self.frame(.15, size=(640, 360)))), 0)
        self.assertGreater(len(self.subtitle_pixels(self.frame(.6, size=(640, 360)))), 50)
        self.assertEqual(len(self.subtitle_pixels(self.frame(1.7, size=(640, 360)))), 0)
        self.assertEqual(self.voice.read_bytes(), before)
        # Track-only plans must still match narration after its delayed start.
        self.render({"tracks": plan["tracks"], "timeline": plan["timeline"]})
        self.assertGreater(self.rms(.6), .03)

    def test_normalization_does_not_shorten_sample_based_lead(self):
        plan = self.plan({"lead_in_seconds": .4, "tail_out_seconds": .3}, voice=True, voice_duration=1.13)
        result = self.renderer.render(plan, str(self.output), dry_run=False, resolution=(120, 120), fps=20,
                                      subtitles=False, preset="ultrafast", audio_settings={"normalize_voice": True})
        self.assertAlmostEqual(result["duration_seconds"], 1.85)
        self.assertLess(self.rms(.2), .0001)
        self.assertGreater(self.rms(.6), .03)

    def test_actual_voice_timing_rejects_stale_cues_and_manual_gaps(self):
        self.output.write_bytes(b"existing")
        for edit in ({"subtitle": {"timing_mode": "cues", "cues": [{"start": 0, "end": 1.5, "text": "too long"}]}},
                     {"duration_mode": "manual", "lead_in_seconds": .8, "tail_out_seconds": .8}):
            plan = self.plan(edit, duration=2, voice=True, voice_duration=2 if "subtitle" in edit else .1)
            with self.assertRaises(RenderError):
                self.render(plan)
            self.assertEqual(self.output.read_bytes(), b"existing")

    def test_unconfirmed_estimated_cues_are_reported(self):
        self.require_font()
        edit = {"subtitle": {"timing_mode": "cues", "timing_source": "estimated", "cues": [{"start": 0, "end": .4, "text": "test"}]}}
        result = self.render(self.plan(edit), subtitles=True)
        self.assertTrue(any("估算" in text for text in result["warnings"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
