import { updateShot } from "../workflowData.js";

export const SUBTITLE_COLORS = ["#FFFFFF", "#FFE066", "#7DE2D1", "#FF8FAD", "#000000"];
export const narrationText = (shot) => String(shot?.tts_text || shot?.script_text || "").trim();
export const positiveDuration = (value) => Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : null;
const finite = (value) => value !== "" && value !== null && Number.isFinite(Number(value));
const IMAGE_FILE = /\.(heic|heif|jpe?g|png|webp)$/i;
export const isImageAsset = (asset) => asset?.metadata?.asset_type === "image" || IMAGE_FILE.test(String(asset?.file_path || ""));

export function shotEdit(shot, aspectRatio) {
  const edit = shot?.metadata?.edit || {};
  return {
    source_in: 0, source_out: null, fit: "cover", crop_x: 0.5, crop_y: 0.5,
    shortage: "freeze", duration_mode: "narration", lead_in_seconds: 0, tail_out_seconds: 0, ...edit,
    ...(aspectRatio ? edit.framing?.[aspectRatio] : {}),
    transition: { kind: "cut", duration_seconds: 0.3, ...edit.transition },
    motion: { kind: "push", zoom: 1.08, ...edit.motion },
    subtitle: {
      text: shot?.tts_text || shot?.script_text || "", position: "bottom",
      font_size: 48, color: "#FFFFFF", background: false, timing_mode: "full", cues: [], ...edit.subtitle,
    },
  };
}

// Voice-relative measured cues placed after the shot's lead-in, rounded like the backend.
export function alignedCues(alignment, leadIn) {
  const lead = Number(leadIn) || 0;
  const at = (seconds) => Math.round((lead + Number(seconds)) * 100) / 100;
  return (alignment?.cues || []).map((cue) => ({ start: at(cue.start), end: at(cue.end), text: cue.text }));
}

export function editShotFraming(shots, id, aspectRatio, change) {
  if (!["9:16", "16:9"].includes(aspectRatio)) throw new Error("画幅无效。");
  const shot = shots.find((item) => item.shot_id === id);
  if (!shot) return shots;
  const edit = shotEdit(shot, aspectRatio);
  return editShotSettings(shots, id, { framing: { ...edit.framing,
    [aspectRatio]: { fit: edit.fit, crop_x: edit.crop_x, crop_y: edit.crop_y, ...change } } });
}

export function editShotSettings(shots, id, change) {
  const shot = shots.find((item) => item.shot_id === id);
  if (!shot) return shots;
  const previous = shot.metadata?.edit || {};
  const edit = { ...previous, ...change };
  if (change.subtitle) edit.subtitle = { ...previous.subtitle, ...change.subtitle };
  if (change.transition) edit.transition = { ...previous.transition, ...change.transition };
  if (change.motion) edit.motion = { ...previous.motion, ...change.motion };
  return updateShot(shots, id, { metadata: { edit } });
}

export function replaceShotMaterial(shots, id, materialId) {
  return editShotSettings(updateShot(shots, id, { metadata: { material_asset_id: materialId } }), id, { source_in: 0, source_out: null });
}

export function applySegmentCandidate(shots, id, candidate, confirmed = false) {
  if (candidate.requires_confirmation && !confirmed) throw new Error("请先预览并确认候选片段。");
  if (!candidate.asset_id || !Number.isFinite(candidate.source_in) || !Number.isFinite(candidate.source_out) || candidate.source_in < 0 || candidate.source_out <= candidate.source_in) throw new Error("候选片段范围无效。");
  return editShotSettings(updateShot(shots, id, { metadata: { material_asset_id: candidate.asset_id } }), id,
    { source_in: candidate.source_in, source_out: candidate.source_out });
}

export function moveShot(shots, id, direction) {
  const index = shots.findIndex((shot) => shot.shot_id === id);
  const next = index + direction;
  if (index < 0 || next < 0 || next >= shots.length) return shots;
  const result = [...shots];
  [result[index], result[next]] = [result[next], result[index]];
  return result;
}

export function duplicateShot(shots, id, makeId = () => `shot_${globalThis.crypto.randomUUID()}`) {
  const index = shots.findIndex((shot) => shot.shot_id === id);
  if (index < 0) return shots;
  const source = shots[index];
  const shotId = makeId();
  if (!shotId || shots.some((shot) => shot.shot_id === shotId)) throw new Error("复制镜头 ID 冲突，请重试。");
  const copy = structuredClone(source);
  copy.shot_id = shotId;
  copy.metadata = { ...copy.metadata, duplicate_of: source.shot_id };
  return [...shots.slice(0, index + 1), copy, ...shots.slice(index + 1)];
}

export function deleteShot(shots, id) {
  return shots.length > 1 ? shots.filter((shot) => shot.shot_id !== id) : shots;
}

export function voiceForShot(shot, shots, segments, savedShots = shots) {
  const sourceId = shot.metadata?.duplicate_of;
  const source = shots.find((item) => item.shot_id === sourceId);
  const own = segments.find((segment) => segment.shot_id === shot.shot_id && segment.audio_path);
  const isNew = !savedShots.some((item) => item.shot_id === shot.shot_id);
  const segment = own || (isNew && source && narrationText(source) === narrationText(shot)
    ? segments.find((item) => item.shot_id === sourceId && item.audio_path) : null);
  if (!segment) return null;
  const saved = savedShots.find((item) => item.shot_id === segment.shot_id);
  const originalText = segment.text ?? (saved ? narrationText(saved) : null);
  return originalText !== null && String(originalText).trim() !== narrationText(shot) ? null : segment;
}

export function shotTiming(shot, asset, voice, measured = {}) {
  const edit = shotEdit(shot);
  // A photo has no timeline of its own: it fills the whole shot, so it never runs short.
  const still = isImageAsset(asset);
  const mediaDuration = still ? null : positiveDuration(measured.media) || positiveDuration(asset?.duration_seconds);
  const voiceDuration = voice ? positiveDuration(measured.voice) || positiveDuration(voice.duration_seconds) : null;
  const required = Number(edit.lead_in_seconds) + (voiceDuration || 0) + Number(edit.tail_out_seconds);
  const target = edit.duration_mode === "narration" && voiceDuration ? required : Number(shot.duration_seconds);
  const end = edit.source_out === null ? mediaDuration : Number(edit.source_out);
  const available = end === null ? null : end - Number(edit.source_in);
  return { mediaDuration, voiceDuration, target, required, available, still, shortage: available !== null && available + 0.001 < target };
}

export function shotErrors(shot, timing) {
  const edit = shotEdit(shot);
  const errors = [];
  if (!positiveDuration(shot.duration_seconds)) errors.push("镜头时长必须大于 0。");
  if (!finite(edit.source_in) || Number(edit.source_in) < 0) errors.push("片段起点必须为非负秒数。");
  if (edit.source_out !== null && (!finite(edit.source_out) || Number(edit.source_out) <= Number(edit.source_in))) errors.push("片段终点必须大于起点。");
  if (timing.mediaDuration !== null && (Number(edit.source_in) >= timing.mediaDuration || (edit.source_out !== null && Number(edit.source_out) > timing.mediaDuration + 0.001))) errors.push("选段范围超出素材实际时长。");
  if (!["cover", "contain"].includes(edit.fit)) errors.push("请选择画面填充方式。");
  if ([edit.crop_x, edit.crop_y].some((value) => !finite(value) || Number(value) < 0 || Number(value) > 1)) errors.push("裁剪位置必须在 0 到 1 之间。");
  for (const [ratio, framing] of Object.entries(edit.framing || {})) {
    if (!["9:16", "16:9"].includes(ratio) || !framing || (framing.fit !== undefined && !["cover", "contain"].includes(framing.fit)) ||
      [framing.crop_x, framing.crop_y].some((value) => value !== undefined && (!finite(value) || Number(value) < 0 || Number(value) > 1))) errors.push("分画幅构图无效。");
  }
  if (!["cut", "fade_black"].includes(edit.transition.kind) || !finite(edit.transition.duration_seconds) || Number(edit.transition.duration_seconds) < 0.05 || Number(edit.transition.duration_seconds) > 1) errors.push("转场时长必须在 0.05 到 1 秒之间。");
  if (!["push", "pull", "none"].includes(edit.motion.kind) || !finite(edit.motion.zoom) || Number(edit.motion.zoom) < 1.01 || Number(edit.motion.zoom) > 1.5) errors.push("推拉幅度必须在 1.01 到 1.5 倍之间。");
  if (!["freeze", "error"].includes(edit.shortage)) errors.push("请选择短片处理方式。");
  if (!["narration", "manual"].includes(edit.duration_mode)) errors.push("请选择目标时长模式。");
  if (edit.duration_mode === "manual" && timing.voiceDuration && timing.target + 0.001 < timing.voiceDuration) errors.push("目标视频时长不能短于实际配音。");
  if ([edit.lead_in_seconds, edit.tail_out_seconds].some((v) => !finite(v) || Number(v) < 0 || Number(v) > 10)) errors.push("配音前后留白必须在 0 到 10 秒之间。");
  if (timing.target > 3600 || timing.target + 0.000001 < timing.required || (!timing.voiceDuration && timing.target <= timing.required)) errors.push("镜头时长不足以容纳完整配音和前后留白，或超过一小时。");
  if (!["top", "center", "bottom"].includes(edit.subtitle.position)) errors.push("字幕位置无效。");
  if (!finite(edit.subtitle.font_size) || Number(edit.subtitle.font_size) < 16 || Number(edit.subtitle.font_size) > 96) errors.push("字幕字号必须为 16 到 96。");
  if (!/^#[0-9a-f]{6}$/i.test(edit.subtitle.color)) errors.push("字幕颜色必须为 #RRGGBB。");
  if (typeof edit.subtitle.text !== "string" || typeof edit.subtitle.background !== "boolean") errors.push("字幕格式无效。");
  if (!["full", "narration", "cues"].includes(edit.subtitle.timing_mode)) errors.push("字幕时间模式无效。");
  const cues = edit.subtitle.cues;
  if (!Array.isArray(cues) || cues.length > 100) errors.push("每个镜头最多 100 条定时字幕。");
  else {
    let previousEnd = 0, textLength = 0;
    for (const [index, cue] of cues.entries()) {
      if (!cue || !finite(cue.start) || !finite(cue.end) || Number(cue.start) < previousEnd - 1e-9 || Number(cue.end) - Number(cue.start) < 0.01 - 1e-9 || Number(cue.end) > 3600 || typeof cue.text !== "string" || !cue.text.trim()) errors.push(`字幕 ${index + 1} 的时间或正文无效。`);
      else {
        if (edit.subtitle.timing_mode === "cues" && Number(cue.end) > timing.target + 0.000001) errors.push(`字幕 ${index + 1} 超出镜头总时长。`);
        previousEnd = Number(cue.end);
        textLength += cue.text.length;
      }
    }
    if (textLength > 10000) errors.push("定时字幕总计不能超过一万字。");
  }
  return errors;
}

export function serializeShots(shots) {
  return shots.map((shot) => {
    const result = { ...shot, duration_seconds: Number(shot.duration_seconds) };
    if (!shot.metadata?.edit) return result;
    const edit = { ...shot.metadata.edit };
    for (const key of ["source_in", "source_out", "crop_x", "crop_y", "lead_in_seconds", "tail_out_seconds"]) {
      if (edit[key] !== undefined && edit[key] !== null) edit[key] = Number(edit[key]);
    }
    if (edit.subtitle) {
      edit.subtitle = { ...edit.subtitle };
      if (edit.subtitle.font_size !== undefined) edit.subtitle.font_size = Number(edit.subtitle.font_size);
      if (edit.subtitle.cues) edit.subtitle.cues = edit.subtitle.cues.map((cue) => ({ ...cue, start: Number(cue.start), end: Number(cue.end) }));
    }
    if (edit.framing) edit.framing = Object.fromEntries(Object.entries(edit.framing).map(([ratio, framing]) => [ratio, {
      ...framing, ...Object.fromEntries(["crop_x", "crop_y"].filter((key) => framing[key] !== undefined).map((key) => [key, Number(framing[key])])),
    }]));
    if (edit.transition?.duration_seconds !== undefined) edit.transition = { ...edit.transition, duration_seconds: Number(edit.transition.duration_seconds) };
    if (edit.motion?.zoom !== undefined) edit.motion = { ...edit.motion, zoom: Number(edit.motion.zoom) };
    return { ...result, metadata: { ...shot.metadata, edit } };
  });
}

export function previewRange(edit, duration) {
  const end = positiveDuration(duration);
  const start = Math.min(Math.max(0, Number(edit.source_in) || 0), end || Infinity);
  const out = edit.source_out === null ? end : Math.min(Number(edit.source_out), end || Infinity);
  return { start, end: Number.isFinite(out) && out > start ? out : null };
}

export function enforcePreviewRange(video, range) {
  if (!video || !range.end) return false;
  if (video.currentTime < range.start) video.currentTime = range.start;
  if (video.currentTime >= range.end) {
    video.pause();
    video.currentTime = Math.max(range.start, range.end - 0.025);
    return true;
  }
  return false;
}

export function historyRevision(history) {
  if (!history) return undefined;
  if (typeof history.cursor === "string") return history.entries?.find((entry) => entry.id === history.cursor)?.id || "";
  return history.entries?.[history.cursor]?.id || "";
}

export function historySummary(history) {
  if (!history || !Array.isArray(history.entries)) return null;
  const entries = history.entries.map(({ id, label, created_at }) => ({ id, label, created_at }));
  const cursor = history.cursor ?? -1;
  return { entries, cursor, can_undo: cursor > 0, can_redo: cursor >= 0 && cursor < entries.length - 1 };
}
