import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import React from "react";
import { act, create } from "react-test-renderer";
import { build } from "esbuild";
import { editShotFraming, editShotSettings, serializeShots, shotEdit, shotErrors, shotTiming } from "../src/editor/shotEditing.js";

const bundle = await build({ stdin: { contents: 'export * from "./src/components/CoverEditor.jsx";', resolveDir: fileURLToPath(new URL("../", import.meta.url)) },
  bundle: true, platform: "node", format: "cjs", jsx: "automatic", write: false, external: ["react", "react/jsx-runtime"] });
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const { CoverEditor, coverErrors, DEFAULT_COVER } = module.exports;

test("per-ratio framing is independent and leaves legacy defaults and narration intact", () => {
  const original = [{ shot_id: "s", duration_seconds: 3, tts_text: "keep", metadata: { edit: { fit: "cover", crop_x: .7 }, other: 1 } }];
  let shots = editShotFraming(original, "s", "9:16", { crop_x: 0 });
  shots = editShotFraming(shots, "s", "16:9", { fit: "contain", crop_x: "1" });
  shots = editShotSettings(shots, "s", { transition: { kind: "fade_black" } });
  shots = editShotSettings(shots, "s", { transition: { duration_seconds: ".2" } });
  const saved = serializeShots(shots)[0];
  assert.equal(shotEdit(saved, "9:16").crop_x, 0);
  assert.equal(shotEdit(saved, "16:9").crop_x, 1);
  assert.equal(shotEdit(saved).crop_x, .7);
  assert.equal(saved.metadata.edit.transition.kind, "fade_black");
  assert.equal(saved.metadata.edit.transition.duration_seconds, .2);
  assert.equal(saved.tts_text, "keep");
  assert.equal(saved.metadata.other, 1);
  assert.equal(original[0].metadata.edit.framing, undefined);
  assert.deepEqual(shotErrors(saved, shotTiming(saved)), []);
  saved.metadata.edit.framing["9:16"].crop_x = "";
  assert.match(shotErrors(saved, shotTiming(saved)).join(" "), /构图/);
});

test("cover validation rejects end frame, blank numbers, excessive title and invalid style", () => {
  assert.deepEqual(coverErrors(DEFAULT_COVER, 1), []);
  for (const patch of [{ time_seconds: 1 }, { time_seconds: "" }, { time_seconds: Infinity }, { font_size: "" },
    { font_size: 100 }, { title: "a".repeat(81) }, { title: "1\n2\n3\n4" }, { color: "red" }]) assert.ok(coverErrors({ ...DEFAULT_COVER, ...patch }, 1).length);
});

function setup(t, overrides = {}) {
  const props = { result: { output_path: "export.mp4", duration_seconds: 2 }, activity: { activity_id: "a" }, project: { project_id: "p" }, backendUrl: "http://test", onGenerateCover: async () => null, ...overrides };
  let tree;
  act(() => { tree = create(React.createElement(CoverEditor, props)); });
  t.after(() => act(() => tree.unmount()));
  const field = (name) => tree.root.findAllByType("label").find((node) => node.children.includes(name)).findAll((node) => ["input", "textarea", "select"].includes(node.type))[0];
  const change = (name, value) => act(() => field(name).props.onChange({ target: { value } }));
  const button = () => tree.root.findByProps({ "aria-label": "生成封面" });
  return { tree, field, change, button, update: (patch) => act(() => tree.update(React.createElement(CoverEditor, { ...props, ...patch }))) };
}

test("cover submission captures selected export and activity; failures retain settings", async (t) => {
  let sent;
  const ui = setup(t, { onGenerateCover: async (...args) => { sent = args; return null; } });
  ui.change("封面时间（秒）", ".6");
  ui.change("封面标题", "杜亚智能窗帘");
  await act(async () => { await ui.button().props.onClick(); });
  assert.equal(sent[0].source_video_path, "export.mp4");
  assert.equal(sent[0].settings.time_seconds, .6);
  assert.deepEqual(sent[1].expectedScope, { projectId: "p", activityId: "a" });
  assert.equal(ui.field("封面标题").props.value, "杜亚智能窗帘");
  assert.match(JSON.stringify(ui.tree.toJSON()), /设置已保留/);
  ui.change("封面时间（秒）", "2");
  assert.equal(ui.button().props.disabled, true);
  ui.update({ busy: true });
  assert.equal(ui.tree.root.findByType("fieldset").props.disabled, true);
});

test("saved cover gallery is restricted to selected video and uses attachment downloads", (t) => {
  const ui = setup(t, { onGenerateCover: undefined, activity: { activity_id: "a", metadata: { covers: [
    { source_video_path: "other.mp4", image_path: "other.png", settings: {} },
    { source_video_path: "export.mp4", image_path: "current.png", frame_time_seconds: .5, settings: { title: "saved" }, width: 720, height: 1280 },
  ] } } });
  assert.equal(ui.tree.root.findByType("img").props.alt, "saved");
  assert.match(ui.tree.root.findByType("a").props.href, /current.png&download=1$/);
  assert.equal(ui.tree.root.findAllByType("option").length, 1);
});
