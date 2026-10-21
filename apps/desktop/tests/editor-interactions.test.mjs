import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import React from "react";
import { act, create } from "react-test-renderer";
import { build } from "esbuild";

const bundle = await build({
  stdin: { contents: 'export { StoryboardPage } from "./src/pages/StoryboardPage.jsx";', resolveDir: fileURLToPath(new URL("../", import.meta.url)) },
  bundle: true, platform: "node", format: "cjs", jsx: "automatic", write: false,
  external: ["react", "react/jsx-runtime"],
});
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const { StoryboardPage } = module.exports;
globalThis.ResizeObserver = class { observe() {} disconnect() {} };

const shot = { shot_id: "s1", title: "Original", script_text: "Script", tts_text: "Voice", duration_seconds: 4, metadata: { material_asset_id: "m1" } };
const activity = {
  activity_id: "a1", storyboard_analysis: { shots: [shot] },
  material_assets: [{ asset_id: "m1", file_path: "source.mp4", duration_seconds: 20 }],
  voiceover_segments: [{ shot_id: "s1", text: "Voice", audio_path: "voice.wav", duration_seconds: 3 }],
};
const history = { cursor: 1, can_undo: true, can_redo: false, entries: [{ id: "r0", label: "Initial" }, { id: "r1", label: "Saved" }] };

function setup(t, overrides = {}) {
  const props = { project: { project_id: "p1" }, activity: structuredClone(activity), backendUrl: "http://127.0.0.1:8765", history, onGoVoiceover() {}, ...overrides };
  let tree;
  act(() => { tree = create(React.createElement(StoryboardPage, props)); });
  t.after(() => act(() => tree.unmount()));
  const button = (name) => tree.root.findAllByType("button").find((item) => item.props["aria-label"] === name);
  const field = (name) => tree.root.findAllByType("label").find((item) => item.children.some((child) => typeof child === "string" && child === name)).findAll((node) => ["textarea", "input", "select"].includes(node.type))[0];
  const change = (name, value) => act(() => field(name).props.onChange({ target: { value } }));
  const click = async (name) => { assert.equal(Boolean(button(name).props.disabled), false, `${name} is disabled`); await act(async () => { await button(name).props.onClick(); }); };
  return { tree, props, button, field, change, click, update: (next) => act(() => tree.update(React.createElement(StoryboardPage, { ...props, ...next }))) };
}

test("caption edit, draft undo/redo and save preserve narration and captured revision", async (t) => {
  let sent;
  const ui = setup(t, { onSaveShots: async (shots, options) => {
    sent = { shots, options };
    return { project: { activities: [{ ...activity, storyboard_analysis: { shots } }] }, history: { ...history, cursor: 2, entries: [...history.entries, { id: "r2" }] } };
  } });
  ui.change("字幕正文", "Caption\nSecond line");
  await ui.click("撤销草稿");
  assert.equal(ui.field("字幕正文").props.value, "Voice");
  await ui.click("重做草稿");
  assert.equal(ui.field("字幕正文").props.value, "Caption\nSecond line");
  await ui.click("保存分镜");
  assert.equal(sent.shots[0].tts_text, "Voice");
  assert.equal(sent.shots[0].metadata.edit.subtitle.text, "Caption\nSecond line");
  assert.equal(sent.options.baseRevision, "r1");
  assert.deepEqual(sent.options.expectedScope, { projectId: "p1", activityId: "a1" });
  assert.equal(ui.button("保存分镜").props.disabled, true);
});

test("framing presets and reset affect only the selected aspect ratio", async (t) => {
  let sent;
  const ui = setup(t, { onSaveShots: async (shots) => { sent = shots; return null; } });
  await ui.click("构图左侧");
  ui.change("预览画幅", "16:9");
  await ui.click("构图右侧");
  ui.change("预览画幅", "9:16");
  assert.equal(ui.button("构图左侧").props["aria-pressed"], true);
  await ui.click("保存分镜");
  assert.equal(sent[0].metadata.edit.framing["9:16"].crop_x, 0);
  assert.equal(sent[0].metadata.edit.framing["16:9"].crop_x, 1);
  await ui.click("重置当前画幅构图");
  assert.equal(ui.button("构图居中").props["aria-pressed"], true);
  ui.change("预览画幅", "16:9");
  assert.equal(ui.button("构图右侧").props["aria-pressed"], true);
  assert.equal(ui.field("到下一镜头").props.disabled, true);
});

test("failed saves preserve drafts and do not enable saved-version recovery", async (t) => {
  const ui = setup(t, { onSaveShots: async () => { throw new Error("disk full"); } });
  ui.change("字幕正文", "Unsaved caption");
  await ui.click("保存分镜");
  assert.equal(ui.field("字幕正文").props.value, "Unsaved caption");
  assert.equal(ui.button("撤销已保存版本").props.disabled, true);
  assert.equal(ui.button("保存分镜").props.disabled, false);
  assert.match(JSON.stringify(ui.tree.toJSON()), /disk full/);
  await ui.click("放弃草稿并重新载入");
  assert.equal(ui.field("字幕正文").props.value, "Voice");
  assert.equal(ui.button("撤销已保存版本").props.disabled, false);
});

test("copy, reorder and deletion keep stable IDs across draft undo", async (t) => {
  let sent;
  const ui = setup(t, { onSaveShots: async (shots) => { sent = shots; return null; } });
  await ui.click("复制镜头");
  assert.equal(ui.tree.root.findAllByProps({ role: "listitem" }).length, 2);
  await ui.click("上移镜头");
  await ui.click("保存分镜");
  const copiedId = sent[0].shot_id;
  assert.notEqual(copiedId, "s1");
  assert.equal(sent[0].metadata.duplicate_of, "s1");
  await ui.click("删除镜头");
  assert.equal(ui.tree.root.findAllByProps({ role: "listitem" }).length, 1);
  await ui.click("撤销草稿");
  await ui.click("保存分镜");
  assert.deepEqual(sent.map((item) => item.shot_id), [copiedId, "s1"]);
});

test("version recovery captures selected revision and is unavailable during a task", async (t) => {
  let sent;
  const ui = setup(t, { onHistoryAction: async (action, options) => { sent = { action, options }; return {}; } });
  const select = ui.tree.root.findAllByType("select").find((item) => item.props["aria-label"] === "已保存版本");
  act(() => select.props.onChange({ target: { value: "r0" } }));
  await ui.click("从所选版本恢复为新版本");
  assert.equal(sent.action, "restore");
  assert.equal(sent.options.revisionId, "r0");
  assert.equal(sent.options.baseRevision, "r1");
  ui.update({ editorJobRunning: true });
  assert.equal(ui.button("从所选版本恢复为新版本").props.disabled, true);
  assert.equal(ui.button("复制镜头").props.disabled, true);
});

test("background revision changes retain unsaved draft and block stale saves", (t) => {
  const ui = setup(t, { onSaveShots() {} });
  ui.change("字幕正文", "Local caption");
  ui.update({ history: { ...history, entries: [{ id: "r0" }, { id: "new-revision" }] } });
  assert.equal(ui.field("字幕正文").props.value, "Local caption");
  assert.equal(ui.button("保存分镜").props.disabled, true);
  assert.match(JSON.stringify(ui.tree.toJSON()), /后台版本已变化/);
});

test("invalid ranges and manual durations block saving", (t) => {
  const ui = setup(t, { onSaveShots() {} });
  ui.change("片段起点（秒）", "21");
  assert.equal(ui.button("保存分镜").props.disabled, true);
  ui.change("片段起点（秒）", "1");
  ui.change("目标时长模式", "manual");
  ui.change("镜头时长（秒）", "2");
  assert.equal(ui.button("保存分镜").props.disabled, true);
  ui.change("镜头时长（秒）", "4");
  assert.equal(ui.button("保存分镜").props.disabled, false);
});

const cueField = (ui, name) => ui.tree.root.findAllByType("label")
  .find((label) => label.children.filter((item) => ["string", "number"].includes(typeof item)).join("") === name)
  .findAll((node) => ["input", "textarea"].includes(node.type))[0];

test("timing drafts generate editable cues, preserve narration and serialize on save", async (t) => {
  let sent;
  const ui = setup(t, { onSaveShots: async (shots) => { sent = shots; return null; } });
  ui.change("配音前留白（秒）", ".3");
  ui.change("配音后留白（秒）", ".2");
  ui.change("字幕正文", "First.\nSecond.");
  ui.change("字幕时间模式", "cues");
  await ui.click("按正文估算分句时间");
  assert.match(JSON.stringify(ui.tree.toJSON()), /待校准/);
  assert.equal(cueField(ui, "第 1 条开始（秒）").props.value, .3);
  assert.equal(cueField(ui, "第 2 条结束（秒）").props.value, 3.3);
  assert.equal(ui.button("按正文估算分句时间").props.disabled, true);
  act(() => cueField(ui, "第 2 条结束（秒）").props.onChange({ target: { value: "8" } }));
  assert.equal(ui.button("保存分镜").props.disabled, true);
  act(() => cueField(ui, "第 2 条结束（秒）").props.onChange({ target: { value: "3.2" } }));
  await ui.click("标记字幕时间已校准");
  await ui.click("保存分镜");
  assert.equal(sent[0].tts_text, "Voice");
  assert.equal(sent[0].metadata.edit.lead_in_seconds, .3);
  assert.equal(sent[0].metadata.edit.subtitle.cues[1].end, 3.2);
  assert.equal(sent[0].metadata.edit.subtitle.timing_source, "manual");
  await ui.click("删除第 2 条字幕");
  await ui.click("撤销草稿");
  assert.equal(cueField(ui, "第 2 条结束（秒）").props.value, "3.2");
});

test("brand correction previews, applies to draft and is undoable without narration changes", async (t) => {
  let sent;
  const ui = setup(t, { onSaveShots: async (shots) => { sent = shots; return null; } });
  ui.change("字幕正文", "读亚窗帘。读亚。");
  ui.change("原词", "读亚");
  ui.change("正确词 / 品牌词", "杜亚");
  assert.equal(ui.tree.root.findByProps({ "aria-label": "纠词预览" }).findAllByType("ins")[0].children[0], "杜亚窗帘。杜亚。");
  await ui.click("将字幕纠词应用到草稿");
  assert.equal(ui.field("字幕正文").props.value, "杜亚窗帘。杜亚。");
  assert.equal(ui.button("将字幕纠词应用到草稿").props.disabled, true);
  await ui.click("撤销草稿");
  assert.equal(ui.field("字幕正文").props.value, "读亚窗帘。读亚。");
  await ui.click("重做草稿");
  await ui.click("保存分镜");
  assert.equal(sent[0].tts_text, "Voice");
  assert.equal(sent[0].script_text, "Script");
});

test("gap edits reject truncated manual shots and invalid numbers", (t) => {
  const ui = setup(t, { onSaveShots() {} });
  ui.change("目标时长模式", "manual");
  ui.change("配音前留白（秒）", "1");
  ui.change("配音后留白（秒）", "1");
  assert.equal(ui.button("保存分镜").props.disabled, true);
  ui.change("镜头时长（秒）", "5");
  assert.equal(ui.button("保存分镜").props.disabled, false);
  ui.change("配音前留白（秒）", "");
  assert.equal(ui.button("保存分镜").props.disabled, true);
});

test("video preview displays timed cue text relative to source trim, not inactive caption", (t) => {
  const ui = setup(t);
  ui.change("片段起点（秒）", "5");
  ui.change("字幕正文", "First.\nSecond.");
  ui.change("配音前留白（秒）", ".3");
  ui.change("字幕时间模式", "cues");
  act(() => ui.button("按正文估算分句时间").props.onClick());
  const caption = () => ui.tree.root.findAll((node) => node.type === "div" && node.props.className?.startsWith("shot-subtitle "));
  const seek = (currentTime) => act(() => ui.tree.root.findByProps({ "aria-label": "镜头选段预览" }).props.onTimeUpdate({ currentTarget: { currentTime, pause() {} } }));
  seek(5.1);
  assert.equal(caption().length, 0);
  seek(5.5);
  assert.equal(caption()[0].findByType("span").children[0], "First.");
  seek(7.5);
  assert.equal(caption()[0].findByType("span").children[0], "Second.");
  seek(9);
  assert.equal(caption().length, 0);
});
