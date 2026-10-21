import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import React from "react";
import { act, create } from "react-test-renderer";
import { build } from "esbuild";
import { applySegmentCandidate } from "../src/editor/shotEditing.js";

const bundle = await build({
  stdin: { contents: 'export { SegmentRecommendations } from "./src/components/SegmentRecommendations.jsx"; export { MaterialLibraryPage } from "./src/pages/MaterialLibraryPage.jsx";', resolveDir: fileURLToPath(new URL("../", import.meta.url)) },
  bundle: true, platform: "node", format: "cjs", jsx: "automatic", write: false, external: ["react", "react/jsx-runtime"],
});
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const { SegmentRecommendations, MaterialLibraryPage } = module.exports;
globalThis.ResizeObserver = class { observe() {} disconnect() {} };

const shot = { shot_id: "s1", tts_text: "Voice", duration_seconds: 4, metadata: { material_asset_id: "m1", edit: { subtitle: { text: "Caption" }, crop_x: .7 } } };
const asset = { asset_id: "m1", file_path: "source.mp4", metadata: { segment_index: { index_id: "i1", segments: [{ segment_id: "clip_01", source_in: 0, source_out: 8, summary: "Curtain", visual_tags: [] }] } } };
const candidate = { asset_id: "m1", segment_id: "clip_01", index_id: "i1", source_in: 2, source_out: 6, available_seconds: 8, summary: "Curtain", warnings: ["原素材带字幕"], missing_terms: ["手机"], matched_terms: ["窗帘"], requires_confirmation: true };
const result = { candidates: [candidate], needs_material: true, message: "没有充分匹配的片段" };
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };

function setup(t, changes = {}) {
  const props = { shot, assets: [asset], targetDuration: 4, backendUrl: "http://localhost", projectId: "p1", activityId: "a1", aspectRatio: "9:16", onRecommend: async () => result, onApply() {}, ...changes };
  let tree;
  act(() => { tree = create(React.createElement(SegmentRecommendations, props)); });
  t.after(() => act(() => tree.unmount()));
  const button = (name) => tree.root.findAllByType("button").find((item) => item.props["aria-label"] === name);
  const click = async (name) => { assert.equal(Boolean(button(name).props.disabled), false); await act(async () => { await button(name).props.onClick(); }); };
  return { tree, button, click, update: (next) => act(() => tree.update(React.createElement(SegmentRecommendations, { ...props, ...next }))) };
}

test("candidate requests carry the draft and actual duration without saving", async (t) => {
  let sent;
  const ui = setup(t, { onRecommend: async (...args) => { sent = args; return result; } });
  assert.equal(sent, undefined);
  await ui.click("查找候选片段");
  assert.deepEqual(sent, [shot, { targetDuration: 4, query: "", expectedScope: { projectId: "p1", activityId: "a1" } }]);
  assert.match(JSON.stringify(ui.tree.toJSON()), /手机/);
});

test("risk candidates need explicit confirmation and apply only after preview selection", async (t) => {
  let adopted;
  const ui = setup(t, { onApply: (...args) => { adopted = args; } });
  await ui.click("查找候选片段");
  await ui.click("预览候选 clip_01 m1");
  assert.equal(ui.button("采用候选片段").props.disabled, true);
  const check = ui.tree.root.findAllByType("input").find((item) => item.props.type === "checkbox");
  act(() => check.props.onChange({ target: { checked: true } }));
  await ui.click("采用候选片段");
  assert.deepEqual(adopted, [candidate, true]);
});

test("late recommendation responses are discarded after shot change", async (t) => {
  const pending = deferred();
  const ui = setup(t, { onRecommend: () => pending.promise });
  let request;
  act(() => { request = ui.button("查找候选片段").props.onClick(); });
  ui.update({ shot: { ...shot, shot_id: "s2" } });
  await act(async () => { pending.resolve(result); await request; });
  assert.equal(ui.button("预览候选 clip_01 m1"), undefined);
});

test("updated asset indexes clear previous candidates and confirmation", async (t) => {
  const ui = setup(t);
  await ui.click("查找候选片段");
  await ui.click("预览候选 clip_01 m1");
  ui.update({ assets: [{ ...asset, metadata: { segment_index: { ...asset.metadata.segment_index, index_id: "i2" } } }] });
  assert.equal(ui.button("采用候选片段"), undefined);
});

test("missing indexes disable search and query errors stay explicit", async (t) => {
  const empty = setup(t, { assets: [] });
  assert.equal(empty.button("查找候选片段").props.disabled, true);
  const failed = setup(t, { onRecommend: async () => { throw new Error("offline"); } });
  await failed.click("查找候选片段");
  assert.match(JSON.stringify(failed.tree.toJSON()), /offline/);
  assert.equal(failed.button("采用候选片段"), undefined);
});

test("adoption preserves narration, caption, crop and duration and never auto-enables freezing", () => {
  assert.throws(() => applySegmentCandidate([shot], "s1", candidate), /确认/);
  const adopted = applySegmentCandidate([shot], "s1", candidate, true)[0];
  assert.equal(adopted.tts_text, "Voice");
  assert.equal(adopted.duration_seconds, 4);
  assert.equal(adopted.metadata.edit.subtitle.text, "Caption");
  assert.equal(adopted.metadata.edit.crop_x, .7);
  assert.equal(adopted.metadata.edit.source_in, 2);
  assert.equal(adopted.metadata.edit.source_out, 6);
  assert.equal(adopted.metadata.edit.shortage, undefined);
  assert.throws(() => applySegmentCandidate([shot], "s1", { ...candidate, source_out: NaN }, true), /无效/);
});

test("material indexing uses only selected analyzed asset IDs", async (t) => {
  let sent, tree;
  act(() => { tree = create(React.createElement(MaterialLibraryPage, {
    project: { project_id: "p1" }, activity: { material_assets: [asset], metadata: { material_paths: ["untagged.mp4"] } },
    onIndexSegments: (payload) => { sent = payload; },
  })); });
  t.after(() => act(() => tree.unmount()));
  const button = () => tree.root.findAllByType("button").find((item) => item.props["aria-label"] === "分析选中素材的片段");
  assert.equal(button().props.disabled, true);
  const checkboxes = tree.root.findAllByType("input").filter((item) => item.props.type === "checkbox");
  act(() => checkboxes[1].props.onChange({ target: { checked: true } }));
  assert.equal(button().props.disabled, true);
  act(() => checkboxes[0].props.onChange({ target: { checked: true } }));
  await act(async () => button().props.onClick());
  assert.deepEqual(sent, { asset_ids: ["m1"], window_seconds: 8 });
});

test("segment jobs have their own progress label and block resubmission", (t) => {
  let tree;
  act(() => { tree = create(React.createElement(MaterialLibraryPage, {
    project: { project_id: "p1" }, activity: { material_assets: [asset] },
    job: { job_type: "materials.index", status: "running", progress: 35 }, onIndexSegments: () => {},
  })); });
  t.after(() => act(() => tree.unmount()));
  assert.equal(tree.root.findByType("progress").props["aria-label"], "素材片段分析进度");
  assert.equal(tree.root.findByType("progress").props.value, 35);
  act(() => tree.root.findAllByType("input").find((item) => item.props.type === "checkbox").props.onChange({ target: { checked: true } }));
  assert.equal(tree.root.findAllByType("button").find((item) => item.props["aria-label"] === "分析选中素材的片段").props.disabled, true);
});
