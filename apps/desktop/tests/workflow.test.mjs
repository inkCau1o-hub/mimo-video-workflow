import test from "node:test";
import assert from "node:assert/strict";
import { WorkflowController } from "../src/api/workflowController.js";
import { monitorJob } from "../src/api/jobMonitor.js";
import { ApiClient, ApiError } from "../src/api/client.js";
import { pickList, pickReferenceCopy, pickStoryboardAnalysis, pickAssemblyPlan, projectMediaUrl, renderResult, updateShot, validShots } from "../src/workflowData.js";

const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };

test("cover generation rejects stale scope and submits with current activity", async () => {
  const { controller, calls } = setup();
  const input = { source_video_path: "export.mp4", settings: { time_seconds: .2 } };
  await controller.generateCover(input, { expectedScope: { projectId: "other", activityId: "a1" } });
  assert.equal(calls.length, 0);
  await controller.generateCover(input, { expectedScope: { projectId: "p1", activityId: "a1" } });
  assert.deepEqual(calls.find((call) => call[0] === "POST"), ["POST", "/projects/p1/covers/render", { ...input, activity_id: "a1" }]);
});

test("audio settings save has scope and optimistic revision guards", async () => {
  const { controller, calls } = setup();
  const settings = { normalize_voice: true, music_path: "music.wav" };
  await controller.saveAudioSettings(settings, { baseRevision: "r1", expectedScope: { projectId: "p1", activityId: "a1" } });
  assert.deepEqual(calls.find((item) => item[0] === "POST"), ["POST", "/projects/p1/audio/settings", {
    audio_settings: settings, base_revision: "r1", activity_id: "a1",
  }]);
  const before = calls.length;
  await controller.saveAudioSettings(settings, { expectedScope: { projectId: "other", activityId: "a1" } });
  assert.equal(calls.length, before);
});

test("music upload is scoped multipart and late project responses are discarded", async () => {
  const { controller, api } = setup();
  const pending = deferred();
  let sent;
  api.upload = async (...args) => { sent = args; return pending.promise; };
  const file = new File(["audio"], "音乐.wav");
  const request = controller.uploadMusic(file, { expectedScope: { projectId: "p1", activityId: "a1" } });
  assert.equal(sent[0], "/projects/p1/audio/upload-music");
  assert.equal(sent[1].get("activity_id"), "a1");
  assert.equal(sent[1].get("music").name, "音乐.wav");
  const next = controller.beginScope("p2", "a1");
  controller.commitSnapshot(snapshot("p2"), next);
  pending.resolve({ ...snapshot("p1"), uploaded_music: { file_path: "old.wav" } });
  assert.equal(await request, null);
  assert.equal(controller.getSnapshot().project.project_id, "p2");
});
const storage = () => { const data = new Map(); return { getItem: (key) => data.get(key), setItem: (key, value) => data.set(key, value) }; };
const snapshot = (id = "p1", active = "a1", overrides = {}) => ({
  status: { project_id: id, current_activity_id: active },
  project: { project_id: id, name: id, current_activity_id: active, activities: [
    { activity_id: "a1", name: "First", reference_copy: { source_text: "saved" }, material_assets: [], ...overrides },
    { activity_id: "a2", name: "Second", material_assets: [] },
  ] },
});
function setup(options = {}) {
  const calls = [];
  const api = {
    baseUrl: "http://test", calls,
    get: async (path) => {
      calls.push(["GET", path]);
      if (path === "/health") return { ok: true };
      if (path === "/projects") return { projects: [{ project_id: "p1" }, { project_id: "p2" }] };
      if (path.endsWith("/jobs")) return { jobs: [] };
      if (path.endsWith("/snapshot")) return snapshot(path.split("/")[2]);
      throw new Error(`unexpected GET ${path}`);
    },
    post: async (path, payload) => { calls.push(["POST", path, payload]); return snapshot(); },
    upload: async (path, payload) => { calls.push(["UPLOAD", path, payload]); return { ...snapshot(), uploaded_videos: [{ file_path: "real.mp4" }] }; },
  };
  const controller = new WorkflowController(api, { storage: storage(), ...options });
  const scope = controller.beginScope("p1", "a1");
  controller.commitSnapshot(snapshot(), scope);
  controller.set({ loading: false });
  return { controller, api, calls, scope };
}

test("polling continues past 120 responses without a false timeout", async () => {
  let count = 0;
  const seen = [];
  const job = await monitorJob({
    api: { get: async () => ({ job: { job_id: "long", status: ++count === 145 ? "succeeded" : "running" } }) },
    jobId: "long", signal: new AbortController().signal, onJob: (value) => seen.push(value),
    onConnectionError: assert.fail, wait: async () => {},
  });
  assert.equal(count, 145);
  assert.equal(job.status, "succeeded");
  assert.equal(seen.length, 145);
});

test("connection errors back off and recover without failing the job", async () => {
  let count = 0;
  const errors = [], waits = [];
  const result = await monitorJob({
    api: { get: async () => { if (++count < 3) throw new Error("offline"); return { job: { job_id: "j", status: "succeeded" } }; } },
    jobId: "j", signal: new AbortController().signal, onJob: () => {},
    onConnectionError: (error) => errors.push(error), wait: async (ms) => waits.push(ms),
  });
  assert.equal(result.status, "succeeded");
  assert.equal(errors.length, 2);
  assert.deepEqual(waits, [2000, 4000]);
});

test("abort discards an in-flight polling response", async () => {
  const response = deferred(), abort = new AbortController();
  const pending = monitorJob({ api: { get: () => response.promise }, jobId: "j", signal: abort.signal, onJob: assert.fail, onConnectionError: assert.fail });
  abort.abort();
  response.resolve({ job: { job_id: "j", status: "succeeded" } });
  assert.equal(await pending, null);
});

test("a missing job is reported as unknown, not a fabricated failed task", async () => {
  const errors = [];
  const result = await monitorJob({ api: { get: async () => { throw new ApiError("not found", 404); } }, jobId: "j", signal: new AbortController().signal, onJob: assert.fail, onConnectionError: (e) => errors.push(e) });
  assert.equal(result, null);
  assert.equal(errors[0].status, 404);
});

test("old project snapshot cannot replace a newly opened project", async () => {
  const { controller, api } = setup();
  const old = deferred();
  const get = api.get;
  api.get = (path) => path === "/projects/p1/snapshot" ? old.promise : get(path);
  const first = controller.openProject("p1");
  await controller.openProject("p2");
  old.resolve(snapshot("p1"));
  await first;
  assert.equal(controller.state.project.project_id, "p2");
  controller.stop();
});

test("old save response and validation cannot overwrite another activity", async () => {
  const { controller, api } = setup();
  const save = deferred(), validation = deferred();
  api.post = (path) => path.endsWith("/reference/text") ? save.promise
    : path.endsWith("/validate-videos") ? validation.promise : Promise.resolve(snapshot("p1", "a2"));
  const pending = controller.saveReferenceText("old edit");
  const validating = controller.validateReferenceVideos(["one.mp4"]);
  await controller.selectActivity("a2");
  save.resolve(snapshot("p1", "a1", { reference_copy: { source_text: "old edit" } }));
  validation.resolve({ ok: true, videos: [{ input_path: "one.mp4" }] });
  assert.equal(await pending, null);
  assert.equal(await validating, null);
  assert.equal(controller.state.status.current_activity_id, "a2");
  assert.equal(controller.state.validation, null);
  controller.stop();
});

test("background snapshots preserve the selected activity", async () => {
  const { controller, api } = setup();
  await controller.selectActivity("a2");
  api.get = async (path) => path.endsWith("/jobs") ? { jobs: [] } : snapshot("p1", "a1");
  await controller.loadSnapshot();
  assert.equal(controller.state.project.current_activity_id, "a2");
  assert.equal(controller.state.status.has_reference, false);
  controller.stop();
});

test("refresh restores the stored project, activity and only its jobs", async () => {
  const store = storage();
  store.setItem("mimo.workflow:http://test:selection", JSON.stringify({ projectId: "p2", activityId: "a2" }));
  const { api } = setup();
  const get = api.get;
  api.get = async (path) => path.endsWith("/jobs") ? { jobs: [
    { job_id: "a", project_id: "p2", activity_id: "a1", status: "failed" },
    { job_id: "b", project_id: "p2", activity_id: "a2", status: "failed", error: "encoder error" },
  ] } : get(path);
  api.post = async (path, body) => {
    assert.equal(path, "/projects/p2/activities/a2/select");
    assert.equal(body.activity_id, "a2");
    return snapshot("p2", "a2");
  };
  const restored = new WorkflowController(api, { storage: store });
  await restored.refreshProject();
  assert.equal(restored.state.project.project_id, "p2");
  assert.equal(restored.state.status.current_activity_id, "a2");
  assert.deepEqual(restored.state.jobs.map((job) => job.job_id), ["b"]);
  assert.equal(restored.state.jobs[0].error, "encoder error");
  restored.stop();
});

test("running tasks discovered on project open resume monitoring", async () => {
  const { controller, api } = setup();
  const response = deferred();
  const get = api.get;
  api.get = async (path) => path.endsWith("/jobs") ? { jobs: [{ job_id: "resume", project_id: "p1", activity_id: "a1", status: "running" }] }
    : path === "/jobs/resume" ? response.promise : get(path);
  await controller.openProject("p1");
  assert.equal(controller.monitors.size, 1);
  response.resolve({ job: { job_id: "resume", status: "failed", error: "real failure" } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(controller.state.jobs[0].status, "failed");
  assert.match(controller.state.error, /real failure/);
  controller.stop();
});

test("old backend can recover locally recorded jobs without the listing API", async () => {
  const { controller, api, scope } = setup();
  controller.upsertJob({ job_id: "cached", project_id: "p1", activity_id: "a1", status: "running" }, scope);
  api.get = async (path) => {
    if (path.endsWith("/jobs")) throw new ApiError("route not found", 404);
    if (path === "/jobs/cached") return { job: { job_id: "cached", status: "succeeded" } };
    return snapshot();
  };
  await controller.recoverJobs(scope);
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(controller.state.notice, /仅恢复本机/);
  assert.equal(controller.state.jobs[0].status, "succeeded");
  controller.stop();
});

test("an old monitored job cannot inject output or errors after switching project", async () => {
  const { controller, api, scope } = setup();
  const response = deferred();
  const get = api.get;
  api.get = (path, options) => path === "/jobs/old" ? response.promise : get(path, options);
  controller.acceptJob({ job_id: "old", job_type: "assembly.render", status: "running" }, scope);
  await controller.openProject("p2");
  response.resolve({ job: { job_id: "old", status: "failed", error: "old project error" } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(controller.state.project.project_id, "p2");
  assert.equal(controller.state.error, "");
  assert.deepEqual(controller.state.jobs, []);
  controller.stop();
});

test("a task finishing during recovery refreshes the snapshot and keeps newest-first ties", async () => {
  const { controller, api } = setup();
  let snapshots = 0;
  api.get = async (path) => {
    if (path.endsWith("/jobs")) return { jobs: [
      { job_id: "new", project_id: "p1", activity_id: "a1", status: "succeeded", created_at: "2026-01-01T00:00:00" },
      { job_id: "old", project_id: "p1", activity_id: "a1", status: "succeeded", created_at: "2026-01-01T00:00:00" },
    ] };
    return snapshot("p1", "a1", ++snapshots > 1 ? { metadata: { render_result: { output_path: "completed.mp4" } } } : {});
  };
  await controller.openProject("p1");
  assert.equal(controller.state.project.activities[0].metadata.render_result.output_path, "completed.mp4");
  assert.deepEqual(controller.state.jobs.map((job) => job.job_id), ["new", "old"]);
  controller.stop();
});

test("all edit POST payloads preserve the captured activity_id and full shots", async () => {
  const { controller, calls } = setup();
  const shots = [{ shot_id: "s1", script_text: "edit", tts_text: "voice", duration_seconds: 4, visual_tags: ["kept"], metadata: { custom: true, material_asset_id: "m1" } }];
  await controller.saveReferenceText("ref");
  await controller.saveCopyText("copy");
  await controller.saveShots(shots);
  assert.equal(calls.filter(([method]) => method === "POST").length, 3);
  for (const [, , body] of calls.filter(([method]) => method === "POST")) assert.equal(body.activity_id, "a1");
  assert.deepEqual(calls.at(-1)[2].shots, shots);
  controller.stop();
});

test("old reference/text status-only response reloads the actual snapshot", async () => {
  const { controller, api, calls } = setup();
  api.post = async () => ({ status: { project_id: "p1" } });
  const result = await controller.saveReferenceText("new reference");
  assert.ok(result.project);
  assert.ok(calls.some(([, path]) => path === "/projects/p1/snapshot"));
  controller.stop();
});

test("unsupported new endpoints report failure and never claim a save", async () => {
  const { controller, api } = setup();
  api.post = async () => { throw new ApiError("route not found", 404); };
  const result = await controller.saveCopyText("text");
  assert.equal(result, null);
  assert.match(controller.state.error, /后端已更新/);
  assert.equal(controller.state.messages.length, 0);
  controller.stop();
});

test("material upload is multipart, carries activity_id and does not tag", async () => {
  const { controller, calls } = setup();
  const file = new Blob(["video"], { type: "video/mp4" });
  file.name = "clip.mp4";
  const result = await controller.uploadMaterialVideos([file]);
  const [method, path, body] = calls.at(-1);
  assert.equal(method, "UPLOAD");
  assert.equal(path, "/projects/p1/materials/upload-videos");
  assert.equal(body.get("activity_id"), "a1");
  assert.equal(body.get("videos").name, "clip.mp4");
  assert.equal(result.uploaded_videos[0].file_path, "real.mp4");
  assert.equal(calls.length, 1);
  controller.stop();
});

test("render submits the real API and inline failed jobs remain failed", async () => {
  const { controller, api } = setup();
  let request;
  api.post = async (path, body) => {
    request = [path, body];
    return { job: { job_id: "render", job_type: "assembly.render", status: "failed", error: "ffmpeg failed", logs: ["has log"] } };
  };
  await controller.submitProjectJob("/assembly/render", { aspect_ratio: "9:16", subtitles: true, width: 720, height: 1280 }, "导出视频");
  assert.equal(request[0], "/projects/p1/assembly/render");
  assert.equal(request[1].activity_id, "a1");
  assert.equal(controller.state.jobs[0].status, "failed");
  assert.match(controller.state.error, /ffmpeg failed/);
  controller.stop();
});

test("opening an external project sends its path and selects the returned project", async () => {
  const { controller, api } = setup();
  let request;
  api.post = async (path, input) => { request = [path, input]; return snapshot("p2", "a2"); };
  await controller.openExternalProject(" C:\\Videos\\project.json ");
  assert.deepEqual(request, ["/projects/open", { path: "C:\\Videos\\project.json" }]);
  assert.equal(controller.state.project.project_id, "p2");
  assert.equal(controller.state.status.current_activity_id, "a2");
  controller.stop();
});

test("help commands reach the backend without a selected project", async () => {
  for (const command of ["帮助", "help"]) {
    const { api, calls } = setup();
    const controller = new WorkflowController(api, { storage: storage() });
    const response = { summary: "可用命令: 创建项目 名称", requires_confirmation: false, actions: [] };
    api.post = async (path, payload) => { calls.push(["POST", path, payload]); return response; };
    const result = await controller.runAgentCommand(command);
    assert.equal(result, response);
    assert.deepEqual(calls.map(([method, path]) => [method, path]), [["POST", "/agent/command"]]);
    const sent = JSON.parse(JSON.stringify(calls[0][2]));
    assert.equal(sent.command, command);
    assert.ok(!sent.project_id);
    assert.ok(!sent.activity_id);
    assert.equal(controller.state.project, null);
    assert.equal(controller.state.error, "");
    assert.equal(controller.state.pending, "");
    assert.ok(controller.state.messages.some((message) => message.role === "system" && message.text === response.summary));
    controller.stop();
  }
});

test("create-project command works from an empty controller and opens the real returned project", async () => {
  const { api, calls } = setup();
  const store = storage();
  const controller = new WorkflowController(api, { storage: store });
  const created = snapshot("p2");
  created.project.name = "控制台项目";
  const projects = [{ project_id: "p2", name: created.project.name }];
  api.post = async (path, payload) => {
    calls.push(["POST", path, payload]);
    assert.equal(path, "/agent/command");
    assert.equal(payload.command, "创建项目 控制台项目");
    assert.ok(!payload.project_id);
    assert.ok(!payload.activity_id);
    return { ...created, projects, summary: "已创建项目: 控制台项目" };
  };
  api.get = async (path) => {
    calls.push(["GET", path]);
    if (path === "/projects/p2/snapshot") return created;
    if (path === "/projects/p2/jobs") return { jobs: [] };
    assert.fail(`unexpected GET ${path}`);
  };
  await controller.runAgentCommand("创建项目 控制台项目");
  assert.equal(controller.state.project.project_id, "p2");
  assert.equal(controller.state.project.name, "控制台项目");
  assert.equal(controller.state.status.current_activity_id, "a1");
  assert.deepEqual(controller.state.projects, projects);
  assert.equal(controller.state.error, "");
  assert.equal(controller.state.loading, false);
  assert.equal(controller.state.pending, "");
  assert.deepEqual(JSON.parse(store.getItem("mimo.workflow:http://test:selection")), { projectId: "p2", activityId: "a1" });
  assert.equal(calls.filter(([method]) => method === "POST").length, 1);
  controller.stop();
});

test("projectless console access does not bypass normal workflow project requirements", async () => {
  const { api, calls } = setup();
  const controller = new WorkflowController(api);
  assert.equal(await controller.saveReferenceText("text"), null);
  assert.equal(await controller.submitProjectJob("/assembly/render", {}, "导出视频"), null);
  assert.match(controller.state.error, /请先创建或打开项目/);
  assert.deepEqual(calls, []);
  controller.stop();
});

test("agent commands carry the captured activity and discard responses after switching", async () => {
  const { controller, api } = setup();
  const response = deferred();
  let sent;
  api.post = async (path, payload) => {
    if (path === "/agent/command") { sent = payload; return response.promise; }
    assert.equal(path, "/projects/p1/activities/a2/select");
    return snapshot("p1", "a2");
  };
  const command = controller.runAgentCommand("状态");
  assert.deepEqual(sent, { project_id: "p1", activity_id: "a1", command: "状态" });
  await controller.selectActivity("a2");
  response.resolve({ ...snapshot("p1", "a1"), summary: "旧活动响应" });
  assert.equal(await command, null);
  assert.equal(controller.state.status.current_activity_id, "a2");
  assert.equal(controller.state.error, "");
  assert.ok(!controller.state.messages.some((message) => message.text === "旧活动响应"));
  controller.stop();
});

test("empty activities never inherit another activity's material or content", () => {
  const activity = { material_assets: [], reference_copy: null, storyboard_analysis: null, assembly_plan: null };
  const project = { material_assets: [{ asset_id: "foreign" }], reference_copy: { source_text: "foreign" }, storyboard_analysis: { shots: [1] }, assembly_plan: {} };
  assert.deepEqual(pickList(activity, project, "material_assets"), []);
  assert.equal(pickReferenceCopy(activity, project), null);
  assert.equal(pickStoryboardAnalysis(activity, project), null);
  assert.equal(pickAssemblyPlan(activity, project), null);
  assert.deepEqual(pickList(null, project, "material_assets"), project.material_assets);
});

test("shot editing retains untouched data and requires positive finite durations", () => {
  const shots = [{ shot_id: "s1", notes: "keep", visual_tags: ["tag"], metadata: { trace: "keep" }, duration_seconds: 2 }, { shot_id: "s2", duration_seconds: 1 }];
  const edited = updateShot(shots, "s1", { script_text: "new", metadata: { material_asset_id: "manual" } });
  assert.equal(edited[0].metadata.trace, "keep");
  assert.equal(edited[0].metadata.material_asset_id, "manual");
  assert.equal(edited[0].notes, "keep");
  assert.equal(edited[1], shots[1]);
  assert.equal(shots[0].script_text, undefined);
  assert.ok(validShots(edited));
  for (const duration of [0, -1, "", "wrong", Infinity]) assert.equal(validShots([{ duration_seconds: duration }]), false);
});

test("media uses encoded backend URLs and plans alone are not rendered output", () => {
  const url = new URL(projectMediaUrl("http://local/", "project one", "C:\\素材\\one #1.mp4", "v1"));
  assert.equal(url.pathname, "/projects/project%20one/media");
  assert.equal(url.searchParams.get("path"), "C:\\素材\\one #1.mp4");
  assert.equal(renderResult({ assembly_plan: { output_path: "planned.mp4" } }, null), null);
  assert.equal(renderResult({ metadata: { render_result: { output_path: "real.mp4" } } }, null).output_path, "real.mp4");
  assert.equal(renderResult({}, null, { status: "failed", result: { output_path: "no.mp4" } }), null);
  assert.equal(renderResult(null, null, { status: "succeeded", result: { output_path: "yes.mp4" } }).output_path, "yes.mp4");
  assert.equal(renderResult({ metadata: { exports: [{ output_path: "outdated.mp4" }] } }, null, { status: "succeeded", result: { output_path: "outdated.mp4" } }), null);
});

test("API errors retain HTTP status for compatibility decisions", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ error: "missing" }), { status: 404 });
  try {
    await assert.rejects(new ApiClient("http://test").get("/new"), (error) => error.status === 404 && error.message === "missing");
  } finally { globalThis.fetch = original; }
});
