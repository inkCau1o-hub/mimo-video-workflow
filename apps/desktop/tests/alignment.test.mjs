import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import React from "react";
import { act, create } from "react-test-renderer";
import { build } from "esbuild";
import { alignedCues, shotEdit } from "../src/editor/shotEditing.js";

const bundle = await build({
  stdin: { contents: 'export { SubtitleTiming } from "./src/components/SubtitleTiming.jsx"; export { VoiceoverPage } from "./src/pages/VoiceoverPage.jsx";',
    resolveDir: fileURLToPath(new URL("../", import.meta.url)) },
  bundle: true, platform: "node", format: "cjs", jsx: "automatic", write: false, external: ["react", "react/jsx-runtime"],
});
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const { SubtitleTiming, VoiceoverPage } = module.exports;

const alignment = { source: "aligned", warnings: ["第 2 句与上一句之间有英文，时间请复核。"],
  cues: [{ start: 0.05, end: 1.1, text: "一二三四" }, { start: 1.1, end: 2.1, text: "五六七八" }] };
const shot = (subtitle = {}) => ({ shot_id: "s1", tts_text: "一二三四，五六七八。", duration_seconds: 3,
  metadata: { edit: { lead_in_seconds: 0.5, subtitle: { timing_mode: "cues", cues: [], ...subtitle } } } });

function timingPanel(t, props) {
  const calls = [];
  let tree;
  act(() => { tree = create(React.createElement(SubtitleTiming, { edit: shotEdit(shot(props.subtitle)), timing: { voiceDuration: 2.2, target: 3 },
    onSettings: (patch) => calls.push(patch), onPlayCue() {}, hasVoice: true, disabled: false, alignment: props.alignment })); });
  t.after(() => act(() => tree.unmount()));
  const button = (label) => tree.root.findAllByType("button").find((item) => item.props["aria-label"] === label);
  return { calls, button, text: () => JSON.stringify(tree.toJSON()) };
}

test("measured cues sit after the lead-in on the shot timeline", () => {
  assert.deepEqual(alignedCues(alignment, 0.5), [{ start: 0.55, end: 1.6, text: "一二三四" }, { start: 1.6, end: 2.6, text: "五六七八" }]);
  assert.deepEqual(alignedCues(null, 0.5), []);
});

test("measured subtitles are labelled and can be re-applied after the lead-in changes", (t) => {
  const ui = timingPanel(t, { subtitle: { timing_source: "aligned", cues: [{ start: 0.55, end: 1.6, text: "一二三四" }] }, alignment });
  assert.match(ui.text(), /实测时间/);
  assert.doesNotMatch(ui.text(), /待校准/);
  assert.match(ui.text(), /第 2 句与上一句之间有英文/);
  act(() => ui.button("按配音实测时间重新分句").props.onClick());
  assert.deepEqual(ui.calls.at(-1), { subtitle: { cues: alignedCues(alignment, 0.5), timing_source: "aligned" } });
});

test("without a measurement only the estimate is offered", (t) => {
  const ui = timingPanel(t, { subtitle: {}, alignment: undefined });
  assert.equal(ui.button("按配音实测时间重新分句").props.disabled, true);
  assert.equal(Boolean(ui.button("按正文估算分句时间").props.disabled), false);
});

test("the voiceover page can align existing voiceovers without regenerating them", (t) => {
  const calls = [];
  const activity = { activity_id: "a1", storyboard_analysis: { shots: [{ shot_id: "s1", tts_text: "一二三四" }] },
    voiceover_segments: [{ shot_id: "s1", segment_id: "v1", audio_path: "v.wav", text: "一二三四" }] };
  let tree;
  act(() => { tree = create(React.createElement(VoiceoverPage, { project: { project_id: "p1" }, activity, backendUrl: "http://localhost",
    onGenerateVoiceovers() {}, onRegenerateVoiceover: async () => {}, onAlignVoiceovers: () => calls.push("align") })); });
  t.after(() => act(() => tree.unmount()));
  const align = tree.root.findAllByType("button").find((item) => item.children.join("") === "按配音对齐字幕");
  assert.equal(Boolean(align.props.disabled), false);
  act(() => align.props.onClick());
  assert.deepEqual(calls, ["align"]);
});
