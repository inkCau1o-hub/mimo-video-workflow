import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import React from "react";
import { act, create } from "react-test-renderer";
import { build } from "esbuild";

const bundle = await build({
  stdin: { contents: 'export { VoiceoverPage } from "./src/pages/VoiceoverPage.jsx";', resolveDir: fileURLToPath(new URL("../", import.meta.url)) },
  bundle: true, platform: "node", format: "cjs", jsx: "automatic", write: false, external: ["react", "react/jsx-runtime"],
});
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const { VoiceoverPage } = module.exports;

const pauses = [
  { start: 0.7, end: 1.6, seconds: 0.9, before: "一二三四", after: "五六七八" },
  { start: 2.0, end: 2.2, seconds: 0.2, before: "八", after: "九十" },
];
const activity = (items, metadata = {}) => ({
  activity_id: "a1", metadata, storyboard_analysis: { shots: [{ shot_id: "s1", tts_text: "一二三四，五六七八，九十。" }] },
  voiceover_segments: [{ shot_id: "s1", segment_id: "v1", audio_path: "v.wav", text: "一二三四，五六七八，九十。",
    metadata: { subtitle_alignment: { source: "aligned", cues: [], warnings: [], pauses: items } } }],
});

function page(t, props) {
  const calls = [];
  let tree;
  act(() => { tree = create(React.createElement(VoiceoverPage, { project: { project_id: "p1" }, backendUrl: "http://localhost",
    onGenerateVoiceovers() {}, onRegenerateVoiceover: async () => {}, onShortenPauses: (payload) => calls.push(payload), ...props })); });
  t.after(() => act(() => tree.unmount()));
  const button = () => tree.root.findAllByType("button").find((item) => item.children.join("") === "压短超长停顿");
  const limit = () => tree.root.findAll((item) => item.type === "input" && item.props["aria-label"] === "句内停顿上限（秒）")[0];
  return { calls, button, limit, text: () => JSON.stringify(tree.toJSON()) };
}

test("pauses longer than the limit are flagged with where they fall", (t) => {
  const ui = page(t, { activity: activity(pauses) });
  assert.equal(ui.limit().props.value, 0.5);
  assert.match(ui.text(), /句内停顿 0\.90 秒/);
  assert.match(ui.text(), /一二三四｜五六七八/);
  assert.doesNotMatch(ui.text(), /0\.20 秒/);
});

test("a stricter limit flags more and the shortening uses it", (t) => {
  const ui = page(t, { activity: activity(pauses, { pause_limit_seconds: 0.4 }) });
  assert.equal(ui.limit().props.value, 0.4);
  act(() => ui.limit().props.onChange({ target: { value: "0.1" } }));
  assert.match(ui.text(), /0\.20 秒/);
  act(() => ui.button().props.onClick());
  assert.deepEqual(ui.calls, [{ pause_limit_seconds: 0.1 }]);
});

test("without a pause above the limit there is nothing to shorten", (t) => {
  // 5 ms over the limit is within the detection tolerance the backend also allows.
  const ui = page(t, { activity: activity([pauses[1], { start: 3, end: 3.505, seconds: 0.505, before: "", after: "" }]) });
  assert.equal(ui.button().props.disabled, true);
  assert.doesNotMatch(ui.text(), /句内停顿超过/);
});

test("after a shortening the page says what changed and which subtitles to check", (t) => {
  const job = { job_id: "j1", job_type: "voiceovers.pauses", status: "succeeded",
    result: { shortened: [{ shot_id: "s1", audio_path: "v.wav", pauses: 1, saved_seconds: 0.4 }], alignment: [{ shot_id: "s1", applied: false }] } };
  const ui = page(t, { activity: activity([]), job });
  assert.match(ui.text(), /已压短 1 段配音，共缩短 0\.40 秒/);
  assert.match(ui.text(), /1 个镜头的字幕不是自动生成的/);
});

test("an old shortening result is not shown once the voiceovers have moved on", (t) => {
  // Undone: the voiceover is back on its original file.
  const undone = { job_id: "j1", job_type: "voiceovers.pauses", status: "succeeded",
    result: { shortened: [{ shot_id: "s1", audio_path: "v_停顿0.5s.wav", pauses: 1, saved_seconds: 0.4 }], alignment: [] } };
  const first = page(t, { activity: activity(pauses), job: undone });
  assert.doesNotMatch(first.text(), /已压短/);
  // Nothing was long enough then, but a pause is flagged now.
  const empty = { ...undone, result: { shortened: [], alignment: [] } };
  const second = page(t, { activity: activity(pauses), job: empty });
  assert.doesNotMatch(second.text(), /没有需要压短的停顿/);
  assert.match(page(t, { activity: activity([]), job: empty }).text(), /没有需要压短的停顿/);
});
