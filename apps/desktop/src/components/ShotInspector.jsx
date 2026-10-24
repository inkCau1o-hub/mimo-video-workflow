import { SUBTITLE_COLORS, positiveDuration, shotEdit } from "../editor/shotEditing.js";
import { projectMediaUrl } from "../workflowData.js";
import { ShotPreview } from "./ShotPreview.jsx";
import { useRef, useState } from "react";
import { SubtitleTiming } from "./SubtitleTiming.jsx";
import { ArrowUpLeft, ArrowUp, ArrowUpRight, ArrowLeft, Focus, ArrowRight, ArrowDownLeft, ArrowDown, ArrowDownRight, Copy, RotateCcw } from "lucide-react";
import { EditorButton } from "./EditorButton.jsx";

const seconds = (value) => Number.isFinite(value) ? `${value.toFixed(2)} s` : "待测量";

export function ShotInspector({ shot, asset, assets, voice, timing, errors, backendUrl, projectId, aspectRatio, onAspectRatio, onEdit, onSettings, onFraming, hasNext = true, onMaterial, onMediaDuration, onVoiceDuration, disabled }) {
  const edit = shotEdit(shot, aspectRatio);
  const frame = (patch, ratio = aspectRatio) => onFraming?.(ratio, patch);
  const audioRef = useRef(null);
  const previewEnd = useRef(null);
  const [previewError, setPreviewError] = useState("");
  const playCue = async (cue) => {
    const audio = audioRef.current;
    const start = Math.max(0, Number(cue.start) - Number(edit.lead_in_seconds));
    const end = Math.min(timing.voiceDuration, Number(cue.end) - Number(edit.lead_in_seconds));
    if (!audio || !Number.isFinite(start) || !Number.isFinite(end) || end <= start) { setPreviewError("该字幕时段没有配音。"); return; }
    try { audio.currentTime = start; previewEnd.current = end; await audio.play(); setPreviewError(""); }
    catch { previewEnd.current = null; setPreviewError("配音试听失败，请检查音频文件。"); }
  };
  return <fieldset className="shot-inspector" disabled={disabled}>
    <legend className="sr-only">镜头参数</legend>
    <div className="shot-visual-column">
      <label className="full-label">预览画幅
        <select value={aspectRatio} onChange={(event) => onAspectRatio(event.target.value)}><option value="9:16">9:16</option><option value="16:9">16:9</option></select>
      </label>
      <ShotPreview key={`${projectId}:${shot.shot_id}:${asset?.asset_id || "auto"}:${asset?.file_path || ""}`} backendUrl={backendUrl} projectId={projectId} asset={asset} edit={edit} aspectRatio={aspectRatio} onDuration={onMediaDuration} timing={timing} />
      <dl className="shot-timing">
        <div><dt>素材实际时长</dt><dd>{seconds(timing.mediaDuration)}</dd></div>
        <div><dt>当前选段</dt><dd>{seconds(timing.available)}</dd></div>
        <div><dt>实际配音</dt><dd>{voice ? `${seconds(timing.voiceDuration)}${voice.shot_id !== shot.shot_id ? " · 待复用" : ""}` : "未配音 / 待生成"}</dd></div>
        <div><dt>目标视频时长</dt><dd>{seconds(timing.target)}</dd></div>
      </dl>
      {voice ? <audio ref={audioRef} key={voice.audio_path} aria-label="当前镜头配音" controls preload="metadata"
        src={projectMediaUrl(backendUrl, projectId, voice.audio_path, voice.metadata?.generated_at)}
        onTimeUpdate={(event) => { if (previewEnd.current !== null && event.currentTarget.currentTime >= previewEnd.current) { event.currentTarget.pause(); previewEnd.current = null; } }}
        onPause={() => { previewEnd.current = null; }}
        onLoadedMetadata={(event) => { if (positiveDuration(event.currentTarget.duration)) onVoiceDuration(event.currentTarget.duration); }} /> : null}
      {previewError ? <p className="error-text" role="alert">{previewError}</p> : null}
      {timing.shortage ? <p className={edit.shortage === "error" ? "error-text" : "editor-warning"} role="status">
        {edit.shortage === "freeze" ? `选段不足，导出时补末帧 ${(timing.target - timing.available).toFixed(2)} s；配音完整保留。` : "选段不足：当前选择将拒绝导出，请延长选段或选择补末帧。"}
      </p> : null}
      {timing.still ? <p className="muted">图片素材：按镜头时长显示，并做推拉。</p>
        : !timing.mediaDuration ? <p className="muted">{asset ? "素材时长待校验。" : "自动匹配后需校验素材与选段时长。"}</p> : null}
      {errors.length ? <ul className="editor-errors" role="alert">{errors.map((error) => <li key={error}>{error}</li>)}</ul> : null}
    </div>
    <div className="shot-parameter-column">
      <label className="full-label">镜头素材
        <select value={shot.metadata?.material_asset_id || ""} onChange={(event) => onMaterial(event.target.value)}>
          <option value="">自动匹配</option>
          {shot.metadata?.material_asset_id && !asset ? <option value={shot.metadata.material_asset_id}>素材不可用：{shot.metadata.material_asset_id}</option> : null}
          {assets.filter((item) => item.asset_id).map((item) => <option key={item.asset_id} value={item.asset_id}>{String(item.file_path).split(/[\\/]/).pop()} · {item.asset_id}</option>)}
        </select>
      </label>
      <div className="editor-field-row">
        <label className="full-label">片段起点（秒）<input type="number" min="0" step="0.01" max={timing.mediaDuration ?? undefined} value={edit.source_in} onChange={(event) => onSettings({ source_in: event.target.value })} /></label>
        <label className="full-label">片段终点（秒）<input type="number" min="0" step="0.01" max={timing.mediaDuration ?? undefined} placeholder="素材结尾" value={edit.source_out ?? ""} onChange={(event) => onSettings({ source_out: event.target.value === "" ? null : event.target.value })} /></label>
      </div>
      <div className="editor-field-row">
        <label className="full-label">目标时长模式<select value={edit.duration_mode} onChange={(event) => onSettings({ duration_mode: event.target.value })}><option value="narration">实际配音优先</option><option value="manual">手动时长</option></select></label>
        <label className="full-label">镜头时长（秒）<input type="number" min="0.1" step="0.1" value={shot.duration_seconds ?? ""} onChange={(event) => onEdit({ duration_seconds: event.target.value })} /></label>
      </div>
      <label className="full-label">选段不足时<select value={edit.shortage} onChange={(event) => onSettings({ shortage: event.target.value })}><option value="freeze">补末帧（freeze）</option><option value="error">拒绝导出（error）</option></select></label>
      <div className="editor-field-row">
        <label className="full-label">配音前留白（秒）<input type="number" min="0" max="10" step="0.01" value={edit.lead_in_seconds} onChange={(event) => onSettings({ lead_in_seconds: event.target.value })} /></label>
        <label className="full-label">配音后留白（秒）<input type="number" min="0" max="10" step="0.01" value={edit.tail_out_seconds} onChange={(event) => onSettings({ tail_out_seconds: event.target.value })} /></label>
      </div>
      <fieldset className="editor-fit"><legend>画面裁剪 · {aspectRatio}</legend><div className="segmented-control">
        {[['cover', '铺满裁剪'], ['contain', '完整留边']].map(([value, label]) => <label key={value} className={edit.fit === value ? "selected" : ""}><input type="radio" name={`fit-${shot.shot_id}`} value={value} checked={edit.fit === value} onChange={() => frame({ fit: value })} /> {label}</label>)}
      </div></fieldset>
      <div className="framing-tools">
        <div className="framing-presets" role="group" aria-label="构图位置">
          {[ArrowUpLeft, ArrowUp, ArrowUpRight, ArrowLeft, Focus, ArrowRight, ArrowDownLeft, ArrowDown, ArrowDownRight].map((Icon, index) => {
            const x = (index % 3) / 2, y = Math.floor(index / 3) / 2;
            return <EditorButton key={index} icon={Icon} label={`构图${["左上", "上方", "右上", "左侧", "居中", "右侧", "左下", "下方", "右下"][index]}`}
              aria-pressed={Number(edit.crop_x) === x && Number(edit.crop_y) === y} disabled={disabled || edit.fit === "contain"} onClick={() => frame({ crop_x: x, crop_y: y })} />;
          })}
        </div>
        <div className="action-row">
          <EditorButton icon={Copy} label={`复制构图到 ${aspectRatio === "9:16" ? "16:9" : "9:16"}`} onClick={() => frame({ fit: edit.fit, crop_x: edit.crop_x, crop_y: edit.crop_y }, aspectRatio === "9:16" ? "16:9" : "9:16")} />
          <EditorButton icon={RotateCcw} label="重置当前画幅构图" onClick={() => frame({ fit: "cover", crop_x: 0.5, crop_y: 0.5 })} />
        </div>
      </div>
      <div className="editor-field-row">
        <label className="full-label">水平 X · {Math.round(Number(edit.crop_x) * 100)}%<input type="range" min="0" max="1" step="0.01" value={edit.crop_x} disabled={edit.fit === "contain"} onChange={(event) => frame({ crop_x: Number(event.target.value) })} /></label>
        <label className="full-label">垂直 Y · {Math.round(Number(edit.crop_y) * 100)}%<input type="range" min="0" max="1" step="0.01" value={edit.crop_y} disabled={edit.fit === "contain"} onChange={(event) => frame({ crop_y: Number(event.target.value) })} /></label>
      </div>
      <div className="editor-field-row">
        <label className="full-label">到下一镜头<select disabled={!hasNext} value={edit.transition.kind} onChange={(event) => onSettings({ transition: { kind: event.target.value } })}><option value="cut">直接切换</option><option value="fade_black">淡黑转场</option></select></label>
        <label className="full-label">单侧淡化（秒）<input type="number" min="0.05" max="1" step="0.05" value={edit.transition.duration_seconds} disabled={!hasNext || edit.transition.kind === "cut"} onChange={(event) => onSettings({ transition: { duration_seconds: event.target.value } })} /></label>
      </div>
      {timing.still ? <div className="editor-field-row">
        <label className="full-label">图片运动<select value={edit.motion.kind} onChange={(event) => onSettings({ motion: { kind: event.target.value } })}><option value="push">推近</option><option value="pull">拉远</option><option value="none">静止</option></select></label>
        <label className="full-label">推拉幅度（倍）<input type="number" min="1.01" max="1.5" step="0.01" value={edit.motion.zoom} disabled={edit.motion.kind === "none"} onChange={(event) => onSettings({ motion: { zoom: event.target.value } })} /></label>
      </div> : null}
      <label className="full-label">{edit.subtitle.timing_mode === "cues" ? "分句底稿" : "字幕正文"}<textarea className="mini-textarea" value={edit.subtitle.text} onChange={(event) => onSettings({ subtitle: { text: event.target.value } })} /></label>
      <SubtitleTiming edit={edit} timing={timing} onSettings={onSettings} onPlayCue={playCue} hasVoice={Boolean(voice)} disabled={disabled}
        alignment={voice?.metadata?.subtitle_alignment} />
      <div className="editor-field-row">
        <label className="full-label">字幕位置<select value={edit.subtitle.position} onChange={(event) => onSettings({ subtitle: { position: event.target.value } })}><option value="top">顶部</option><option value="center">居中</option><option value="bottom">底部</option></select></label>
        <label className="full-label">字号（720p）<input type="number" min="16" max="96" value={edit.subtitle.font_size} onChange={(event) => onSettings({ subtitle: { font_size: event.target.value } })} /></label>
      </div>
      <div className="subtitle-color-row">
        <div className="color-swatches" role="group" aria-label="字幕颜色">
          {SUBTITLE_COLORS.map((color) => <button key={color} type="button" className="color-swatch" title={`字幕颜色 ${color}`} aria-label={`字幕颜色 ${color}`} aria-pressed={edit.subtitle.color.toUpperCase() === color} style={{ backgroundColor: color }} onClick={() => onSettings({ subtitle: { color } })} />)}
          <label className="custom-color" title="自定义字幕颜色"><span className="sr-only">自定义字幕颜色</span><input type="color" value={/^#[0-9a-f]{6}$/i.test(edit.subtitle.color) ? edit.subtitle.color : "#FFFFFF"} onChange={(event) => onSettings({ subtitle: { color: event.target.value.toUpperCase() } })} /></label>
        </div>
        <label className="checkbox-control"><input type="checkbox" checked={edit.subtitle.background} onChange={(event) => onSettings({ subtitle: { background: event.target.checked } })} />字幕背景</label>
      </div>
      <details className="shot-narration"><summary>脚本与配音正文</summary>
        <label className="full-label">脚本文本<textarea className="mini-textarea" value={shot.script_text || ""} onChange={(event) => onEdit({ script_text: event.target.value })} /></label>
        <label className="full-label">配音文本<textarea className="mini-textarea" value={shot.tts_text || ""} onChange={(event) => onEdit({ tts_text: event.target.value })} /></label>
      </details>
    </div>
  </fieldset>;
}
