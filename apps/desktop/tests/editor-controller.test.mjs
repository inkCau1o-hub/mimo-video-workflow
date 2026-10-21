import test from "node:test";
import assert from "node:assert/strict";
import { WorkflowController } from "../src/api/workflowController.js";
import { ApiError } from "../src/api/client.js";

const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const shots = [{ shot_id: "s1", tts_text: "Voice", duration_seconds: 5 }];
const summary = (cursor = 0) => ({ cursor, can_undo: cursor > 0, can_redo: cursor < 1, entries: [{ id: "r1", label: "First" }, { id: "r2", label: "Second" }] });
const snapshot = (projectId = "p1", activityId = "a1", history = summary(), extra = {}) => ({
  history, status: { current_activity_id: activityId },
  project: { project_id: projectId, current_activity_id: activityId, activities: [{ activity_id: "a1", storyboard_analysis: { shots }, ...extra }, { activity_id: "a2" }] },
});

function setup() {
  const calls = [];
  const api = {
    baseUrl: "http://test", get: async (path) => {
      calls.push(["GET", path]);
      if (path.endsWith("/jobs")) return { jobs: [] };
      if (path.includes("/editor/history")) return { history: summary() };
      return snapshot(path.split("/")[2]);
    },
    post: async (path, body) => { calls.push(["POST", path, body]); return snapshot("p1", "a1", summary(1)); },
  };
  const controller = new WorkflowController(api);
  const scope = controller.beginScope("p1", "a1");
  controller.commitSnapshot(snapshot(), scope);
  controller.set({ loading: false });
  return { controller, api, calls };
}

test("segment queries carry scope without mutating project and discard late responses", async () => {
  const { controller, api, calls } = setup();
  const before = controller.state.project;
  api.post = async (path, body) => { calls.push([path, body]); return { candidates: [] }; };
  assert.deepEqual(await controller.recommendSegments(shots[0], { targetDuration: 5, query: "phone" }), { candidates: [] });
  assert.equal(calls[0][0], "/projects/p1/materials/recommendations");
  assert.equal(calls[0][1].activity_id, "a1");
  assert.equal(controller.state.project, before);
  const pending = deferred();
  api.post = () => pending.promise;
  const request = controller.recommendSegments(shots[0], { targetDuration: 5 });
  controller.beginScope("p2", "a2");
  pending.resolve({ candidates: [{ asset_id: "old" }] });
  assert.equal(await request, null);
  assert.equal(await controller.recommendSegments(shots[0], { expectedScope: { projectId: "p1", activityId: "a1" } }), null);
});

test("save carries the full shots, activity, label and captured base revision", async () => {
  const { controller, calls } = setup();
  const result = await controller.saveShots(shots, { label: "Subtitle", baseRevision: "r1", expectedScope: { projectId: "p1", activityId: "a1" } });
  assert.deepEqual(calls, [["POST", "/projects/p1/storyboard/shots", { shots, label: "Subtitle", base_revision: "r1", activity_id: "a1" }]]);
  assert.equal(result.history.cursor, 1);
  assert.equal(controller.state.editorHistory.cursor, 1);
});

test("undo, redo, restore and checkpoint post contract and commit returned voice references", async () => {
  for (const action of ["undo", "redo", "restore", "checkpoint"]) {
    const { controller, api, calls } = setup();
    controller.set({ editorHistory: summary(action === "undo" ? 1 : 0) });
    api.post = async (path, body) => {
      calls.push(["POST", path, body]);
      return snapshot("p1", "a1", summary(1), {
        voiceover_segments: [{ shot_id: "s1", audio_path: "restored.wav" }],
        metadata: { exports: [{ output_path: "old.mp4" }] },
      });
    };
    const result = await controller.editorHistoryAction(action, { revisionId: action === "restore" ? "r2" : undefined, label: "Version", baseRevision: "captured" });
    assert.ok(result);
    assert.equal(calls[0][1], "/projects/p1/editor/history");
    assert.equal(calls[0][2].activity_id, "a1");
    assert.equal(calls[0][2].base_revision, "captured");
    assert.equal(calls[0][2].action, action);
    assert.equal(calls[0][2].revision_id, action === "restore" ? "r2" : undefined);
    const activity = controller.state.project.activities[0];
    assert.equal(activity.voiceover_segments[0].audio_path, "restored.wav");
    assert.equal(activity.metadata.render_result, undefined);
    assert.equal(activity.metadata.exports[0].output_path, "old.mp4");
  }
});

test("snapshot's activity history avoids an extra GET and excludes stored snapshots", () => {
  const { controller, calls } = setup();
  controller.commitSnapshot(snapshot("p1", "a1", undefined, { metadata: { edit_history: { cursor: 1, entries: [
    { id: "r0", label: "Initial", snapshot: { shots: [] } }, { id: "r1", label: "Saved", snapshot: { shots } },
  ] } } }), controller.scope);
  assert.equal(controller.state.editorHistory.entries[1].id, "r1");
  assert.equal(controller.state.editorHistory.can_undo, true);
  assert.equal(controller.state.editorHistory.entries[1].snapshot, undefined);
  assert.deepEqual(calls, []);
});

test("history GET discards out-of-order reads and responses predating a save", async () => {
  const { controller, api } = setup();
  const old = deferred(), fresh = deferred();
  let count = 0;
  api.get = () => ++count === 1 ? old.promise : fresh.promise;
  const first = controller.loadEditorHistory();
  const second = controller.loadEditorHistory();
  fresh.resolve({ history: summary(1) });
  await second;
  old.resolve({ history: summary(0) });
  assert.equal(await first, null);
  assert.equal(controller.state.editorHistory.cursor, 1);
  const stale = deferred(); api.get = () => stale.promise;
  const reading = controller.loadEditorHistory();
  await controller.saveShots(shots);
  stale.resolve({ history: summary(0) });
  assert.equal(await reading, null);
  assert.equal(controller.state.editorHistory.cursor, 1);
});

test("history reads are discarded when a newer snapshot has committed", async () => {
  const { controller, api } = setup();
  const stale = deferred(); api.get = () => stale.promise;
  const reading = controller.loadEditorHistory();
  controller.commitSnapshot(snapshot("p1", "a1", summary(1)), controller.scope);
  stale.resolve({ history: summary(0) });
  assert.equal(await reading, null);
  assert.equal(controller.state.editorHistory.cursor, 1);
});

test("cross-project and cross-activity delayed save/history responses never commit", async () => {
  for (const action of ["save", "restore", "read"]) {
    for (const navigation of ["project", "activity"]) {
      const { controller, api } = setup();
      const pending = deferred();
      const get = api.get;
      api.get = (path) => path.includes("/editor/history") ? pending.promise : get(path);
      api.post = (path) => path.endsWith("/select") ? Promise.resolve(snapshot("p1", "a2")) : pending.promise;
      const work = action === "save" ? controller.saveShots(shots)
        : action === "restore" ? controller.editorHistoryAction("restore", { revisionId: "r2" }) : controller.loadEditorHistory();
      if (navigation === "project") await controller.openProject("p2");
      else await controller.selectActivity("a2");
      pending.resolve(snapshot("p1", "a1", summary(1)));
      assert.equal(await work, null);
      assert.equal(controller.state.project.project_id, navigation === "project" ? "p2" : "p1");
      assert.equal(controller.state.status.current_activity_id, navigation === "activity" ? "a2" : "a1");
      assert.equal(controller.state.editorHistory.cursor, 0);
      assert.equal(controller.state.error, "");
    }
  }
});

test("a callback captured by an old editor cannot start a write in a new scope", async () => {
  const { controller, calls } = setup();
  const expectedScope = { projectId: "other", activityId: "a1" };
  assert.equal(await controller.saveShots(shots, { expectedScope }), null);
  assert.equal(await controller.editorHistoryAction("restore", { revisionId: "r2", expectedScope }), null);
  assert.deepEqual(calls, []);
});

test("409 conflicts and persistence failures leave project and history unchanged", async () => {
  for (const status of [409, 500]) {
    const { controller, api } = setup();
    const project = controller.state.project, history = controller.state.editorHistory;
    api.post = async () => { throw new ApiError("storage conflict", status); };
    assert.equal(await controller.saveShots(shots), null);
    assert.equal(controller.state.project, project);
    assert.equal(controller.state.editorHistory, history);
    assert.equal(controller.state.pending, "");
    assert.equal(controller.state.messages.length, 0);
    if (status === 409) assert.match(controller.state.error, /版本冲突.*刷新/);
  }
});

test("dirty drafts, unavailable revisions and concurrent mutations cannot restore", async () => {
  const { controller, calls } = setup();
  assert.equal(await controller.editorHistoryAction("restore", { revisionId: "r2", dirty: true }), null);
  assert.match(controller.state.error, /草稿/);
  assert.equal(await controller.editorHistoryAction("restore", { revisionId: "absent" }), null);
  controller.set({ pending: "Other request" });
  assert.equal(await controller.editorHistoryAction("restore", { revisionId: "r2" }), null);
  assert.deepEqual(calls, []);
});

test("unsupported history API is explicit and never invents a current revision", async () => {
  const { controller, api } = setup();
  api.get = async () => { throw new ApiError("missing", 404); };
  assert.equal(await controller.loadEditorHistory(), null);
  assert.equal(controller.state.editorHistory, null);
  assert.equal(controller.state.historyLoading, false);
  assert.match(controller.state.historyError, /后端已更新/);
});
