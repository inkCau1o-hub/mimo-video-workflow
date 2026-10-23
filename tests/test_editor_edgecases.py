"""Offline contract regressions; failures intentionally expose unmet requirements.

Run: .venv-windows/Scripts/python.exe -B tests/test_editor_edgecases.py -v
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# macOS keeps temp dirs behind the /var -> /private/var symlink; stored project paths are resolved.
tempfile.tempdir = str(Path(tempfile.gettempdir()).resolve())


def offline_environment(directory):
    environment = {
        key: os.environ[key]
        for key in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE")
        if key in os.environ
    }
    environment.update({
        "VIDEO_SCRIPT_CONFIG_PATH": str(Path(directory) / "unused-config.json"),
        "VIDEO_SCRIPT_MEMORY_PATH": str(Path(directory) / "unused-memory.json"),
        "MIMO_API_KEY": "offline-test-placeholder",
        "MIMO_BASE_URL": "https://offline.invalid/v1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return environment


# Isolate configuration BEFORE application imports, which otherwise load user keys.
with tempfile.TemporaryDirectory(prefix="editor_import_") as _config_directory:
    with patch.dict(os.environ, offline_environment(_config_directory), clear=True):
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
            import config
            from backend.jobs import JobManager, JobRecord
            from backend.project_store import ProjectStore
            from backend.workflow_api import WorkflowBackend
            from production_schema import AssemblyPlan, MaterialAsset, VoiceoverSegment


class EditorEdgeFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="editor_edges_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enter_context(patch.dict(os.environ, offline_environment(self.root), clear=True))
        self.enter_context(patch.object(config, "API_KEY", "offline-test-placeholder"))
        self.enter_context(patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")))
        self.enter_context(patch("requests.sessions.Session.request", side_effect=AssertionError("API forbidden")))
        self.backend = WorkflowBackend(ProjectStore(self.root / "store"), jobs=JobManager())
        self.pid = self.backend.create_project({"name": "edge fixtures"})["status"]["project_id"]
        self.state = self.backend.store.get_state(self.pid)
        self.aid = self.state.current_activity.activity_id
        self.manifest = self.state.project_dir / "project.json"
        self.video = self.state.project_dir / "media" / "source clip.mp4"
        self.audio = self.state.project_dir / "voiceovers" / "original.wav"
        self.export = self.state.project_dir / "exports" / "old.mp4"
        for path, content in ((self.video, b"video fixture"), (self.audio, b"audio fixture"),
                              (self.export, b"export fixture")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.state.set_material_assets([
            MaterialAsset(asset_id="material1", file_path=str(self.video), duration_seconds=20),
        ])
        self.shots = [self.shot("S01", "original narration"), self.shot("S02", "second narration")]
        self.state.set_storyboard_shots(self.shots)
        self.state.set_voiceover_segments([
            self.voice("S01", "original narration", 3), self.voice("S02", "second narration", 2),
        ])
        self.state.current_activity.metadata.update({
            "exports": [{"output_path": str(self.export)}],
            "render_result": {"output_path": str(self.export)},
        })
        self.backend.store.save_project(self.pid)

    def enter_context(self, context):
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value

    def shot(self, identifier, text, duration=4):
        return {"shot_id": identifier, "title": identifier, "script_text": text, "tts_text": text,
                "duration_seconds": duration, "metadata": {"material_asset_id": "material1"}}

    def voice(self, identifier, text, duration, path=None):
        return VoiceoverSegment(shot_id=identifier, segment_id=f"voice_{identifier}", text=text,
                                audio_path=str(path or self.audio), duration_seconds=duration)

    def save(self, shots=None, **payload):
        return self.backend.set_storyboard_shots(self.pid, {
            "activity_id": self.aid, "shots": self.shots if shots is None else shots, **payload,
        })

    def history(self, action, **payload):
        return self.backend.edit_history(self.pid, {"activity_id": self.aid, "action": action, **payload})

    def revision(self):
        history = self.backend.get_editor_history(self.pid)["history"]
        return history["entries"][history["cursor"]]["id"] if history["cursor"] >= 0 else ""

    def current_shots(self):
        return [item.to_dict() for item in self.state.storyboard_analysis.shots]

    def voices(self):
        return {item.shot_id: item for item in self.state.voiceover_segments}

    def before(self):
        self.state.ensure_current_activity()
        return (deepcopy(self.state.to_dict()), self.manifest.read_bytes(),
                self.backend.store.index_path.read_bytes())

    def assert_unchanged(self, before):
        self.assertEqual(self.state.to_dict(), before[0], "Rejected edit changed in-memory state")
        self.assertEqual(self.manifest.read_bytes(), before[1], "Rejected edit changed project.json")
        self.assertEqual(self.backend.store.index_path.read_bytes(), before[2])
        self.assertEqual(self.audio.read_bytes(), b"audio fixture")
        self.assertEqual(self.export.read_bytes(), b"export fixture")

    def two_versions(self):
        first = self.save()["history"]["entries"][0]["id"]
        shots = self.current_shots()
        shots[0]["title"] = "second version"
        self.save(shots)
        return first

    def start_job(self, worker=None):
        started, release, done = threading.Event(), threading.Event(), threading.Event()
        original_run = self.backend.jobs._run

        def run(job_id, callback):
            try:
                original_run(job_id, callback)
            finally:
                done.set()

        def work(snapshot, context):
            started.set()
            if not release.wait(8):
                raise AssertionError("Controlled job timed out")
            return worker(snapshot, context) if worker else {}

        def finish():
            release.set()
            self.assertTrue(done.wait(10), "Controlled job did not finish")

        with patch.object(self.backend.jobs, "_run", side_effect=run):
            record = self.backend._submit_activity_job("test.offline", self.pid, work)["job"]
        self.addCleanup(finish)
        self.assertTrue(started.wait(3), "Controlled job did not start")
        return record["job_id"], release, done


class EditorRestoreEdges(EditorEdgeFixture):
    def test_restore_and_undo_validate_target_audio_not_current_audio(self):
        self.shots[0]["metadata"]["edit"] = {"duration_mode": "manual"}
        self.shots[0]["duration_seconds"] = 3
        self.save()
        old_revision = self.revision()
        old_voice = self.voices()["S01"].to_dict()
        changed = self.current_shots()
        changed[0]["duration_seconds"] = 9
        self.state.set_storyboard_shots(changed)
        longer = self.voice("S01", "original narration", 8, self.audio)
        self.state.set_voiceover_segments([longer, self.voices()["S02"]])
        self.backend.store.save_project(self.pid)
        for action in ("restore", "undo"):
            with self.subTest(action=action):
                if action == "undo":
                    self.history("undo")
                self.history(action, revision_id=old_revision)
                self.assertEqual(self.voices()["S01"].to_dict(), old_voice)
                self.assertEqual(self.current_shots()[0]["duration_seconds"], 3)

    def test_redo_uses_saved_long_audio_and_manual_duration(self):
        self.shots[0]["metadata"]["edit"] = {"duration_mode": "manual"}
        self.shots[0]["duration_seconds"] = 3
        self.save()
        changed = self.current_shots()
        changed[0]["duration_seconds"] = 9
        self.save(changed)
        self.state.set_voiceover_segments([
            self.voice("S01", "original narration", 8), self.voices()["S02"],
        ])
        self.history("undo")
        self.history("redo")
        self.assertEqual(self.voices()["S01"].duration_seconds, 8)
        self.assertEqual(self.current_shots()[0]["duration_seconds"], 9)

    def test_invalid_historical_manual_duration_is_rejected_atomically(self):
        first = self.two_versions()
        entries = self.state.current_activity.metadata["edit_history"]["entries"]
        target = entries[0]["snapshot"]["shots"][0]
        target["metadata"]["edit"] = {"duration_mode": "manual"}
        target["duration_seconds"] = 1
        self.backend.store.save_project(self.pid)
        before = self.before()
        for action in ("restore", "undo"):
            with self.subTest(action=action):
                with self.assertRaises(ValueError):
                    self.history(action, revision_id=first)
                self.assert_unchanged(before)

    def test_restore_from_undo_position_keeps_the_redo_entries(self):
        first = self.two_versions()
        shots = self.current_shots()
        shots[0]["title"] = "third version"
        old = self.save(shots)["history"]
        self.history("undo")
        restored = self.history("restore", revision_id=first)["history"]
        self.assertEqual(restored["entries"][:-1], old["entries"])
        self.assertNotIn(restored["entries"][-1]["id"], [item["id"] for item in old["entries"]])
        self.assertEqual(self.current_shots()[0]["title"], "S01")

    def test_restore_rebuilds_plan_and_keeps_old_exports(self):
        first = self.two_versions()
        self.state.current_activity.assembly_plan = AssemblyPlan(plan_id="obsolete-plan")
        self.state.current_activity.metadata["render_result"] = {"output_path": str(self.export)}
        self.backend.store.save_project(self.pid)
        exports = deepcopy(self.state.current_activity.metadata["exports"])
        self.history("restore", revision_id=first)
        self.assertEqual(self.state.current_activity.metadata["exports"], exports)
        self.assertEqual(self.export.read_bytes(), b"export fixture")
        self.assertNotIn("render_result", self.state.current_activity.metadata)
        plan = self.state.assembly_plan
        self.assertIsNotNone(plan, "Contract requires rebuilding the restored assembly plan, not only clearing it")
        self.assertNotEqual(plan.plan_id, "obsolete-plan")
        self.assertEqual([item.shot_id for item in plan.items], ["S01", "S02"])
        self.assertEqual([item.voiceover_segment_id for item in plan.items],
                         [self.voices()[identifier].segment_id for identifier in ("S01", "S02")])

    def test_invalid_history_operations_leave_no_partial_initial_entry(self):
        before = self.before()
        for action in ("undo", "redo", "restore", "unknown"):
            with self.subTest(action=action):
                with self.assertRaises(ValueError):
                    self.history(action, revision_id="missing", base_revision="")
                self.assert_unchanged(before)


class EditorDuplicateEdges(EditorEdgeFixture):
    def duplicate(self):
        clone = deepcopy(self.shots[0])
        clone["shot_id"] = "S03"
        clone["metadata"]["duplicate_of"] = "S01"
        return clone

    def test_new_duplicate_gets_distinct_segment_id_without_copying_media(self):
        files = set(self.state.project_dir.rglob("*.wav"))
        self.save([*self.shots, self.duplicate()])
        voices = self.voices()
        self.assertEqual(voices["S03"].audio_path, voices["S01"].audio_path)
        self.assertNotEqual(voices["S03"].segment_id, voices["S01"].segment_id)
        self.assertEqual(set(self.state.project_dir.rglob("*.wav")), files)

    def test_copy_cannot_reuse_source_removed_in_same_request(self):
        self.save([self.shots[1], self.duplicate()])
        self.assertNotIn("S03", self.voices())
        self.assertTrue(self.audio.exists())

    def test_copy_cannot_reuse_source_changed_in_same_request(self):
        clone = self.duplicate()
        changed = deepcopy(self.shots)
        changed[0]["tts_text"] = "source changed in this save"
        self.save([*changed, clone])
        self.assertNotIn("S01", self.voices())
        self.assertNotIn("S03", self.voices(), "Source and copy no longer have identical narration")

    def test_copy_cannot_reuse_orphan_voice_without_source_shot(self):
        orphan = self.voice("missing", "original narration", 8)
        self.state.current_activity.voiceover_segments.append(orphan)
        clone = self.duplicate()
        clone["metadata"]["duplicate_of"] = "missing"
        self.save([*self.shots, clone])
        self.assertNotIn("S03", self.voices())

    def test_new_manual_copy_cannot_be_shorter_than_reused_audio(self):
        clone = self.duplicate()
        clone["duration_seconds"] = 2
        clone["metadata"]["edit"] = {"duration_mode": "manual"}
        before = self.before()
        with self.assertRaises(ValueError):
            self.save([*self.shots, clone])
        self.assert_unchanged(before)

    def test_reediting_copy_back_to_source_text_does_not_resurrect_old_audio(self):
        self.save([*self.shots, self.duplicate()])
        changed = self.current_shots()
        changed[2]["tts_text"] = "new copy narration"
        self.save(changed)
        self.assertNotIn("S03", self.voices())
        changed[2]["tts_text"] = "original narration"
        self.save(changed)
        self.assertNotIn("S03", self.voices(), "Changing an existing shot's narration must clear its audio")

    def test_reedited_manual_copy_never_accepts_audio_longer_than_shot(self):
        self.save([*self.shots, self.duplicate()])
        changed = self.current_shots()
        changed[2]["tts_text"] = "short copy narration"
        self.save(changed)
        self.state.set_voiceover_segments([
            *self.state.voiceover_segments, self.voice("S03", "short copy narration", 1),
        ])
        changed[2]["tts_text"] = "original narration"
        changed[2]["duration_seconds"] = 2
        changed[2]["metadata"]["edit"] = {"duration_mode": "manual"}
        self.save(changed)
        self.assertNotIn("S03", self.voices(), "Old source audio must not bypass manual-duration validation")

    def test_subtitle_only_edit_of_copy_retains_its_new_voice(self):
        self.save([*self.shots, self.duplicate()])
        fresh = self.state.project_dir / "voiceovers" / "fresh.wav"
        fresh.write_bytes(b"new voice fixture")
        self.state.set_voiceover_segments([
            self.voices()["S01"], self.voices()["S02"], self.voice("S03", "original narration", 2, fresh),
        ])
        shots = self.current_shots()
        shots[2]["metadata"]["edit"] = {"subtitle": {"text": "Independent subtitle"}}
        self.save(shots)
        self.assertEqual(self.voices()["S03"].audio_path, str(fresh))
        self.assertEqual(self.voices()["S03"].duration_seconds, 2)


class EditorConcurrencyEdges(EditorEdgeFixture):
    def test_queued_same_activity_job_blocks_every_editor_mutation(self):
        first = self.two_versions()
        pending = JobRecord("queued-test", "test.offline", project_id=self.pid, activity_id=self.aid)
        self.backend.jobs._jobs[pending.job_id] = pending
        before = self.before()
        for action in ("save", "undo", "redo", "restore", "checkpoint"):
            with self.subTest(action=action):
                with self.assertRaisesRegex(ValueError, "\u4efb\u52a1"):
                    if action == "save":
                        self.save()
                    else:
                        self.history(action, revision_id=first)
                self.assert_unchanged(before)

    def test_running_and_cancelling_jobs_block_edits_until_finished(self):
        self.save()
        job_id, release, done = self.start_job()
        before = self.before()
        for cancelling in (False, True):
            with self.subTest(cancelling=cancelling):
                if cancelling:
                    self.backend.jobs.cancel(job_id)
                with self.assertRaisesRegex(ValueError, "\u4efb\u52a1"):
                    self.save()
                with self.assertRaisesRegex(ValueError, "\u4efb\u52a1"):
                    self.history("checkpoint")
                self.assert_unchanged(before)
        release.set()
        self.assertTrue(done.wait(5))
        self.assertEqual(self.backend.jobs.get(job_id).status, "cancelled")
        self.history("checkpoint", base_revision=self.revision())

    def test_other_activity_job_does_not_block_or_overwrite_current_edit(self):
        self.save()

        def worker(snapshot, context):
            shots = [item.to_dict() for item in snapshot.storyboard_analysis.shots]
            shots[0]["title"] = "background activity A"
            snapshot.set_storyboard_shots(shots)
            return {}

        job_id, release, done = self.start_job(worker)
        old_activity_id = self.aid
        self.aid = self.backend.create_activity(self.pid, {"name": "B"})["activity"]["activity_id"]
        other_shot = self.shot("B01", "current activity B")
        other_shot["metadata"] = {}
        self.save([other_shot], base_revision="")
        edited = deepcopy(self.state.current_activity.to_dict())
        release.set()
        self.assertTrue(done.wait(5))
        self.assertEqual(self.backend.jobs.get(job_id).status, "succeeded")
        self.assertEqual(self.state.current_activity.to_dict(), edited)
        self.assertEqual(self.state.get_activity(old_activity_id).storyboard_analysis.shots[0].title,
                         "background activity A")

    def test_other_project_job_and_terminal_jobs_do_not_block(self):
        jobs = [JobRecord("other-project", "test", project_id="different", activity_id=self.aid)]
        jobs.extend(JobRecord(status, "test", project_id=self.pid, activity_id=self.aid, status=status)
                    for status in ("failed", "cancelled", "succeeded"))
        self.backend.jobs._jobs.update({item.job_id: item for item in jobs})
        self.save(base_revision="")
        self.history("checkpoint", base_revision=self.revision())

    def test_two_editors_with_same_revision_only_one_commits(self):
        self.save()
        revision = self.revision()
        original_count = len(self.backend.get_editor_history(self.pid)["history"]["entries"])
        gate = threading.Barrier(3)

        def edit(title):
            shots = deepcopy(self.shots)
            shots[0]["title"] = title
            gate.wait(timeout=5)
            try:
                self.save(shots, base_revision=revision)
                return "saved", title
            except ValueError as exc:
                return "rejected", str(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(edit, title) for title in ("client A", "client B")]
            gate.wait(timeout=5)
            results = [future.result(timeout=5) for future in futures]
        self.assertEqual(sorted(item[0] for item in results), ["rejected", "saved"])
        winner = next(text for status, text in results if status == "saved")
        self.assertEqual(self.current_shots()[0]["title"], winner)
        self.assertIn("\u7248\u672c\u5df2\u66f4\u65b0", next(text for status, text in results if status == "rejected"))
        history = self.backend.get_editor_history(self.pid)["history"]
        self.assertEqual(len(history["entries"]), original_count + 1)
        reopened = ProjectStore(self.backend.store.root_dir).get_state(self.pid)
        self.assertEqual(reopened.storyboard_analysis.shots[0].title, winner)

    def test_empty_and_stale_revisions_reject_without_mutation(self):
        self.save(base_revision="")
        stale = self.revision()
        self.history("checkpoint", base_revision=stale)
        before = self.before()
        for revision in ("", stale, "missing"):
            for action in ("save", "undo", "redo", "restore", "checkpoint"):
                with self.subTest(revision=revision, action=action):
                    with self.assertRaisesRegex(ValueError, "\u7248\u672c\u5df2\u66f4\u65b0"):
                        if action == "save":
                            self.save(base_revision=revision)
                        else:
                            self.history(action, base_revision=revision, revision_id=stale)
                    self.assert_unchanged(before)

    def test_revision_tracks_undo_redo_and_restore_cursor(self):
        first = self.two_versions()
        latest = self.revision()
        self.history("undo", base_revision=latest)
        self.assertEqual(self.revision(), first)
        before = self.before()
        with self.assertRaises(ValueError):
            self.save(base_revision=latest)
        self.assert_unchanged(before)
        self.history("redo", base_revision=first)
        self.assertEqual(self.revision(), latest)
        self.history("restore", revision_id=first, base_revision=latest)
        self.assertNotIn(self.revision(), (first, latest))


class EditorSaveFailureEdges(EditorEdgeFixture):
    def test_all_history_actions_roll_back_on_atomic_replace_failure(self):
        first = self.two_versions()
        for action in ("undo", "restore", "checkpoint", "redo"):
            with self.subTest(action=action):
                if action == "redo":
                    self.history("undo")
                before = self.before()
                revision = self.revision()
                with patch("production_schema.os.replace", side_effect=OSError("simulated disk full")):
                    with self.assertRaises(OSError):
                        self.history(action, revision_id=first, base_revision=revision)
                self.assert_unchanged(before)
                self.assertEqual(self.revision(), revision)
                self.assertFalse(list(self.manifest.parent.glob(".project.json.*.tmp")))

    def test_failed_save_preserves_redo_branch_and_same_revision_can_retry(self):
        self.two_versions()
        self.history("undo")
        revision = self.revision()
        shots = self.current_shots()
        shots[0]["tts_text"] = "pending narration"
        before = self.before()
        with patch("production_schema.os.fsync", side_effect=OSError("simulated flush failure")):
            with self.assertRaises(OSError):
                self.save(shots, base_revision=revision)
        self.assert_unchanged(before)
        self.assertTrue(self.backend.get_editor_history(self.pid)["history"]["can_redo"])
        self.assertFalse(list(self.manifest.parent.glob(".project.json.*.tmp")))
        self.save(shots, base_revision=revision)
        self.assertFalse(self.backend.get_editor_history(self.pid)["history"]["can_redo"])
        self.assertNotIn("S01", self.voices())
        reopened = ProjectStore(self.backend.store.root_dir).get_state(self.pid)
        self.assertEqual(reopened.storyboard_analysis.shots[0].tts_text, "pending narration")

    def test_invalid_empty_shot_list_cannot_remove_last_shot_or_history(self):
        self.save()
        before = self.before()
        with self.assertRaises(ValueError):
            self.save([])
        self.assert_unchanged(before)


class EditorHistoricalPathEdges(EditorEdgeFixture):
    def test_historical_only_media_survives_move_and_second_move(self):
        self.shots[0]["metadata"]["source_path"] = str(self.video)
        self.save()
        old_revision = self.revision()
        changed = deepcopy(self.shots)
        changed[0]["tts_text"] = "no current audio reference"
        changed[0]["metadata"].pop("source_path")
        changed[1]["tts_text"] = "also no current audio reference"
        self.save(changed)
        self.assertFalse(self.state.voiceover_segments)
        disk = json.loads(self.manifest.read_text(encoding="utf-8"))
        entries = disk["activities"][0]["metadata"]["edit_history"]["entries"]
        saved = next(entry["snapshot"] for entry in entries if entry["id"] == old_revision)
        self.assertEqual(saved["voiceover_segments"][0]["audio_path"], "voiceovers/original.wav")
        self.assertEqual(saved["shots"][0]["metadata"]["source_path"], "media/source clip.mp4")
        directory = self.state.project_dir
        for index in (1, 2):
            moved = self.root / f"moved {index} \u7d20\u6750" / directory.name
            moved.parent.mkdir()
            shutil.move(str(directory), str(moved))
            fresh = WorkflowBackend(ProjectStore(self.root / f"index-{index}"), jobs=JobManager())
            fresh.open_project({"path": str(moved)})
            fresh.edit_history(self.pid, {"action": "restore", "revision_id": old_revision, "activity_id": self.aid})
            restored = fresh.store.get_state(self.pid)
            self.assertEqual(Path(restored.voiceover_segments[0].audio_path), moved / "voiceovers" / "original.wav")
            self.assertEqual(Path(restored.storyboard_analysis.shots[0].metadata["source_path"]),
                             moved / "media" / "source clip.mp4")
            self.assertEqual(Path(restored.material_assets[0].file_path), moved / "media" / "source clip.mp4")
            self.assertEqual(Path(restored.voiceover_segments[0].audio_path).read_bytes(), b"audio fixture")
            self.assertEqual((moved / "exports" / "old.mp4").read_bytes(), b"export fixture")
            directory = moved

    def test_legacy_absolute_paths_inside_history_are_migrated_and_backed_up(self):
        self.save()
        original = json.loads(self.manifest.read_text(encoding="utf-8"))
        roots = ("/Users/old/Movies/project", "C:\\old machine\\project",
                 "file:///C:/old%20machine/project")
        for index, old_root in enumerate(roots):
            with self.subTest(old_root=old_root):
                directory = self.root / f"legacy-{index}"
                shutil.copytree(self.state.project_dir, directory)
                data = deepcopy(original)
                data["schema_version"] = 2
                data["project_dir"] = old_root
                snapshot = data["activities"][0]["metadata"]["edit_history"]["entries"][0]["snapshot"]
                snapshot["voiceover_segments"][0]["audio_path"] = old_root + "/voiceovers/original.wav"
                snapshot["shots"][0]["metadata"]["source_path"] = old_root + "/media/source%20clip.mp4" if old_root.startswith("file:") else old_root + "/media/source clip.mp4"
                manifest = directory / "project.json"
                manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                legacy_bytes = manifest.read_bytes()
                fresh = WorkflowBackend(ProjectStore(self.root / f"legacy-index-{index}"), jobs=JobManager())
                fresh.open_project({"path": str(directory)})
                self.assertEqual(manifest.with_suffix(".json.bak").read_bytes(), legacy_bytes)
                fresh.edit_history(self.pid, {"action": "restore", "revision_id": snapshot_id(data), "activity_id": self.aid})
                restored = fresh.store.get_state(self.pid)
                self.assertEqual(Path(restored.voiceover_segments[0].audio_path), directory / "voiceovers" / "original.wav")
                self.assertEqual(Path(restored.storyboard_analysis.shots[0].metadata["source_path"]),
                                 directory / "media" / "source clip.mp4")
                migrated = json.loads(manifest.read_text(encoding="utf-8"))
                histories = migrated["activities"][0]["metadata"]["edit_history"]
                self.assertNotIn(old_root, json.dumps(histories, ensure_ascii=False))
                self.assertEqual(manifest.with_suffix(".json.bak").read_bytes(), legacy_bytes)


def snapshot_id(data):
    return data["activities"][0]["metadata"]["edit_history"]["entries"][0]["id"]


if __name__ == "__main__":
    unittest.main()
