import { editShotSettings, shotEdit } from "./shotEditing.js";

export function subtitleAtTime(edit, seconds, timing = {}) {
  const subtitle = edit.subtitle || {};
  if (seconds < 0 || seconds >= (timing.target ?? Infinity)) return "";
  if (subtitle.timing_mode === "cues") {
    return (subtitle.cues || []).find((cue) => seconds >= Number(cue.start) && seconds < Number(cue.end))?.text || "";
  }
  if (subtitle.timing_mode === "narration") {
    const start = Number(edit.lead_in_seconds || 0);
    const end = timing.voiceDuration ? start + timing.voiceDuration : (timing.target ?? Infinity) - Number(edit.tail_out_seconds || 0);
    if (seconds < start || seconds >= end) return "";
  }
  return subtitle.text || "";
}

export function estimateSubtitleCues(text, start, end) {
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start || !text.trim()) return [];
  const chunks = (text.match(/[^。！？!?；;\n]+[。！？!?；;\n]*/gu) || [text]).map((part) => part.trim()).filter(Boolean);
  if (chunks.length > 100) chunks.splice(99, chunks.length - 99, chunks.slice(99).join(""));
  const first = Math.round(start * 100), last = Math.floor(end * 100);
  if (last - first < chunks.length) return [];
  const total = chunks.reduce((sum, part) => sum + Array.from(part).length, 0);
  let cursor = first, weight = 0;
  return chunks.map((part, index) => {
    weight += Array.from(part).length;
    const stop = index === chunks.length - 1 ? last : Math.min(last - (chunks.length - index - 1), Math.max(cursor + 1, Math.round(first + (last - first) * weight / total)));
    const cue = { start: cursor / 100, end: stop / 100, text: part };
    cursor = stop;
    return cue;
  });
}

export function subtitleCorrections(shots, from, to) {
  if (!from.trim() || !to.trim() || from === to || from.length > 100 || to.length > 100) return [];
  return shots.flatMap((shot) => {
    const subtitle = shotEdit(shot).subtitle;
    const values = subtitle.timing_mode === "cues" ? subtitle.cues.map((cue) => cue.text) : [subtitle.text];
    const count = values.reduce((sum, value) => sum + value.split(from).length - 1, 0);
    if (!count) return [];
    return [{ shotId: shot.shot_id, count, before: values.join("\n"), after: values.map((v) => v.split(from).join(to)).join("\n") }];
  });
}

export function applySubtitleCorrection(shots, from, to) {
  const matches = new Set(subtitleCorrections(shots, from, to).map((item) => item.shotId));
  return shots.map((shot) => {
    if (!matches.has(shot.shot_id)) return shot;
    const subtitle = shotEdit(shot).subtitle;
    const patch = subtitle.timing_mode === "cues"
      ? { cues: subtitle.cues.map((cue) => ({ ...cue, text: cue.text.split(from).join(to) })) }
      : { text: subtitle.text.split(from).join(to) };
    return editShotSettings([shot], shot.shot_id, { subtitle: patch })[0];
  });
}
