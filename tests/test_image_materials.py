import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# macOS keeps temp dirs behind the /var -> /private/var symlink; stored project paths are resolved.
tempfile.tempdir = str(Path(tempfile.gettempdir()).resolve())

from PIL import Image

from backend.project_store import ProjectStore
from backend.workflow_api import WorkflowBackend
from material_catalog import MaterialCatalog


class Analyzer:
    """Stands in for the vision model; records which file it was shown."""

    def __init__(self):
        self.seen = []

    def analyze_video_material(self, path, output_dir, **kwargs):
        self.seen.append(path)
        return {"video_path": path, "frames": [path], "analysis": '{"material_type": "product_demo", "scene_summary": "板卡"}'}


class ImageMaterialTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="image_materials_")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.backend = WorkflowBackend(ProjectStore(self.root / "store"), run_inline_jobs=True)
        self.pid = self.backend.create_project({"name": "图片素材"})["status"]["project_id"]
        self.state = self.backend.store.get_state(self.pid)
        self.analyzer = Analyzer()
        self.state.material_catalog = MaterialCatalog(analyzer=self.analyzer, output_dir=str(self.root / "catalog"))

    def photo(self, directory=None, name="相机 照片.jpg"):
        path = (directory or self.root) / name
        image = Image.new("RGB", (64, 32), (255, 0, 0))
        image.paste((0, 0, 255), (0, 0, 32, 32))
        exif = Image.Exif()
        exif[0x0112] = 6  # shown rotated 90 degrees clockwise
        image.save(path, exif=exif.tobytes())
        return path

    def tag(self, **payload):
        return self.backend.tag_materials(self.pid, payload)["job"]

    def test_tagging_a_photo_imports_an_upright_jpeg_without_a_duration(self):
        job = self.tag(material_paths=[str(self.photo())])
        self.assertEqual(job["status"], "succeeded", job.get("error"))
        asset = self.state.material_assets[0]
        path = Path(asset.file_path)
        self.assertEqual(path.parent, self.state.project_dir / "media" / "materials")
        self.assertEqual(path.suffix, ".jpg")
        with Image.open(path) as image:
            self.assertEqual(image.size, (32, 64))
        self.assertIsNone(asset.duration_seconds)
        self.assertEqual(asset.metadata["asset_type"], "image")
        self.assertEqual(self.analyzer.seen, [str(path)])  # the model is shown the upright photo

    def test_photo_folders_and_uploads_count_as_materials_but_not_as_reference(self):
        folder = self.root / "素材"
        folder.mkdir()
        self.photo(folder, "a.jpg")
        (folder / "notes.txt").write_text("not media", encoding="utf-8")
        job = self.tag(material_dir=str(folder))
        self.assertEqual(job["status"], "succeeded", job.get("error"))
        self.assertEqual(len(self.state.material_assets), 1)
        upload = [{"filename": "IMG_0001.HEIC", "content": b"heic fixture"}]
        stored = self.backend.upload_reference_videos(self.pid, upload, materials=True)["uploaded_videos"][0]
        self.assertTrue(stored["file_path"].endswith(".HEIC"))
        with self.assertRaises(ValueError):
            self.backend.upload_reference_videos(self.pid, upload)

    def test_folder_scans_skip_the_hidden_files_macos_leaves_on_external_drives(self):
        # On exFAT and FAT drives macOS writes a "._name" companion next to files; they are not photos or videos.
        folder = self.root / "外接盘素材"
        (folder / ".Trashes").mkdir(parents=True)
        self.photo(folder, "a.jpg")
        (folder / "._a.jpg").write_bytes(b"\x00\x05\x16\x07 AppleDouble")
        (folder / ".Trashes" / "old.jpg").write_bytes(b"deleted")
        job = self.tag(material_dir=str(folder))
        self.assertEqual(job["status"], "succeeded", job.get("error"))
        self.assertEqual(len(self.state.material_assets), 1)
        self.assertEqual(len(self.analyzer.seen), 1)
        for name in ("clip.mp4", "._clip.mp4", ".Trashes/old.mp4"):
            (folder / name).write_bytes(b"video")
        self.assertEqual(self.state.material_catalog.scan_videos(str(folder)), [str(folder / "clip.mp4")])

    def test_photos_have_no_segments_to_index(self):
        self.assertEqual(self.tag(material_paths=[str(self.photo())])["status"], "succeeded")
        with self.assertRaisesRegex(ValueError, "图片素材"):
            self.backend.index_material_segments(self.pid, {"asset_ids": [self.state.material_assets[0].asset_id]})

    def test_an_unreadable_photo_fails_the_job_with_a_clear_message(self):
        broken = self.root / "坏图.jpg"
        broken.write_bytes(b"not an image")
        job = self.tag(material_paths=[str(broken)])
        self.assertEqual(job["status"], "failed")
        self.assertIn("无法读取图片", job["error"])
        self.assertEqual(self.state.material_assets, [])


if __name__ == "__main__":
    unittest.main()
