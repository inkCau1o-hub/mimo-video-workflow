from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.editor_history import HISTORY_LIMIT, validate_shots
from backend.project_store import ProjectStore
from backend.server import create_server
from backend.workflow_api import WorkflowBackend
from production_schema import MaterialAsset, VoiceoverSegment


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_editor_")
        self.backend = WorkflowBackend(ProjectStore(self.tmp.name))
        self.pid = self.backend.create_project({"name": "editor"})["status"]["project_id"]
        self.state = self.backend.store.get_state(self.pid)
        self.aid = self.state.current_activity.activity_id
        self.video = self.state.project_dir / "media" / "素材 空格.mp4"
        self.video.parent.mkdir(parents=True)
        self.video.write_bytes(b"video fixture")
        self.audio = self.state.project_dir / "audio.mp3"
        self.audio.write_bytes(b"audio fixture")
        self.output = self.state.project_dir / "output.mp4"
        self.output.write_bytes(b"export fixture")
        self.state.set_material_assets([MaterialAsset(asset_id="m1", file_path=str(self.video), duration_seconds=12)])
        self.shots = [self.shot("S01", "original one"), self.shot("S02", "original two")]
        self.state.set_storyboard_shots(self.shots)
        self.state.set_voiceover_segments([
            VoiceoverSegment(shot_id=shot["shot_id"], segment_id=f"v{index}", text=shot["tts_text"],
                             audio_path=str(self.audio), duration_seconds=3, start_seconds=index * 3)
            for index, shot in enumerate(self.shots)
        ])
        self.state.current_activity.metadata["exports"] = [{"output_path": str(self.output)}]
        self.state.current_activity.metadata["render_result"] = {"output_path": str(self.output)}
        self.backend.store.save_project(self.pid)

    def tearDown(self):
        self.tmp.cleanup()

    def shot(self, identifier, text):
        return {"shot_id": identifier, "title": identifier, "script_text": text, "tts_text": text,
                "duration_seconds": 4, "metadata": {"material_asset_id": "m1"}}

    def save(self, shots=None, **kwargs):
        return self.backend.set_storyboard_shots(self.pid, {"shots": shots or self.shots, "activity_id": self.aid, **kwargs})

    def history(self, action, **kwargs):
        return self.backend.edit_history(self.pid, {"action": action, "activity_id": self.aid, **kwargs})

    def test_subtitle_edit_keeps_audio_and_invalidates_current_export(self):
        self.shots[0]["metadata"]["edit"] = {"subtitle": {"text": "独立字幕\n第二行", "font_size": 44}}
        self.save()
        self.assertEqual(len(self.state.voiceover_segments), 2)
        self.assertEqual(self.state.voiceover_segments[0].audio_path, str(self.audio))
        self.assertNotIn("render_result", self.state.current_activity.metadata)
        self.assertEqual(len(self.state.current_activity.metadata["exports"]), 1)
        self.assertTrue(self.output.exists())

    def test_framing_transition_history_roundtrip_preserves_narration(self):
        edit = {"framing": {"9:16": {"crop_x": .8}, "16:9": {"fit": "contain"}},
                "transition": {"kind": "fade_black", "duration_seconds": .25}}
        self.shots[0]["metadata"]["edit"] = edit
        self.save()
        self.history("undo")
        self.history("redo")
        reopened = WorkflowBackend(ProjectStore(self.tmp.name)).store.get_state(self.pid)
        self.assertEqual(reopened.storyboard_analysis.shots[0].metadata["edit"]["framing"], edit["framing"])
        self.assertEqual(reopened.storyboard_analysis.shots[0].metadata["edit"]["transition"], edit["transition"])
        self.assertEqual(len(reopened.voiceover_segments), 2)
        self.assertEqual(self.audio.read_bytes(), b"audio fixture")

    def test_invalid_visual_settings_save_is_atomic(self):
        before = self.state.project.to_dict()
        for edit in ({"framing": []}, {"framing": {"9:16": {"crop_x": True}}},
                     {"transition": {"kind": "crossfade"}}, {"transition": {"duration_seconds": 0}}):
            self.shots[0]["metadata"]["edit"] = edit
            with self.assertRaises(ValueError):
                self.save()
            self.assertEqual(self.state.project.to_dict(), before)

    def test_timed_captions_gaps_history_and_reopen_preserve_audio(self):
        edit = {"lead_in_seconds": .25, "tail_out_seconds": .75, "subtitle": {
            "timing_mode": "cues", "cues": [{"start": .3, "end": 2.8, "text": "correct brand"}]}}
        self.shots[0]["metadata"]["edit"] = edit
        self.save()
        self.assertEqual([v.start_seconds for v in self.state.voiceover_segments], [.25, 4])
        self.assertEqual(self.state.voiceover_segments[0].duration_seconds, 3)
        self.history("undo")
        self.assertEqual([v.start_seconds for v in self.state.voiceover_segments], [0, 3])
        self.history("redo")
        self.assertEqual([v.start_seconds for v in self.state.voiceover_segments], [.25, 4])
        reopened = WorkflowBackend(ProjectStore(self.tmp.name)).store.get_state(self.pid)
        self.assertEqual(reopened.storyboard_analysis.shots[0].metadata["edit"]["subtitle"]["cues"], edit["subtitle"]["cues"])
        self.assertEqual(self.audio.read_bytes(), b"audio fixture")
        self.assertEqual(len(reopened.voiceover_segments), 2)
        self.assertEqual(len(reopened.current_activity.metadata["exports"]), 1)

    def test_invalid_cue_or_gap_save_is_atomic(self):
        before = self.state.project.to_dict()
        for edit in ({"lead_in_seconds": 2, "duration_mode": "manual"},
                     {"subtitle": {"timing_mode": "cues", "cues": [{"start": 0, "end": 3.2, "text": "too long"}]}}):
            self.shots[0]["metadata"]["edit"] = edit
            with self.assertRaises(ValueError):
                self.save()
            self.assertEqual(self.state.project.to_dict(), before)

    def test_new_voice_must_fit_existing_timed_captions_at_render_plan(self):
        self.shots[0]["metadata"]["edit"] = {"subtitle": {"timing_mode": "cues", "cues": [{"start": 0, "end": 2.8, "text": "caption"}]}}
        self.save()
        voices = [v.to_dict() for v in self.state.voiceover_segments]
        voices[0]["duration_seconds"] = 2
        self.state.set_voiceover_segments(voices)
        with self.assertRaisesRegex(ValueError, "字幕超出"):
            self.state.build_assembly_plan(save=False)

    def test_reorder_delete_duplicate_and_restore_retains_media(self):
        clone = deepcopy(self.shots[0])
        clone["shot_id"] = "S03"
        clone["metadata"]["duplicate_of"] = "S01"
        self.save([self.shots[1], clone, self.shots[0]])
        self.assertEqual([v.shot_id for v in self.state.voiceover_segments], ["S02", "S03", "S01"])
        self.assertEqual([v.start_seconds for v in self.state.voiceover_segments], [0, 3, 6])
        self.assertEqual(self.state.voiceover_segments[1].audio_path, str(self.audio))
        self.save([self.shots[1]])
        self.assertEqual(len(self.state.voiceover_segments), 1)
        self.history("undo")
        self.assertEqual(len(self.state.voiceover_segments), 3)
        self.assertTrue(self.audio.exists())
        self.assertTrue(self.output.exists())

    def test_narration_change_undo_redo_and_reopen(self):
        self.shots[0]["tts_text"] = "new narration"
        payload = self.save()
        self.assertEqual(len(self.state.voiceover_segments), 1)
        self.assertTrue(payload["history"]["can_undo"])
        self.history("undo")
        self.assertEqual(len(self.state.voiceover_segments), 2)
        self.assertEqual(self.state.storyboard_analysis.shots[0].tts_text, "original one")
        self.history("redo")
        self.assertEqual(len(self.state.voiceover_segments), 1)
        reopened = WorkflowBackend(ProjectStore(self.tmp.name))
        reopened.edit_history(self.pid, {"action": "undo", "activity_id": self.aid})
        self.assertEqual(len(reopened.store.get_state(self.pid).voiceover_segments), 2)

    def test_new_edit_discards_redo_but_restore_appends(self):
        self.shots[0]["title"] = "A"
        first = self.save()["history"]
        self.shots[0]["title"] = "B"
        self.save()
        self.history("undo")
        self.shots[0]["title"] = "C"
        third = self.save()["history"]
        self.assertFalse(third["can_redo"])
        restored = self.history("restore", revision_id=first["entries"][0]["id"])["history"]
        self.assertEqual(len(restored["entries"]), len(third["entries"]) + 1)
        self.assertEqual(self.state.storyboard_analysis.shots[0].title, "S01")

    def test_audio_generated_after_save_is_retained_for_redo(self):
        self.shots[0]["tts_text"] = "new narration"
        self.save()
        fresh = self.state.project_dir / "fresh.mp3"
        fresh.write_bytes(b"fresh")
        voices = self.state.voiceover_segments + [VoiceoverSegment(
            shot_id="S01", segment_id="fresh", text="new narration", audio_path=str(fresh), duration_seconds=4)]
        self.state.set_voiceover_segments(voices)
        self.history("undo")
        self.history("redo")
        self.assertEqual(self.state.voiceover_segments[0].audio_path, str(fresh))

    def test_stale_revision_cannot_override_new_edits(self):
        first = self.save()["history"]
        revision = first["entries"][first["cursor"]]["id"]
        self.shots[0]["title"] = "new"
        self.save(base_revision=revision)
        with self.assertRaisesRegex(ValueError, "版本已更新"):
            self.history("undo", base_revision=revision)
        self.assertEqual(self.state.storyboard_analysis.shots[0].title, "new")

    def test_background_storyboard_change_advances_editor_revision(self):
        first = self.save()["history"]
        revision = first["entries"][first["cursor"]]["id"]
        self.backend.jobs.run_inline = True
        updated = deepcopy(self.shots)
        updated[0]["title"] = "AI updated"
        def worker(state, context):
            state.set_storyboard_shots(updated)
            return {}
        job = self.backend._submit_activity_job("test.storyboard", self.pid, worker)["job"]
        self.assertEqual(job["status"], "succeeded")
        with self.assertRaisesRegex(ValueError, "版本已更新"):
            self.save(base_revision=revision)

    def test_reference_change_advances_editor_revision(self):
        first = self.save()["history"]
        revision = first["entries"][first["cursor"]]["id"]
        self.backend.set_reference_text(self.pid, {"text": "new reference"})
        with self.assertRaisesRegex(ValueError, "版本已更新"):
            self.save(base_revision=revision)

    def test_save_failure_rolls_back_history_memory_and_manifest(self):
        manifest = self.state.project_dir / "project.json"
        before = manifest.read_bytes()
        previous = self.state.to_dict()
        self.shots[0]["title"] = "not committed"
        with patch("production_schema.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.save()
        self.assertEqual(self.state.to_dict(), previous)
        self.assertEqual(manifest.read_bytes(), before)

    def test_history_moves_with_project_and_does_not_store_absolute_media_paths(self):
        self.shots[0]["tts_text"] = "new"
        self.save()
        manifest = json.loads((self.state.project_dir / "project.json").read_text(encoding="utf-8"))
        saved_history = manifest["activities"][0]["metadata"]["edit_history"]
        self.assertNotIn(str(self.state.project_dir), json.dumps(saved_history))
        relocated = Path(self.tmp.name) / "搬迁 空格" / self.state.project_dir.name
        shutil.copytree(self.state.project_dir, relocated)
        fresh = WorkflowBackend(ProjectStore(Path(self.tmp.name) / "other-index"))
        fresh.open_project({"path": str(relocated)})
        fresh.edit_history(self.pid, {"action": "undo", "activity_id": self.aid})
        audio = Path(fresh.store.get_state(self.pid).voiceover_segments[0].audio_path)
        self.assertEqual(audio, relocated / "audio.mp3")
        self.assertTrue(audio.is_file())

    def test_history_bounded_and_explicit_checkpoint(self):
        for index in range(HISTORY_LIMIT + 5):
            self.history("checkpoint", label=f"version {index}")
        history = self.backend.get_editor_history(self.pid)["history"]
        self.assertEqual(len(history["entries"]), HISTORY_LIMIT)
        self.assertEqual(history["cursor"], HISTORY_LIMIT - 1)
        self.assertEqual(history["entries"][-1]["label"], f"version {HISTORY_LIMIT + 4}")

    def test_stale_activity_is_rejected(self):
        self.backend.create_activity(self.pid, {"name": "other"})
        with self.assertRaises(ValueError):
            self.history("checkpoint")

    def test_invalid_edit_data_never_mutates_state(self):
        invalid = [{"source_in": -1}, {"source_in": 4, "source_out": 2}, {"source_out": 13},
                   {"source_in": 12}, {"crop_x": 2}, {"crop_y": float("nan")}, {"fit": "stretch"},
                   {"shortage": "loop"}, {"duration_mode": "cut"}, {"subtitle": {"font_size": 500}},
                   {"subtitle": {"color": "red;inject"}}, {"subtitle": {"position": "elsewhere"}},
                   {"subtitle": {"background": "yes"}}, {"subtitle": {"text": 33}}, []]
        before = self.state.to_dict()
        for edit in invalid:
            with self.subTest(edit=edit):
                shots = deepcopy(self.shots)
                shots[0]["metadata"]["edit"] = edit
                with self.assertRaises(ValueError):
                    self.save(shots)
                self.assertEqual(self.state.to_dict(), before)

    def test_manual_duration_cannot_cut_existing_narration(self):
        self.shots[0]["metadata"]["edit"] = {"duration_mode": "manual"}
        self.shots[0]["duration_seconds"] = 2
        with self.assertRaisesRegex(ValueError, "截断旁白"):
            self.save()
        self.shots[0]["tts_text"] = "shorter new line"
        self.save()
        self.assertEqual(len(self.state.voiceover_segments), 1)

    def test_manual_duration_advances_following_audio_start(self):
        self.shots[0]["metadata"]["edit"] = {"duration_mode": "manual"}
        self.shots[0]["duration_seconds"] = 6
        self.save()
        self.assertEqual(self.state.voiceover_segments[0].duration_seconds, 3)
        self.assertEqual(self.state.voiceover_segments[1].start_seconds, 6)

    def test_duplicate_ids_and_unsafe_ids_rejected(self):
        for identifier in ("S02", "../file", ""):
            shots = deepcopy(self.shots)
            shots[0]["shot_id"] = identifier
            if identifier:
                with self.assertRaises(ValueError):
                    self.save(shots)
            else:
                self.save(shots)

    def test_empty_subtitle_is_not_replaced_by_narration(self):
        self.shots[0]["metadata"]["edit"] = {"subtitle": {"text": ""}}
        self.save()
        self.assertEqual(self.state.storyboard_analysis.shots[0].metadata["edit"]["subtitle"]["text"], "")
        self.assertEqual(len(self.state.voiceover_segments), 2)


class EditorHTTPTests(unittest.TestCase):
    def test_history_routes_and_conflict(self):
        with tempfile.TemporaryDirectory(prefix="mimo_editor_http_") as root:
            server = create_server(port=0, projects_dir=root, quiet=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                pid = server.backend.create_project({"name": "api"})["status"]["project_id"]
                url = f"http://127.0.0.1:{server.server_port}/projects/{pid}/editor/history"
                with urllib.request.urlopen(url) as response:
                    self.assertEqual(json.load(response)["history"]["cursor"], -1)
                request = urllib.request.Request(url, data=json.dumps({"action": "checkpoint", "label": "test"}).encode(),
                                                 headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(json.load(response)["history"]["entries"][-1]["label"], "test")
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
