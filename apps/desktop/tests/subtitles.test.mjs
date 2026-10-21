import test from "node:test";
import assert from "node:assert/strict";
import { estimateSubtitleCues, subtitleCorrections, applySubtitleCorrection, subtitleAtTime } from "../src/editor/subtitleEditing.js";
import { shotEdit, shotTiming, shotErrors, serializeShots } from "../src/editor/shotEditing.js";

const shot = { shot_id: "one", tts_text: "杜亚智能窗帘", script_text: "Script", duration_seconds: 6,
  metadata: { other: "keep", edit: { subtitle: { text: "读亚智能窗帘。读亚。" } } } };

test("preview caption uses shot-relative cue times and hides during gaps", () => {
  const edit = { lead_in_seconds: .3, tail_out_seconds: .2, subtitle: { text: "fallback", timing_mode: "cues", cues: [{ start: .3, end: 1, text: "first" }, { start: 2, end: 3, text: "second" }] } };
  assert.equal(subtitleAtTime(edit, .2), "");
  assert.equal(subtitleAtTime(edit, .3), "first");
  assert.equal(subtitleAtTime(edit, 1), "");
  assert.equal(subtitleAtTime(edit, 2.5), "second");
  edit.subtitle.timing_mode = "narration";
  assert.equal(subtitleAtTime(edit, .2, { target: 6, voiceDuration: 3 }), "");
  assert.equal(subtitleAtTime(edit, 1, { target: 6, voiceDuration: 3 }), "fallback");
  assert.equal(subtitleAtTime(edit, 3.3, { target: 6, voiceDuration: 3 }), "");
  edit.subtitle.timing_mode = "full";
  assert.equal(subtitleAtTime(edit, 6, { target: 6 }), "");
});

test("subtitle modes and gaps preserve legacy defaults and timing", () => {
  const edit = shotEdit({ tts_text: "old" });
  assert.equal(edit.subtitle.timing_mode, "full");
  assert.deepEqual(edit.subtitle.cues, []);
  assert.equal(edit.lead_in_seconds, 0);
  const value = structuredClone(shot);
  Object.assign(value.metadata.edit, { lead_in_seconds: .2, tail_out_seconds: .3 });
  const timing = shotTiming(value, null, { duration_seconds: 3 });
  assert.equal(timing.target, 3.5);
  assert.equal(shotErrors(value, timing).length, 0);
  value.metadata.edit.duration_mode = "manual";
  value.duration_seconds = 3.2;
  assert.match(shotErrors(value, shotTiming(value, null, { duration_seconds: 3 })).join(" "), /留白/);
});

test("estimated cues split punctuation and conserve the time range without zero-length rows", () => {
  const cues = estimateSubtitleCues("第一句。第二句！末句", .2, 3.55);
  assert.equal(cues.length, 3);
  assert.equal(cues[0].start, .2);
  assert.equal(cues.at(-1).end, 3.55);
  assert.equal(cues.map((cue) => cue.text).join(""), "第一句。第二句！末句");
  assert.equal(cues[1].start, cues[0].end);
  assert.deepEqual(estimateSubtitleCues("一。二。", 0, .01), []);
  assert.deepEqual(estimateSubtitleCues("text", NaN, 3), []);
  const many = estimateSubtitleCues("甲。".repeat(150), 0, 30);
  assert.equal(many.length, 100);
  assert.equal(many.map((cue) => cue.text).join(""), "甲。".repeat(150));
});

test("literal brand correction previews and changes captions only", () => {
  const before = structuredClone(shot);
  const matches = subtitleCorrections([shot], "读亚", "杜亚");
  assert.equal(matches[0].count, 2);
  assert.equal(matches[0].after, "杜亚智能窗帘。杜亚。");
  const corrected = applySubtitleCorrection([shot], "读亚", "杜亚")[0];
  assert.equal(corrected.metadata.edit.subtitle.text, matches[0].after);
  assert.equal(corrected.tts_text, shot.tts_text);
  assert.equal(corrected.script_text, shot.script_text);
  assert.equal(corrected.metadata.other, "keep");
  assert.deepEqual(shot, before);
  for (const [from, to] of [["", "x"], ["杜亚", ""], ["x", "x"]]) assert.deepEqual(subtitleCorrections([shot], from, to), []);
});

test("cue correction keeps exact timestamps and does not interpret regex or replacement syntax", () => {
  const value = structuredClone(shot);
  value.metadata.edit.subtitle = { text: "inactive", timing_mode: "cues", cues: [{ start: .3, end: 2, text: "a.* a.*" }] };
  const corrected = applySubtitleCorrection([value], "a.*", "$&")[0];
  assert.deepEqual(corrected.metadata.edit.subtitle.cues, [{ start: .3, end: 2, text: "$& $&" }]);
  assert.equal(corrected.metadata.edit.subtitle.text, "inactive");
  assert.equal(subtitleCorrections([corrected], "inactive", "changed").length, 0);
});

test("cue gaps are legal while overlap, overflow, blanks and nonfinite times block save", () => {
  const value = structuredClone(shot);
  value.metadata.edit.subtitle = { timing_mode: "cues", text: "", cues: [{ start: .2, end: 1, text: "one" }, { start: 2, end: 3, text: "two" }] };
  assert.deepEqual(shotErrors(value, shotTiming(value, null, { duration_seconds: 3 })), []);
  for (const patch of [{ start: "" }, { start: -.1 }, { start: .9 }, { end: 3.1 }, { end: Infinity }, { text: " " }]) {
    const invalid = structuredClone(value);
    Object.assign(invalid.metadata.edit.subtitle.cues[1], patch);
    assert.ok(shotErrors(invalid, shotTiming(invalid, null, { duration_seconds: 3 })).length);
  }
  value.metadata.edit.lead_in_seconds = "";
  assert.match(shotErrors(value, shotTiming(value, null, null)).join(" "), /留白/);
});

test("serialization normalizes cue times and gaps without changing text", () => {
  const value = structuredClone(shot);
  Object.assign(value.metadata.edit, { lead_in_seconds: ".2", tail_out_seconds: ".3" });
  value.metadata.edit.subtitle.cues = [{ start: ".2", end: "3", text: "123" }];
  const saved = serializeShots([value])[0].metadata.edit;
  assert.equal(saved.lead_in_seconds, .2);
  assert.equal(saved.tail_out_seconds, .3);
  assert.deepEqual(saved.subtitle.cues, [{ start: .2, end: 3, text: "123" }]);
});
