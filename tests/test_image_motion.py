import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media_assembler import FFmpegAssemblyRenderer
from media_render import discover_executable
from visual_edit import validate_visual_edit


class MotionContractTests(unittest.TestCase):
    def test_push_pull_and_still_are_the_only_motions(self):
        for motion in ({"kind": "push"}, {"kind": "pull", "zoom": 1.2}, {"kind": "none"}, {"zoom": 1.01}):
            self.assertEqual(validate_visual_edit({"motion": motion})["motion"], motion)
        for motion in ({"kind": "spin"}, {"zoom": 1.0}, {"zoom": 2}, {"kind": "push", "speed": 1}, "push"):
            with self.subTest(motion=motion), self.assertRaises(ValueError):
                validate_visual_edit({"motion": motion})


class RealImageMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="image_motion_")
        cls.root = Path(cls.temp.name)
        cls.ffmpeg = discover_executable("ffmpeg")
        cls.ffprobe = discover_executable("ffprobe", sibling=Path(cls.ffmpeg))
        cls.still = cls.root / "产品 照片.jpg"  # a 4:3 photo like an iPhone one: a white square centred on black
        cls.ff("-f", "lavfi", "-i", "color=black:s=1600x1200,drawbox=x=700:y=500:w=200:h=200:color=white:t=fill",
               "-frames:v", "1", str(cls.still))
        cls.video = cls.root / "clip.mp4"
        cls.ff("-f", "lavfi", "-i", "color=blue:s=320x180:r=30:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.video))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def ff(cls, *args):
        return subprocess.run([cls.ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-y", *args],
                              capture_output=True, check=True).stdout

    def render(self, name, rows):
        segments = [{"video_clip": {"source_path": str(path)}, "voiceover_clip": None, "duration": duration,
                     **({"edit": edit} if edit is not None else {})} for path, duration, edit in rows]
        return FFmpegAssemblyRenderer(self.ffmpeg).render({"segments": segments}, str(self.root / name), dry_run=False,
            resolution=(320, 180), fps=30, subtitles=False, preset="ultrafast")

    def frames(self, path):
        return subprocess.run([self.ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                               "stream=nb_read_frames", "-of", "csv=p=0", path], capture_output=True, text=True,
                              check=True).stdout.strip()

    def bright(self, path, frame):
        gray = self.ff("-i", path, "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1")
        return sum(value > 128 for value in gray) / len(gray)

    def test_push_zooms_in_pull_zooms_out_and_none_holds(self):
        sizes = {}
        for kind in ("push", "pull", "none"):
            result = self.render(f"{kind}.mp4", [(self.still, 1.5, {"motion": {"kind": kind, "zoom": 1.3}})])
            self.assertEqual(self.frames(result["output_path"]), "45")
            self.assertFalse(any("shorter than the target" in warning for warning in result.get("warnings", [])))
            sizes[kind] = self.bright(result["output_path"], 0), self.bright(result["output_path"], 44)
        first, last = sizes["push"]
        self.assertGreater(last, first * 1.5)  # the square grows by about 1.3 squared
        first, last = sizes["pull"]
        self.assertLess(last, first / 1.5)
        first, last = sizes["none"]
        self.assertAlmostEqual(last, first, delta=first * .05)

    def test_photos_without_edits_push_in_by_default_and_mix_with_video_frame_exactly(self):
        result = self.render("mixed.mp4", [(self.video, 1.0, None), (self.still, 0.5, None), (self.video, 1.5, None)])
        self.assertEqual(self.frames(result["output_path"]), "90")
        result = self.render("default.mp4", [(self.still, 1.0, None)])
        self.assertGreater(self.bright(result["output_path"], 29), self.bright(result["output_path"], 0))


if __name__ == "__main__":
    unittest.main()
