import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import React from "react";
import { act, create } from "react-test-renderer";
import { build } from "esbuild";

const bundle = await build({
  stdin: { contents: 'export { AssemblyPage } from "./src/pages/AssemblyPage.jsx";', resolveDir: fileURLToPath(new URL("../", import.meta.url)) },
  bundle: true, platform: "node", format: "cjs", jsx: "automatic", write: false, external: ["react", "react/jsx-runtime"],
});
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const { AssemblyPage } = module.exports;

const activity = (narration_mode, voiceover_segments = []) => ({
  activity_id: "a1", name: "图片预演", metadata: {}, narration_mode, voiceover_segments,
  storyboard_analysis: { shots: [{ shot_id: "s1", duration_seconds: 3 }] }, material_assets: [{ asset_id: "m1" }],
});

function setup(t, overrides = {}) {
  const calls = [];
  const props = { project: { project_id: "p1" }, activity: activity("voiceover"), backendUrl: "http://localhost",
    onRenderVideo: (payload) => calls.push(["render", payload]), onSetNarrationMode: async (mode) => calls.push(["mode", mode]),
    ...overrides };
  let tree;
  act(() => { tree = create(React.createElement(AssemblyPage, props)); });
  t.after(() => act(() => tree.unmount()));
  const button = (name) => tree.root.findAllByType("button").find((item) => item.children.join("") === name);
  const select = () => tree.root.findAll((item) => item.type === "select" && item.props["aria-label"] === "旁白")[0];
  const text = () => JSON.stringify(tree.toJSON());
  return { calls, button, select, text };
}

test("a voiceover activity still needs voiceover before export", (t) => {
  const ui = setup(t);
  assert.equal(ui.select().props.value, "voiceover");
  assert.equal(ui.button("导出视频").props.disabled, true);
});

test("a no-narration activity exports with music only and no voiceover", (t) => {
  const ui = setup(t, { activity: activity("none") });
  assert.equal(ui.select().props.value, "none");
  assert.equal(ui.button("导出视频").props.disabled, false);
  assert.match(ui.text(), /无旁白/);
  act(() => ui.button("导出视频").props.onClick());
  assert.equal(ui.calls[0][0], "render");
});

test("activities saved before the mode existed count as voiceover", (t) => {
  const legacy = activity(undefined, [{ shot_id: "s1", audio_path: "v.wav" }]);
  const ui = setup(t, { activity: legacy });
  assert.equal(ui.select().props.value, "voiceover");
  assert.equal(ui.button("导出视频").props.disabled, false);
});

test("choosing a narration mode asks the backend to save it", async (t) => {
  const ui = setup(t);
  await act(async () => { await ui.select().props.onChange({ target: { value: "none" } }); });
  assert.deepEqual(ui.calls, [["mode", "none"]]);
});

test("the mode cannot change while a job runs", (t) => {
  const ui = setup(t, { busy: true });
  assert.equal(ui.select().props.disabled, true);
});

test("the controller saves the mode on the current activity", async () => {
  const { WorkflowController } = await import("../src/api/workflowController.js");
  const calls = [];
  const controller = new WorkflowController({ baseUrl: "http://localhost", patch: async (path, body) => { calls.push([path, body]); return {}; } });
  controller.scope = { projectId: "p 1", activityId: "a1" };
  await controller.setNarrationMode("none");
  assert.deepEqual(calls, [["/projects/p%201/activities/a1", { narration_mode: "none" }]]);
});
