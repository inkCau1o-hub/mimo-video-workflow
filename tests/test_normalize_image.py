import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageCms

from tools.normalize_image import ImageNormalizeError, is_image, normalize_image

DISPLAY_P3 = Path("/System/Library/ColorSync/Profiles/Display P3.icc")


class NormalizeImageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="normalize_")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.out = self.root / "normalized"

    def photo(self, name, size=(64, 32), color=(255, 0, 0), **save):
        path = self.root / name
        image = Image.new("RGB", size, color)
        image.paste((0, 0, 255), (0, 0, size[0] // 2, size[1]))  # left half blue
        image.save(path, **save)
        return path

    def test_only_photo_formats_count_as_images(self):
        self.assertTrue(all(is_image(name) for name in ("a.HEIC", "b.heif", "c.jpg", "d.JPEG", "e.png", "f.webp")))
        self.assertFalse(any(is_image(name) for name in ("a.mp4", "b.mov", "c.gif", "d")))

    def test_exif_orientation_is_baked_into_the_pixels(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # shown rotated 90 degrees clockwise
        result = normalize_image(self.photo("rotated.jpg", exif=exif.tobytes()), self.out)
        with Image.open(result) as image:
            self.assertEqual(image.size, (32, 64))
            self.assertNotIn(0x0112, image.getexif())
            top, bottom = image.getpixel((16, 4)), image.getpixel((16, 60))
        self.assertGreater(top[2], 200)  # the blue left edge is now on top
        self.assertGreater(bottom[0], 200)

    @unittest.skipUnless(DISPLAY_P3.is_file(), "needs the Display P3 profile")
    def test_wide_gamut_colors_are_converted_to_srgb(self):
        profile = ImageCms.ImageCmsProfile(str(DISPLAY_P3)).tobytes()
        source = self.root / "p3.jpg"
        Image.new("RGB", (16, 16), (40, 180, 90)).save(source, icc_profile=profile, quality=100)
        with Image.open(normalize_image(source, self.out)) as image:
            red, green, _ = image.getpixel((8, 8))
        self.assertLess(red, 15)  # Display P3 (40, 180, 90) is sRGB (0, 183, 78); untouched it would stay 40
        self.assertGreater(green, 170)

    def test_large_photos_shrink_and_small_ones_keep_their_size(self):
        large = normalize_image(self.photo("large.png", size=(5000, 100)), self.out, max_edge=4096)
        small = normalize_image(self.photo("small.png", size=(100, 50)), self.out, max_edge=4096)
        with Image.open(large) as image:
            self.assertEqual(image.size, (4096, 82))
        with Image.open(small) as image:
            self.assertEqual(image.size, (100, 50))

    def test_transparency_is_flattened_on_white(self):
        source = self.root / "alpha.png"
        Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(source)
        with Image.open(normalize_image(source, self.out)) as image:
            self.assertEqual(image.mode, "RGB")
            self.assertTrue(all(channel > 250 for channel in image.getpixel((4, 4))))

    def test_output_is_named_by_content_and_reused(self):
        source = self.photo("same.jpg")
        first = normalize_image(source, self.out)
        stamp = first.stat().st_mtime_ns
        self.assertEqual(normalize_image(source, self.out), first)
        self.assertEqual(first.stat().st_mtime_ns, stamp)
        self.assertEqual(first.suffix, ".jpg")
        self.assertNotEqual(normalize_image(self.photo("other.jpg", color=(0, 255, 0)), self.out), first)

    def test_unreadable_photo_is_rejected_without_leaving_output(self):
        broken = self.root / "broken.jpg"
        broken.write_bytes(b"not an image")
        with self.assertRaises(ImageNormalizeError):
            normalize_image(broken, self.out)
        self.assertEqual(list(self.out.glob("*")) if self.out.exists() else [], [])

    def test_heic_on_mac_is_converted_by_sips_to_srgb_without_upscaling(self):
        calls = []

        def sips(argv, **kwargs):
            calls.append(argv)
            if "-g" in argv:
                return type("Done", (), {"returncode": 0, "stdout": "  pixelWidth: 5712\n  pixelHeight: 4284\n"})()
            Image.new("RGB", (4096, 3072)).save(argv[argv.index("--out") + 1], "JPEG")
            return type("Done", (), {"returncode": 0, "stdout": ""})()

        source = self.root / "IMG_0001.HEIC"
        source.write_bytes(b"heic fixture")
        result = normalize_image(source, self.out, max_edge=4096, runner=sips, platform="darwin")
        convert = calls[-1]
        self.assertEqual(convert[:5], ["sips", "-s", "format", "jpeg", "-s"])
        self.assertIn("--matchTo", convert)
        self.assertTrue(convert[convert.index("--matchTo") + 1].endswith("sRGB Profile.icc"))
        self.assertEqual(convert[convert.index("-Z") + 1], "4096")
        self.assertTrue(result.is_file())


if __name__ == "__main__":
    unittest.main()
