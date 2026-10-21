from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import wave
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.project_store import ProjectStore
from production_schema import (
    AssemblyPlan, MaterialAsset, ProductionProject, ReferenceCopy,
    StoryboardAnalysis, StoryboardShot, VoiceoverSegment, WorkflowActivity,
    load_json, project_from_storage, project_to_storage, save_json,
)
from workflow_state import WorkflowState


class LocalTTS:
    def __init__(self, fail=False, incomplete=False):
        self.fail = fail
        self.incomplete = incomplete

    def synthesize_segments(self, segments, output_dir, **kwargs):
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        results = []
        for segment in segments:
            path = directory / f"{segment['shot_id']}.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(b"\0\0" * 800)
            if self.fail:
                raise RuntimeError("synthetic TTS failure")
            results.append({**segment, "audio_path": str(path)})
        return results[:1] if self.incomplete else results


class LocalAnalyzer:
    def analyze(self, reference, **kwargs):
        return {"reference_copy": reference.to_dict(), "shots": sample_shots()}


class LocalCatalog:
    def analyze_batch(self, paths, output_dir=None, **kwargs):
        return [MaterialAsset(asset_id="asset", file_path=paths[0])]


class LocalRenderer:
    def validate_plan(self, *args, **kwargs):
        return {"ok": True}

    def build_command(self, plan, output_path, **kwargs):
        return ["ffmpeg", "-i", "media/input.mp4", output_path]


def sample_shots():
    return [
        {"shot_id": "S01", "script_text": "first", "tts_text": "first", "duration_seconds": 1.0},
        {"shot_id": "S02", "script_text": "second", "tts_text": "second", "duration_seconds": 2.0},
    ]


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mimo_portability_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def new_state(self, directory="demo.mimoproject"):
        return WorkflowState(
            project=ProductionProject(project_id="portable", name="demo", project_dir=str(self.root / directory)),
            output_dir=self.root,
        )

    def ready_state(self):
        state = self.new_state()
        state.set_reference_text("reference")
        state.set_copywriting("rewrite", {"text": "rewritten"})
        state.set_storyboard_shots(sample_shots())
        state.set_material_assets([MaterialAsset(asset_id="asset", file_path=str(state.project_dir / "media/input.mp4"))])
        state.generate_voiceovers(tts_client=LocalTTS())
        state.current_activity.assembly_plan = AssemblyPlan(output_path=str(state.activity_dir / "final.mp4"))
        state.current_activity.metadata["assembly_plan_path"] = str(state.activity_dir / "assembly_plan.json")
        state.ensure_current_activity()
        return state

    def test_atomic_replace_failure_keeps_original_and_removes_temp(self):
        target = self.root / "state.json"
        save_json({"old": True}, target)
        original = target.read_bytes()
        with patch("production_schema.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                save_json({"new": True}, target)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [target])

    def test_serialization_failure_keeps_original(self):
        target = self.root / "state.json"
        save_json({"old": True}, target)
        with self.assertRaises(TypeError):
            save_json({"bad": object()}, target)
        self.assertEqual(load_json(target), {"old": True})

    def test_atomic_write_flushes_before_replace(self):
        target = self.root / "state.json"
        with patch("production_schema.os.fsync") as flush:
            save_json({"unicode": "\u6d4b\u8bd5"}, target)
        flush.assert_called_once()
        self.assertEqual(load_json(target)["unicode"], "\u6d4b\u8bd5")

    def test_load_accepts_utf8_bom(self):
        target = self.root / "state.json"
        target.write_text('{"ok": true}', encoding="utf-8-sig")
        self.assertEqual(load_json(target), {"ok": True})

    def test_project_round_trip_preserves_runtime_absolute_paths(self):
        state = self.ready_state()
        activity = state.current_activity
        activity.metadata["material_paths"] = [state.material_assets[0].file_path]
        activity.reference_videos = [str(state.project_dir / "media/reference.mp4")]
        original = copy.deepcopy(state.to_dict())
        result = state.save_project()
        disk = load_json(result["project_path"])
        self.assertEqual(disk["project_dir"], ".")
        self.assertEqual(disk["schema_version"], 3)
        self.assertEqual(disk["activities"][0]["metadata"]["material_paths"], ["media/input.mp4"])
        self.assertTrue(disk["voiceover_segments"][0]["audio_path"].startswith("activities/"))
        self.assertEqual(state.voiceover_segments[0].audio_path, original["voiceover_segments"][0]["audio_path"])
        loaded = WorkflowState.load_project(result["project_path"])
        self.assertEqual(loaded.to_dict(), state.to_dict())
        self.assertFalse(Path(result["project_path"] + ".bak").exists())

    def test_move_then_read_audio_and_continue_processing(self):
        state = self.ready_state()
        state.save_project()
        moved = self.root / "moved.mimoproject"
        shutil.move(str(state.project_dir), str(moved))
        loaded = WorkflowState.load_project(moved)
        before = Path(loaded.voiceover_segments[0].audio_path)
        self.assertTrue(before.is_relative_to(moved))
        with wave.open(str(before), "rb") as audio:
            self.assertEqual(audio.getnframes(), 800)
        old_bytes = before.read_bytes()
        replacement = loaded.generate_voiceover_segment("S01", tts_client=LocalTTS())
        self.assertTrue(Path(replacement.audio_path).is_relative_to(moved))
        self.assertNotEqual(Path(replacement.audio_path), before)
        self.assertEqual(before.read_bytes(), old_bytes)
        self.assertIsNone(loaded.assembly_plan)
        loaded.save_project()
        self.assertEqual(WorkflowState.load_project(moved).voiceover_segments[0].audio_path, replacement.audio_path)

    def test_moved_project_audio_is_served_by_existing_media_api(self):
        from backend.server import create_server

        state = self.ready_state()
        state.save_project()
        moved = self.root / "moved.mimoproject"
        shutil.move(str(state.project_dir), str(moved))
        server = create_server(port=0, projects_dir=str(self.root / "server_store"), quiet=True)
        loaded = server.backend.store.load_project(moved)
        audio_path = loaded.voiceover_segments[0].audio_path
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            query = urllib.parse.urlencode({"path": audio_path})
            url = f"http://127.0.0.1:{server.server_port}/projects/portable/media?{query}"
            with urllib.request.urlopen(url, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), Path(audio_path).read_bytes())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())

    def test_mac_legacy_migration_preserves_exact_backup_once(self):
        directory = self.root / "migrated.mimoproject"
        directory.mkdir()
        manifest = directory / "project.json"
        old = "/Users/old/Movies/demo.mimoproject"
        data = {
            "project_id": "mac", "project_dir": old, "schema_version": 2,
            "reference_copy": {"source_text": "literal /Users/old stays text", "source_path": old + "/media/reference.mp4"},
            "material_assets": [{"asset_id": "one", "file_path": old + "/media/input.mp4", "metadata": {"frames": [old + "/frames/one.jpg"]}}],
            "voiceover_segments": [{"shot_id": "S01", "audio_path": old + "/voiceovers/one.wav"}],
            "assembly_plan": {"output_path": old + "/final.mp4", "metadata": {
                "tracks": [{"clips": [{"source_path": old + "/media/input.mp4"}]}],
                "renderer": {"command": ["ffmpeg", "-i", old + "/media/input.mp4", old + "/final.mp4"]},
            }},
        }
        save_json(data, manifest)
        original = manifest.read_bytes()
        state = WorkflowState.load_project(manifest)
        self.assertEqual(state.project_dir, directory)
        self.assertEqual(state.material_assets[0].file_path, str(directory / "media/input.mp4"))
        self.assertEqual(state.material_assets[0].metadata["frames"], [str(directory / "frames/one.jpg")])
        self.assertEqual(state.assembly_plan.metadata["renderer"]["command"][-1], str(directory / "final.mp4"))
        self.assertEqual(state.reference_text, data["reference_copy"]["source_text"])
        self.assertEqual(manifest.with_suffix(".json.bak").read_bytes(), original)
        self.assertEqual(load_json(manifest)["material_assets"][0]["file_path"], "media/input.mp4")
        state.save_project()
        WorkflowState.load_project(manifest)
        self.assertEqual(manifest.with_suffix(".json.bak").read_bytes(), original)

    def test_windows_uri_unc_and_mac_paths_relocate_lexically(self):
        cases = [
            ("C:\\Old\\Project", "c:\\old\\PROJECT\\media\\clip.mp4"),
            ("C:/Old/Project", "file:///C:/Old/Project/media/clip.mp4"),
            ("C:/Old/Project", "file://localhost/C:/Old/Project/media/clip.mp4"),
            ("\\\\server\\share\\Project", "file://server/share/Project/media/clip.mp4"),
            ("/Users/old/Project", "file:///Users/old/Project/media/clip.mp4"),
        ]
        for old, media in cases:
            with self.subTest(old=old, media=media):
                resolved = project_from_storage({"project_dir": old, "material_assets": [{"file_path": media}]}, self.root)
                self.assertEqual(resolved["material_assets"][0]["file_path"], str(self.root / "media/clip.mp4"))

    def test_external_missing_file_never_guesses_by_basename_or_prefix(self):
        (self.root / "clip.mp4").write_bytes(b"unrelated")
        outside = "/Users/old/Project-other/clip.mp4"
        resolved = project_from_storage({"project_dir": "/Users/old/Project", "material_assets": [
            {"file_path": outside}, {"file_path": "/Users/old/Project/missing/clip.mp4"},
        ]}, self.root)
        self.assertEqual(resolved["material_assets"][0]["file_path"], outside)
        self.assertEqual(resolved["material_assets"][1]["file_path"], str(self.root / "missing/clip.mp4"))
        self.assertFalse(Path(resolved["material_assets"][1]["file_path"]).exists())

    def test_urls_and_text_are_not_rewritten(self):
        data = {"project_dir": ".", "reference_videos": ["https://example.test/video.mp4"],
                "copywriting": {"rewrite": {"text": "media/input.mp4"}}, "metadata": {"source": "reference_transcript"}}
        resolved = project_from_storage(data, self.root)
        self.assertEqual(resolved["reference_videos"], data["reference_videos"])
        self.assertEqual(resolved["copywriting"], data["copywriting"])
        self.assertEqual(resolved["metadata"], data["metadata"])

    def test_external_paths_stay_absolute_when_saving(self):
        data = {"project_dir": str(self.root / "project"), "file_path": str(self.root / "outside.mp4")}
        stored = project_to_storage(data, self.root / "project")
        self.assertEqual(stored["file_path"], (self.root / "outside.mp4").as_posix())

    def test_atomic_migration_failure_leaves_old_manifest_and_backup(self):
        manifest = self.root / "project.json"
        save_json({"project_id": "old", "project_dir": "/Users/old/project", "schema_version": 1}, manifest)
        original = manifest.read_bytes()
        with patch("production_schema.os.replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                WorkflowState.load_project(manifest)
        self.assertEqual(manifest.read_bytes(), original)
        self.assertEqual(manifest.with_suffix(".json.bak").read_bytes(), original)

    def test_backup_flush_failure_does_not_leave_partial_backup(self):
        manifest = self.root / "project.json"
        save_json({"project_id": "old", "schema_version": 1}, manifest)
        original = manifest.read_bytes()
        with patch("workflow_state.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                WorkflowState.load_project(manifest)
        self.assertEqual(manifest.read_bytes(), original)
        self.assertFalse(manifest.with_suffix(".json.bak").exists())

    def test_reference_change_clears_all_downstream_but_materials(self):
        state = self.ready_state()
        state.set_reference_text("changed")
        self.assertEqual(state.current_activity.copywriting, {})
        self.assertIsNone(state.storyboard_analysis)
        self.assertEqual(state.voiceover_segments, [])
        self.assertIsNone(state.assembly_plan)
        self.assertEqual(len(state.material_assets), 1)
        self.assertNotIn("voiceover_dir", state.current_activity.metadata)
        self.assertNotIn("assembly_plan_path", state.current_activity.metadata)
        self.assertIsNone(state.project.assembly_plan)

    def test_reference_videos_change_clears_transcript_and_downstream(self):
        state = self.ready_state()
        state.set_reference_videos([self.root / "different.mp4"])
        self.assertEqual(state.reference_text, "")
        self.assertEqual(state.current_activity.transcript, {})
        self.assertIsNone(state.storyboard_analysis)
        self.assertEqual(state.voiceover_segments, [])
        self.assertIsNone(state.assembly_plan)

    def test_identical_reference_does_not_invalidate(self):
        state = self.ready_state()
        state.set_reference_text("reference")
        self.assertIsNotNone(state.storyboard_analysis)
        self.assertIsNotNone(state.assembly_plan)

    def test_rewrite_change_invalidates_but_title_change_does_not(self):
        state = self.ready_state()
        state.set_copywriting("titles", {"titles": ["title"]})
        self.assertIsNotNone(state.assembly_plan)
        state.set_copywriting("rewrite", {"text": "rewritten", "created_at": "later"})
        self.assertIsNotNone(state.assembly_plan)
        state.set_copywriting("rewrite", {"text": "changed"})
        self.assertEqual(state.reference_text, "reference")
        self.assertIsNone(state.storyboard_analysis)
        self.assertEqual(state.voiceover_segments, [])
        self.assertIsNone(state.assembly_plan)

    def test_storyboard_edit_retains_only_unchanged_narration_audio(self):
        state = self.ready_state()
        original = state.voiceover_segments[0].audio_path
        shots = sample_shots()
        shots[1]["tts_text"] = "new second"
        analysis = state.set_storyboard_shots(shots)
        self.assertEqual(analysis.shots[1].tts_text, "new second")
        self.assertEqual([item.shot_id for item in state.voiceover_segments], ["S01"])
        self.assertEqual(state.voiceover_segments[0].audio_path, original)
        self.assertEqual(state.current_activity.status["voiceover"], "partial")
        self.assertIsNone(state.assembly_plan)
        self.assertNotIn("voiceover_segments_path", state.current_activity.metadata)

    def test_visual_edit_clears_storyboard_links_but_keeps_voiceover_manifest(self):
        state = self.ready_state()
        state.analyze_storyboard(analyzer=LocalAnalyzer())
        self.assertIn("storyboard_path", state.current_activity.metadata)
        voiceovers = [segment.to_dict() for segment in state.voiceover_segments]
        voiceover_manifest = state.current_activity.metadata["voiceover_segments_path"]
        shots = sample_shots()
        shots[1]["notes"] = "edited visual"
        state.set_storyboard_shots(shots)
        self.assertNotIn("storyboard_path", state.current_activity.metadata)
        self.assertNotIn("json_path", state.storyboard_analysis.metadata)
        self.assertEqual([segment.to_dict() for segment in state.voiceover_segments], voiceovers)
        self.assertEqual(state.current_activity.metadata["voiceover_segments_path"], voiceover_manifest)

    def test_visual_fields_preserve_audio_but_invalidate_assembly(self):
        changes = {
            "metadata": {"material_asset_id": "replacement"},
            "visual_tags": ["new visual"],
            "material_requirements": ["new material"],
            "title": "new shot title",
            "transition": "fade",
            "notes": "new direction",
            "script_text": "new caption with unchanged explicit narration",
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                state = self.ready_state()
                original = [segment.to_dict() for segment in state.voiceover_segments]
                shots = sample_shots()
                shots[1][field] = value
                state.set_storyboard_shots(shots)
                self.assertEqual([segment.to_dict() for segment in state.voiceover_segments], original)
                self.assertEqual(state.current_activity.status["voiceover"], "ready")
                self.assertIsNone(state.assembly_plan)

    def test_script_change_invalidates_audio_when_used_as_narration(self):
        state = self.ready_state()
        shots = sample_shots()
        shots[1]["tts_text"] = ""
        state.set_storyboard_shots(shots)
        self.assertEqual(len(state.voiceover_segments), 2)
        shots[1]["script_text"] = "new fallback narration"
        state.set_storyboard_shots(shots)
        self.assertEqual([segment.shot_id for segment in state.voiceover_segments], ["S01"])
        self.assertEqual(state.current_activity.status["voiceover"], "partial")
        self.assertNotIn("voiceover_segments_path", state.current_activity.metadata)

    def test_clearing_explicit_tts_invalidates_when_fallback_text_differs(self):
        state = self.ready_state()
        shots = sample_shots()
        shots[1]["script_text"] = "different caption"
        state.set_storyboard_shots(shots)
        self.assertEqual(len(state.voiceover_segments), 2)
        shots[1]["tts_text"] = ""
        state.set_storyboard_shots(shots)
        self.assertEqual([segment.shot_id for segment in state.voiceover_segments], ["S01"])

    def test_equivalent_explicit_and_fallback_narration_keep_audio(self):
        state = self.ready_state()
        original = [segment.to_dict() for segment in state.voiceover_segments]
        manifest = state.current_activity.metadata["voiceover_segments_path"]
        shots = sample_shots()
        shots[1]["tts_text"] = ""
        state.set_storyboard_shots(shots)
        self.assertEqual([segment.to_dict() for segment in state.voiceover_segments], original)
        state.set_storyboard_shots(sample_shots())
        self.assertEqual([segment.to_dict() for segment in state.voiceover_segments], original)
        self.assertEqual(state.current_activity.metadata["voiceover_segments_path"], manifest)

    def test_planned_duration_edit_preserves_actual_audio_duration(self):
        state = self.ready_state()
        original = [segment.to_dict() for segment in state.voiceover_segments]
        manifest = state.current_activity.metadata["voiceover_segments_path"]
        shots = sample_shots()
        shots[0]["duration_seconds"] = 30.0
        shots[1]["duration_seconds"] = 40.0
        state.set_storyboard_shots(shots)
        self.assertEqual([segment.to_dict() for segment in state.voiceover_segments], original)
        self.assertEqual([segment.duration_seconds for segment in state.voiceover_segments], [1.0, 2.0])
        self.assertEqual([segment.start_seconds for segment in state.voiceover_segments], [0.0, 1.0])
        self.assertEqual(state.current_activity.metadata["voiceover_segments_path"], manifest)
        self.assertIsNone(state.assembly_plan)

    def test_reorder_keeps_audio_and_updates_timing(self):
        state = self.ready_state()
        original = {segment.shot_id: segment.to_dict() for segment in state.voiceover_segments}
        state.set_storyboard_shots(list(reversed(sample_shots())))
        self.assertEqual([item.shot_id for item in state.voiceover_segments], ["S02", "S01"])
        self.assertEqual([item.start_seconds for item in state.voiceover_segments], [0, 2])
        for segment in state.voiceover_segments:
            expected = {**original[segment.shot_id], "start_seconds": segment.start_seconds}
            self.assertEqual(segment.to_dict(), expected)
        self.assertNotIn("voiceover_segments_path", state.current_activity.metadata)
        self.assertIsNone(state.assembly_plan)

    def test_upstream_changes_clear_render_result_but_preserve_exports(self):
        mutations = {
            "reference": lambda state: state.set_reference_text("changed reference"),
            "reference_videos": lambda state: state.set_reference_videos([self.root / "changed.mp4"]),
            "rewrite": lambda state: state.set_copywriting("rewrite", {"text": "changed rewrite"}),
            "storyboard": lambda state: state.set_storyboard_shots([
                {**sample_shots()[0], "metadata": {"material_asset_id": "replacement"}}, sample_shots()[1],
            ]),
            "materials": lambda state: state.set_material_assets([]),
            "material_paths": lambda state: state.set_material_paths([self.root / "queued.mp4"]),
            "voiceovers": lambda state: state.set_voiceover_segments([]),
        }
        for upstream, mutate in mutations.items():
            with self.subTest(upstream=upstream):
                state = self.ready_state()
                activity = state.current_activity
                exported_path = state.activity_dir / "exports/previous.mp4"
                exported_path.parent.mkdir(parents=True, exist_ok=True)
                exported_path.write_bytes(b"previous render")
                exports = [{"output_path": str(exported_path), "created_at": "2000-01-01"}]
                activity.metadata["render_result"] = {"output_path": str(exported_path)}
                activity.metadata["exports"] = copy.deepcopy(exports)
                mutate(state)
                self.assertNotIn("render_result", activity.metadata)
                self.assertEqual(activity.metadata["exports"], exports)
                self.assertEqual(exported_path.read_bytes(), b"previous render")
                self.assertIsNone(state.assembly_plan)
                state.save_project()
                loaded = WorkflowState.load_project(state.project_dir)
                self.assertNotIn("render_result", loaded.current_activity.metadata)
                self.assertEqual(loaded.current_activity.metadata["exports"], exports)

    def test_unchanged_shots_and_activity_switch_preserve_render_result(self):
        state = self.ready_state()
        activity = state.current_activity
        result = {"output_path": str(state.activity_dir / "final.mp4")}
        activity.metadata["render_result"] = copy.deepcopy(result)
        state.set_storyboard_shots(sample_shots())
        state.create_activity("other")
        state.set_current_activity(activity.activity_id)
        self.assertEqual(activity.metadata["render_result"], result)

    def test_render_invalidation_only_affects_target_activity(self):
        state = self.ready_state()
        original_activity = state.current_activity
        original_activity.metadata["render_result"] = {"output_path": "first.mp4"}
        other = state.create_activity("other")
        other.metadata["render_result"] = {"output_path": "second.mp4"}
        state.set_material_paths([self.root / "new.mp4"], activity_id=original_activity.activity_id)
        self.assertNotIn("render_result", original_activity.metadata)
        self.assertEqual(other.metadata["render_result"], {"output_path": "second.mp4"})

    def test_identical_shots_keep_assembly_and_duplicate_ids_reject(self):
        state = self.ready_state()
        state.set_storyboard_shots(sample_shots())
        self.assertIsNotNone(state.assembly_plan)
        before = state.to_dict()
        with self.assertRaises(ValueError):
            state.set_storyboard_shots([sample_shots()[0], sample_shots()[0]])
        self.assertEqual(state.to_dict(), before)

    def test_removed_shot_audio_is_removed_and_empty_list_clears_all(self):
        state = self.ready_state()
        state.set_storyboard_shots(sample_shots()[:1])
        self.assertEqual([item.shot_id for item in state.voiceover_segments], ["S01"])
        state.set_storyboard_shots([])
        self.assertEqual(state.voiceover_segments, [])
        self.assertEqual(state.current_activity.status["storyboard"], "pending")

    def test_material_and_voiceover_changes_clear_assembly(self):
        state = self.ready_state()
        state.set_material_assets([])
        self.assertIsNone(state.assembly_plan)
        self.assertEqual(len(state.voiceover_segments), 2)
        state.current_activity.assembly_plan = AssemblyPlan()
        state.set_voiceover_segments([])
        self.assertIsNone(state.assembly_plan)
        self.assertEqual(state.project.voiceover_segments, [])

    def test_uploaded_material_queue_change_clears_assembly(self):
        state = self.ready_state()
        state.set_material_paths([state.project_dir / "media/queued.mp4"])
        self.assertIsNone(state.assembly_plan)
        self.assertEqual(state.current_activity.metadata["material_paths"], [str(state.project_dir / "media/queued.mp4")])
        self.assertEqual(len(state.material_assets), 1)
        self.assertEqual(len(state.voiceover_segments), 2)

    def test_material_schema_dict_round_trip_is_idempotent(self):
        state = self.ready_state()
        assets = [item.to_dict() for item in state.material_assets]
        state.set_material_assets(assets)
        self.assertEqual([item.to_dict() for item in state.material_assets], assets)
        self.assertIsNotNone(state.assembly_plan)

    def test_legacy_shots_without_ids_keep_matching_generated_audio(self):
        state = self.ready_state()
        state.storyboard_analysis.shots[0].shot_id = ""
        state.set_storyboard_shots(sample_shots())
        self.assertEqual([item.shot_id for item in state.voiceover_segments], ["S01", "S02"])

    def test_current_activity_empty_values_never_use_legacy_results(self):
        state = self.ready_state()
        legacy = copy.deepcopy(state.project)
        state.create_activity("empty")
        state.project.reference_copy = legacy.reference_copy
        state.project.storyboard_analysis = legacy.storyboard_analysis
        state.project.material_assets = legacy.material_assets
        state.project.voiceover_segments = legacy.voiceover_segments
        state.project.assembly_plan = legacy.assembly_plan
        self.assertEqual(state.reference_text, "")
        self.assertIsNone(state.storyboard_analysis)
        self.assertEqual(state.material_assets, [])
        self.assertEqual(state.voiceover_segments, [])
        self.assertIsNone(state.assembly_plan)
        with self.assertRaises(ValueError):
            state.generate_voiceover_segment("S01", tts_client=LocalTTS())

    def test_targeted_setter_and_switch_do_not_edit_other_activity(self):
        state = self.ready_state()
        first = state.current_activity
        first.updated_at = "2000-01-01T00:00:00"
        other = state.create_activity("other")
        other.updated_at = "2001-01-01T00:00:00"
        original_other = copy.deepcopy(other.to_dict())
        state.set_current_activity(first.activity_id)
        self.assertEqual(first.updated_at, "2000-01-01T00:00:00")
        state.set_current_activity(other.activity_id)
        state.set_reference_text("updated first", activity_id=first.activity_id)
        self.assertEqual(state.project.current_activity_id, other.activity_id)
        self.assertEqual(other.to_dict(), original_other)
        self.assertIsNone(first.assembly_plan)
        self.assertEqual(state.reference_text, "")

    def test_noncurrent_merge_setters_leave_selection_and_current_activity_unchanged(self):
        state = self.ready_state()
        target_id = state.current_activity.activity_id
        current = state.create_activity("selected")
        before = copy.deepcopy(current.to_dict())
        state.set_storyboard_shots(sample_shots()[:1], activity_id=target_id)
        state.set_material_assets([], activity_id=target_id)
        state.set_voiceover_segments([], activity_id=target_id)
        self.assertEqual(state.project.current_activity_id, current.activity_id)
        self.assertEqual(current.to_dict(), before)
        self.assertIsNone(state.project.storyboard_analysis)
        with self.assertRaises(KeyError):
            state.set_storyboard_shots([], activity_id="missing")

    def test_artifacts_are_per_activity_and_metadata_lives_on_activity(self):
        state = self.new_state()
        directories = []
        for index in range(2):
            if index:
                state.create_activity("second")
            state.set_reference_text("reference")
            state.analyze_storyboard(analyzer=LocalAnalyzer())
            state.analyze_materials([state.project_dir / "media/input.mp4"], catalog=LocalCatalog())
            state.generate_voiceovers(tts_client=LocalTTS())
            state.build_assembly_plan(renderer=LocalRenderer())
            metadata = state.current_activity.metadata
            for key in ("storyboard_path", "material_catalog_path", "voiceover_segments_path", "assembly_plan_path"):
                self.assertTrue(Path(metadata[key]).is_file())
                self.assertTrue(Path(metadata[key]).is_relative_to(state.activity_dir))
            directories.append(state.activity_dir)
        self.assertNotEqual(*directories)
        self.assertEqual(len(list(state.project_dir.glob("activities/*/storyboard.json"))), 2)

    def test_every_tts_attempt_is_isolated_even_with_explicit_output_root(self):
        state = self.ready_state()
        previous = {item.audio_path: Path(item.audio_path).read_bytes() for item in state.voiceover_segments}
        root = self.root / "custom_audio"
        first = state.generate_voiceover_segment("S01", tts_client=LocalTTS(), output_dir=root)
        second = state.generate_voiceover_segment("S01", tts_client=LocalTTS(), output_dir=root)
        self.assertNotEqual(first.audio_path, second.audio_path)
        self.assertTrue(Path(first.audio_path).is_relative_to(root))
        before_failure = copy.deepcopy(state.to_dict())
        with self.assertRaises(RuntimeError):
            state.generate_voiceovers(tts_client=LocalTTS(fail=True))
        self.assertEqual(state.to_dict(), before_failure)
        for path, content in previous.items():
            self.assertEqual(Path(path).read_bytes(), content)

    def test_incomplete_tts_keeps_previous_results(self):
        state = self.ready_state()
        previous = copy.deepcopy(state.to_dict())
        with self.assertRaises(ValueError):
            state.generate_voiceovers(tts_client=LocalTTS(incomplete=True))
        self.assertEqual(state.to_dict(), previous)

    def test_voiceover_manifest_and_runtime_use_same_timing_with_missing_shots(self):
        state = self.ready_state()
        state.set_voiceover_segments([])
        segment = state.generate_voiceover_segment("S02", tts_client=LocalTTS())
        manifest = load_json(state.current_activity.metadata["voiceover_segments_path"])
        self.assertEqual(segment.start_seconds, 1.0)
        self.assertEqual(manifest["segments"][0]["start_seconds"], segment.start_seconds)

    def test_voiceover_without_manifest_updates_activity_directory(self):
        state = self.ready_state()
        previous_directory = state.current_activity.metadata["voiceover_dir"]
        state.generate_voiceovers(tts_client=LocalTTS(), save=False)
        self.assertNotEqual(state.current_activity.metadata["voiceover_dir"], previous_directory)
        self.assertNotIn("voiceover_segments_path", state.current_activity.metadata)

    def test_voiceover_manifest_write_failure_preserves_previous_results(self):
        state = self.ready_state()
        previous = copy.deepcopy(state.to_dict())
        with patch("production_schema.os.replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                state.generate_voiceover_segment("S01", tts_client=LocalTTS())
        self.assertEqual(state.to_dict(), previous)

    def test_external_open_persists_and_moved_same_id_replaces_index(self):
        state = self.ready_state()
        manifest = Path(state.save_project()["project_path"])
        root = self.root / "store"
        store = ProjectStore(root)
        store.open_project(manifest)
        self.assertEqual(ProjectStore(root).get_state("portable").project_dir, manifest.parent)
        self.assertIn("portable", ProjectStore(root).list_projects())
        moved = self.root / "moved.mimoproject"
        shutil.move(str(manifest.parent), str(moved))
        store.load_project(moved)
        restarted = ProjectStore(root)
        self.assertEqual(restarted.find_project_file("portable"), moved / "project.json")
        self.assertEqual(restarted.get_state("portable").project_dir, moved)
        self.assertTrue(Path(restarted.get_state("portable").voiceover_segments[0].audio_path).exists())
        self.assertEqual(store.index_path.parent, root)

    def test_root_owned_index_is_relative_and_can_move_with_store(self):
        store = ProjectStore(self.root / "store")
        state = store.create_project("owned", project_id="owned")
        index = load_json(store.index_path)
        self.assertFalse(Path(index["projects"]["owned"]).is_absolute())
        moved = self.root / "moved_store"
        shutil.move(str(store.root_dir), str(moved))
        reopened = ProjectStore(moved).get_state("owned")
        self.assertEqual(reopened.project_dir, moved / state.project_dir.name)

    def test_created_outside_root_and_custom_manifest_are_indexed(self):
        root = self.root / "store"
        store = ProjectStore(root)
        external = store.create_project("external", output_dir=self.root / "elsewhere", project_id="external")
        self.assertEqual(ProjectStore(root).get_state("external").project_dir, external.project_dir)
        custom = external.project_dir / "custom.json"
        (external.project_dir / "project.json").rename(custom)
        store.open_project(custom)
        store.save_project("external")
        self.assertEqual(ProjectStore(root).find_project_file("external"), custom)
        self.assertFalse((external.project_dir / "project.json").exists())

    def test_all_activities_and_uploaded_material_paths_move_together(self):
        state = self.ready_state()
        first_id = state.current_activity.activity_id
        state.create_activity("second")
        state.current_activity.metadata["material_paths"] = [str(state.project_dir / "media/new.mp4")]
        state.save_project()
        moved = self.root / "moved.mimoproject"
        shutil.move(str(state.project_dir), str(moved))
        loaded = WorkflowState.load_project(moved)
        self.assertEqual(loaded.current_activity.metadata["material_paths"], [str(moved / "media/new.mp4")])
        first = loaded.get_activity(first_id)
        self.assertTrue(Path(first.voiceover_segments[0].audio_path).is_file())
        self.assertTrue(Path(first.metadata["voiceover_dir"]).is_relative_to(moved))

    def test_root_scan_uses_actual_manifest_parent_not_old_mac_root(self):
        root = self.root / "store"
        manifest = root / "old.mimoproject/project.json"
        save_json({"project_id": "old", "project_dir": "/Users/old/demo", "schema_version": 2}, manifest)
        projects = ProjectStore(root).list_projects()
        self.assertEqual(projects["old"]["project_dir"], str(manifest.parent))

    def test_external_delete_unlinks_without_moving_unrelated_parent_files(self):
        state = self.new_state("shared_folder")
        state.save_project()
        unrelated = state.project_dir / "unrelated.txt"
        unrelated.write_text("keep", encoding="utf-8")
        store = ProjectStore(self.root / "store")
        store.open_project(state.project_dir)
        result = store.delete_project("portable")
        self.assertTrue(result["unlinked"])
        self.assertTrue(unrelated.is_file())
        self.assertNotIn("portable", ProjectStore(store.root_dir).list_projects())

    def test_owned_delete_removes_persistent_index(self):
        store = ProjectStore(self.root / "store")
        store.create_project("owned", project_id="owned")
        result = store.delete_project("owned")
        self.assertTrue(Path(result["trash_path"]).is_dir())
        self.assertNotIn("owned", load_json(store.index_path)["projects"])
        self.assertNotIn("owned", ProjectStore(store.root_dir).list_projects())


if __name__ == "__main__":
    unittest.main(verbosity=2)
