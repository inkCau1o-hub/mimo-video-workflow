import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import React from "react";
import { act, create } from "react-test-renderer";
import { build } from "esbuild";
import { editShotSettings, isImageAsset, serializeShots, shotEdit, shotErrors, shotTiming } from "../src/editor/shotEditing.js";

const bundle = await build({
  stdin: { contents: 'export { StoryboardPage } from "./src/pages/StoryboardPage.jsx"; export { MaterialLibraryPage } from "./src/pages/MaterialLibraryPage.jsx";',
    resolveDir: fileURLToPath(new URL("../", import.meta.url)) },
  bundle: true, platform: "node", format: "cjs", jsx: "automatic", write: false, external: ["react", "react/jsx-runtime"],
});
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const { StoryboardPage, MaterialLibraryPage } = module.exports;
globalThis.ResizeObserver = class { observe() {} disconnect() {} };

const photo = { asset_id: "p1", file_path: "media/materials/photo_1a2b.jpg", duration_seconds: null, metadata: { asset_type: "image" } };
const shot = { shot_id: "s1", title: "板卡", tts_text: "", duration_seconds: 3, metadata: { material_asset_id: "p1" } };

test("photos are recognised by type or file name and never run short", () => {
  assert.equal(isImageAsset(photo), true);
  assert.equal(isImageAsset({ file_path: "IMG_0001.HEIC" }), true);
  assert.equal(isImageAsset({ file_path: "clip.mp4", duration_seconds: 2 }), false);
  const timing = shotTiming(shot, photo, null);
  assert.equal(timing.still, true);
  assert.equal(timing.shortage, false);
  assert.equal(timing.target, 3);
});

test("motion defaults to a gentle push, merges, validates and saves as numbers", () => {
  assert.deepEqual(shotEdit(shot).motion, { kind: "push", zoom: 1.08 });
  const [pulled] = editShotSettings(editShotSettings([shot], "s1", { motion: { kind: "pull" } }), "s1", { motion: { zoom: "1.2" } });
  assert.deepEqual(pulled.metadata.edit.motion, { kind: "pull", zoom: "1.2" });
  assert.deepEqual(serializeShots([pulled])[0].metadata.edit.motion, { kind: "pull", zoom: 1.2 });
  const [wild] = editShotSettings([shot], "s1", { motion: { zoom: 3 } });
  assert.ok(shotErrors(wild, shotTiming(wild, photo, null)).some((error) => error.includes("推拉幅度")));
});

test("a photo shot offers push, pull or still and saves the choice", async (t) => {
  let sent;
  const activity = { activity_id: "a1", storyboard_analysis: { shots: [shot] }, material_assets: [photo], voiceover_segments: [] };
  const props = { project: { project_id: "p1" }, activity, backendUrl: "http://127.0.0.1:8765", onGoVoiceover() {},
    history: { cursor: 0, can_undo: false, can_redo: false, entries: [{ id: "r0" }] },
    onSaveShots: async (shots) => { sent = shots; return null; } };
  let tree;
  act(() => { tree = create(React.createElement(StoryboardPage, props)); });
  t.after(() => act(() => tree.unmount()));
  const field = (name) => tree.root.findAllByType("label").find((item) => item.children.some((child) => child === name))
    .findAll((node) => ["input", "select"].includes(node.type))[0];
  assert.equal(field("图片运动").props.value, "push");
  assert.ok(tree.root.findAll((node) => node.type === "img").length > 0);
  act(() => field("图片运动").props.onChange({ target: { value: "pull" } }));
  act(() => field("推拉幅度（倍）").props.onChange({ target: { value: "1.15" } }));
  const save = tree.root.findAllByType("button").find((item) => item.props["aria-label"] === "保存分镜");
  await act(async () => { await save.props.onClick(); });
  assert.deepEqual(sent[0].metadata.edit.motion, { kind: "pull", zoom: 1.15 });
});

test("the material library accepts photos", (t) => {
  let tree;
  act(() => { tree = create(React.createElement(MaterialLibraryPage, { project: { project_id: "p1" }, activity: { activity_id: "a1", material_assets: [] } })); });
  t.after(() => act(() => tree.unmount()));
  const input = tree.root.findAll((node) => node.type === "input" && node.props.type === "file")[0];
  for (const kind of [".heic", ".jpg", ".png"]) assert.match(input.props.accept, new RegExp(`\\${kind}`));
});
