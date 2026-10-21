import test from "node:test";
import assert from "node:assert/strict";
import { createDraft, draftDirty, draftReducer } from "../src/editor/draftHistory.js";
import { deleteShot, duplicateShot, editShotSettings, enforcePreviewRange, historyRevision, historySummary, moveShot, previewRange, replaceShotMaterial, serializeShots, shotEdit, shotErrors, shotTiming, voiceForShot } from "../src/editor/shotEditing.js";

const original = { shot_id: "s1", tts_text: "Voice", script_text: "Script", duration_seconds: 4, notes: "keep", metadata: { material_asset_id: "m1", trace: "keep" } };
const voice = { shot_id: "s1", segment_id: "v1", text: "Voice", audio_path: "v.wav", duration_seconds: 5 };
const asset = { asset_id: "m1", duration_seconds: 10 };

test("old shots remain unmodified, caption fallback and explicit empty caption differ", () => {
  assert.equal(shotEdit(original).subtitle.text, "Voice");
  assert.equal(shotEdit({ script_text: "Script" }).subtitle.text, "Script");
  assert.equal(shotEdit({ ...original, metadata: { edit: { subtitle: { text: "" } } } }).subtitle.text, "");
  assert.deepEqual(serializeShots([original]), [original]);
  assert.equal(original.metadata.edit, undefined);
});

test("caption, crop and material changes keep narration and unrelated metadata intact", () => {
  let shots = editShotSettings([original], "s1", { subtitle: { text: "First\nSecond", background: true } });
  shots = editShotSettings(shots, "s1", { subtitle: { color: "#FFE066" }, crop_x: 0.8, source_in: 3, source_out: 7 });
  assert.equal(shots[0].tts_text, original.tts_text);
  assert.equal(shots[0].script_text, original.script_text);
  assert.equal(shots[0].metadata.trace, "keep");
  assert.equal(shots[0].metadata.edit.subtitle.text, "First\nSecond");
  assert.equal(voiceForShot(shots[0], shots, [voice]), voice);
  shots = replaceShotMaterial(shots, "s1", "m2");
  assert.equal(shots[0].metadata.material_asset_id, "m2");
  assert.equal(shots[0].metadata.edit.source_in, 0);
  assert.equal(shots[0].metadata.edit.source_out, null);
  assert.equal(shots[0].metadata.edit.crop_x, 0.8);
});

test("duplicates use fresh stable IDs and deep metadata with immediate source provenance", () => {
  const shots = editShotSettings([original], "s1", { subtitle: { text: "Caption" } });
  const copied = duplicateShot(shots, "s1", () => "copy1");
  assert.deepEqual(copied.map((shot) => shot.shot_id), ["s1", "copy1"]);
  assert.equal(copied[1].metadata.duplicate_of, "s1");
  copied[1].metadata.edit.subtitle.text = "Own caption";
  assert.equal(shots[0].metadata.edit.subtitle.text, "Caption");
  const twice = duplicateShot(copied, "copy1", () => "copy2");
  assert.equal(twice[2].metadata.duplicate_of, "copy1");
  assert.throws(() => duplicateShot(shots, "s1", () => "s1"), /ID/);
  assert.equal(duplicateShot(shots, "missing"), shots);
  assert.notEqual(duplicateShot(shots, "s1")[1].shot_id, duplicateShot(shots, "s1")[1].shot_id);
});

test("reorder and removal preserve stable identity and never delete the last shot", () => {
  const shots = duplicateShot([original], "s1", () => "copy");
  assert.deepEqual(moveShot(shots, "copy", -1).map((shot) => shot.shot_id), ["copy", "s1"]);
  assert.equal(moveShot(shots, "s1", -1), shots);
  assert.equal(deleteShot([original], "s1")[0], original);
  assert.deepEqual(deleteShot(shots, "copy"), [original]);
});

test("voice reuse needs unchanged narration and an existing original for copies", () => {
  const shots = duplicateShot([original], "s1", () => "copy");
  assert.equal(voiceForShot(shots[1], shots, [voice], [original]), voice);
  assert.equal(voiceForShot(shots[1], [shots[1]], [voice], [original]), null);
  assert.equal(voiceForShot({ ...original, tts_text: "Changed" }, shots, [voice]), null);
  assert.equal(voiceForShot(shots[1], [{ ...original, tts_text: "Changed" }, shots[1]], [voice], [original]), null);
  const legacy = { ...voice }; delete legacy.text;
  assert.equal(voiceForShot({ ...original, tts_text: "Changed" }, shots, [legacy], [original]), null);
});

test("existing copies never resurrect source audio after narration changes", () => {
  const shots = duplicateShot([original], "s1", () => "copy");
  const saved = [original, { ...shots[1], tts_text: "Changed" }];
  assert.equal(voiceForShot(shots[1], shots, [voice], saved), null);
  const own = { ...voice, shot_id: "copy", audio_path: "fresh.wav" };
  assert.equal(voiceForShot(shots[1], shots, [voice, own], shots), own);
});

test("actual voice controls target, manual cannot truncate it, browser duration wins", () => {
  assert.equal(shotTiming(original, asset, voice).target, 5);
  assert.equal(shotTiming(original, asset, null).target, 4);
  const manual = editShotSettings([original], "s1", { duration_mode: "manual" })[0];
  assert.match(shotErrors(manual, shotTiming(manual, asset, voice)).join(), /不能短于/);
  const actual = shotTiming(original, asset, voice, { media: 3, voice: 6 });
  assert.equal(actual.target, 6);
  assert.equal(actual.available, 3);
  assert.equal(actual.shortage, true);
});

test("range validation covers out of bounds, null end and non-finite fields", () => {
  for (const edit of [{ source_in: -1 }, { source_in: "" }, { source_in: Infinity }, { source_in: 10 }, { source_out: 11 }, { source_in: 4, source_out: 4 }, { source_out: "bad" }, { crop_x: 1.1 }, { crop_y: NaN }]) {
    const shot = editShotSettings([original], "s1", edit)[0];
    assert.ok(shotErrors(shot, shotTiming(shot, asset, voice)).length, JSON.stringify(edit));
  }
  const shot = editShotSettings([original], "s1", { source_in: 8, source_out: null, shortage: "freeze" })[0];
  assert.equal(shotTiming(shot, asset, voice).available, 2);
  assert.equal(shotTiming(shot, asset, voice).shortage, true);
  assert.deepEqual(shotErrors(shot, shotTiming(shot, asset, voice)), []);
  assert.equal(shotTiming(original, null, voice).available, null);
});

test("subtitle styles reject arbitrary directives and unsafe values", () => {
  for (const subtitle of [{ font_size: 15 }, { font_size: 97 }, { position: "bad" }, { color: "red; drawtext=bad" }, { text: 5 }, { background: "yes" }]) {
    const shot = editShotSettings([original], "s1", { subtitle })[0];
    assert.ok(shotErrors(shot, shotTiming(shot, asset, voice)).length);
  }
  const shot = editShotSettings([original], "s1", { subtitle: { text: "<script>\n{\\pos(1,2)}", color: "#AABBCC" } })[0];
  assert.deepEqual(shotErrors(shot, shotTiming(shot, asset, voice)), []);
});

test("serialization normalizes edited numbers without inventing legacy edit defaults", () => {
  const shot = editShotSettings([original], "s1", { source_in: "2", source_out: null, subtitle: { font_size: "52", text: "" } })[0];
  const result = serializeShots([{ ...shot, duration_seconds: "5" }])[0];
  assert.equal(result.duration_seconds, 5);
  assert.deepEqual(result.metadata.edit, { source_in: 2, source_out: null, subtitle: { font_size: 52, text: "" } });
});

test("range preview clamps seeks and stops at the selected end without looping", () => {
  const range = previewRange({ source_in: 2, source_out: 5 }, 8);
  assert.deepEqual(range, { start: 2, end: 5 });
  const video = { currentTime: 0, pauses: 0, pause() { this.pauses += 1; } };
  assert.equal(enforcePreviewRange(video, range), false);
  assert.equal(video.currentTime, 2);
  video.currentTime = 6;
  assert.equal(enforcePreviewRange(video, range), true);
  assert.equal(video.pauses, 1);
  assert.ok(video.currentTime < 5 && video.currentTime > 4.9);
  assert.deepEqual(previewRange({ source_in: 0, source_out: null }, 8), { start: 0, end: 8 });
  assert.equal(previewRange({ source_in: 4, source_out: 2 }, 8).end, null);
});

test("draft undo/redo restores IDs and content, editing truncates only the redo branch", () => {
  let state = createDraft([original], "r1", "p1:a1");
  state = draftReducer(state, { type: "edit", value: duplicateShot(state.value, "s1", () => "copy") });
  state = draftReducer(state, { type: "edit", value: (shots) => moveShot(shots, "copy", -1) });
  state = draftReducer(state, { type: "undo" });
  assert.deepEqual(state.value.map((shot) => shot.shot_id), ["s1", "copy"]);
  state = draftReducer(state, { type: "redo" });
  assert.deepEqual(state.value.map((shot) => shot.shot_id), ["copy", "s1"]);
  state = draftReducer(state, { type: "undo" });
  state = draftReducer(state, { type: "edit", value: (shots) => deleteShot(shots, "s1") });
  assert.equal(state.future.length, 0);
  assert.equal(draftDirty(state), true);
});

test("draft sync preserves unsaved base revision and failed saves, scope change resets all history", () => {
  let state = createDraft([original], undefined, "p1:a1");
  const edit = { ...original, notes: "draft" };
  state = draftReducer(state, { type: "edit", value: [edit] });
  state = draftReducer(state, { type: "sync", value: [original], revision: "r1", scope: "p1:a1" });
  assert.equal(state.revision, "r1");
  const before = state;
  state = draftReducer(state, { type: "sync", value: [{ ...original, notes: "remote" }], revision: "r2", scope: "p1:a1" });
  assert.equal(state, before);
  assert.equal(draftReducer(state, { type: "saved", value: [], revision: "old", scope: "other" }), state);
  state = draftReducer(state, { type: "sync", value: [], revision: "other", scope: "p2:a1" });
  assert.deepEqual(state.past, []);
  assert.equal(draftDirty(state), false);
});

test("snapshot history summary strips snapshots and yields the exact base revision", () => {
  const summary = historySummary({ cursor: 0, entries: [{ id: "r1", label: "one", created_at: "now", snapshot: { shots: [original] } }, { id: "r2" }] });
  assert.equal(summary.can_undo, false);
  assert.equal(summary.can_redo, true);
  assert.equal(summary.entries[0].snapshot, undefined);
  assert.equal(historyRevision(summary), "r1");
  assert.equal(historyRevision({ cursor: -1, entries: [] }), "");
  assert.equal(historyRevision(null), undefined);
});
