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
const activity = { activity_id: "a1", name: "Audio", metadata: {}, storyboard_analysis: { shots: [{ shot_id: "s1" }] },
  material_assets: [{ asset_id: "m1" }], voiceover_segments: [{ shot_id: "s1", audio_path: "voice.wav" }] };

function setup(t, overrides = {}) {
  let props = { project: { project_id: "p1" }, activity, backendUrl: "http://localhost", onSaveAudioSettings: async () => null,
    onRenderVideo() {}, onUploadMusic: async () => null, ...overrides };
  let tree;
  act(() => { tree = create(React.createElement(AssemblyPage, props)); });
  t.after(() => act(() => tree.unmount()));
  const button = (name) => tree.root.findAllByType("button").find((item) => item.props["aria-label"] === name || item.children.join("") === name);
  const control = (name) => tree.root.findAll((item) => ["input", "select"].includes(item.type) && item.props["aria-label"] === name)[0];
  const change = (name, value) => act(() => control(name).props.onChange({ target: { value, checked: value } }));
  const click = async (name) => { assert.equal(Boolean(button(name).props.disabled), false); await act(async () => { await button(name).props.onClick(); }); };
  const update = (next) => act(() => { props = { ...props, ...next }; tree.update(React.createElement(AssemblyPage, props)); });
  return { tree, button, control, change, click, update };
}

test("legacy audio settings do not silently enable processing", (t) => {
  const ui = setup(t);
  assert.equal(ui.control("统一配音音量").props.checked, false);
  assert.equal(ui.control("背景音乐").props.value, "");
  assert.equal(ui.button("保存声音设置").props.disabled, true);
  assert.equal(ui.button("导出视频").props.disabled, false);
});

test("audio drafts block export and save with captured revision and scope", async (t) => {
  let sent;
  const ui = setup(t, { activity: { ...activity, metadata: { audio_revision: "r1" } },
    onSaveAudioSettings: async (...args) => { sent = args; return { project: {} }; } });
  ui.change("统一配音音量", true);
  assert.equal(ui.button("导出视频").props.disabled, true);
  await ui.click("保存声音设置");
  assert.equal(sent[0].normalize_voice, true);
  assert.deepEqual(sent[1], { baseRevision: "r1", expectedScope: { projectId: "p1", activityId: "a1" } });
  assert.equal(ui.button("导出视频").props.disabled, false);
});

test("failed saves retain audio drafts and invalid numbers cannot be saved", async (t) => {
  const ui = setup(t);
  ui.change("统一配音音量", true);
  await ui.click("保存声音设置");
  assert.equal(ui.control("统一配音音量").props.checked, true);
  assert.equal(ui.button("导出视频").props.disabled, true);
  assert.match(JSON.stringify(ui.tree.toJSON()), /草稿已保留/);
  for (const value of ["", "-99", "Infinity"]) {
    ui.change("目标响度（LUFS）", value);
    assert.equal(ui.button("保存声音设置").props.disabled, true);
  }
});

test("concurrent audio revision keeps draft and requires reload before save", async (t) => {
  const ui = setup(t);
  ui.change("统一配音音量", true);
  ui.update({ activity: { ...activity, metadata: { audio_revision: "r2", audio_settings: { voice_gain_db: -4 } } } });
  assert.equal(ui.control("统一配音音量").props.checked, true);
  assert.equal(ui.button("保存声音设置").props.disabled, true);
  assert.match(JSON.stringify(ui.tree.toJSON()), /其他位置修改/);
  await ui.click("放弃声音修改并重新载入");
  assert.equal(ui.control("配音音量（dB）").props.value, -4);
});

test("music upload selects a draft without changing saved export settings", async (t) => {
  let received;
  const ui = setup(t, { onUploadMusic: async (...args) => { received = args; return { uploaded_music: { file_path: "music.wav" } }; } });
  const file = new File(["audio"], "music.wav");
  await act(async () => ui.control("导入音乐文件").props.onChange({ target: { files: [file], value: "music.wav" } }));
  assert.equal(received[0], file);
  assert.deepEqual(received[1], { expectedScope: { projectId: "p1", activityId: "a1" } });
  assert.equal(ui.control("背景音乐").props.value, "music.wav");
  assert.equal(ui.control("配音时降低音乐").props.checked, true);
  assert.equal(ui.button("导出视频").props.disabled, true);
  assert.equal(ui.tree.root.findByType("audio").props["aria-label"], "背景音乐原音试听");
});

test("music controls and removal retain the imported asset", async (t) => {
  let sent;
  const saved = { ...activity, metadata: { music_assets: [{ file_path: "music.wav", name: "Music" }], audio_settings: { music_path: "music.wav" } } };
  const ui = setup(t, { activity: saved, onSaveAudioSettings: async (input) => { sent = input; return { project: {} }; } });
  ui.change("音乐音量（dB）", "-28");
  ui.change("配音时降低音乐", false);
  ui.change("循环音乐", false);
  ui.change("音乐淡出（秒）", "2.5");
  await ui.click("保存声音设置");
  assert.equal(sent.music_gain_db, -28);
  assert.equal(sent.duck_music, false);
  assert.equal(sent.loop_music, false);
  assert.equal(sent.fade_out_seconds, 2.5);
  ui.change("背景音乐", "");
  await ui.click("保存声音设置");
  assert.equal(sent.music_path, "");
  assert.equal(saved.metadata.music_assets.length, 1);
});

test("export carries saved audio revision and running jobs lock audio controls", async (t) => {
  let sent;
  const ui = setup(t, { activity: { ...activity, metadata: { audio_revision: "r3" } }, onRenderVideo: (input) => { sent = input; } });
  await ui.click("导出视频");
  assert.equal(sent.audio_revision, "r3");
  ui.update({ busy: true });
  assert.equal(ui.button("导出视频").props.disabled, true);
  assert.equal(ui.tree.root.findAllByType("fieldset").find((item) => item.props.className === "audio-settings").props.disabled, true);
});

test("duplicate music display names remain distinguishable by stored filename", (t) => {
  const ui = setup(t, { activity: { ...activity, metadata: { music_assets: [
    { file_path: "media/music/test.wav", name: "test.wav" }, { file_path: "media/music/test_2.wav", name: "test.wav" },
  ] } } });
  const labels = ui.control("背景音乐").findAllByType("option").map((item) => item.children.join(""));
  assert.deepEqual(labels, ["无", "test.wav", "test_2.wav"]);
});
