import { Check, ListPlus, Play, Plus, Trash2 } from "lucide-react";
import { EditorButton } from "./EditorButton.jsx";
import { estimateSubtitleCues } from "../editor/subtitleEditing.js";

export function SubtitleTiming({ edit, timing, onSettings, onPlayCue, hasVoice, disabled }) {
  const subtitle = edit.subtitle;
  const cues = subtitle.cues;
  const change = (patch) => onSettings({ subtitle: patch });
  const update = (index, patch) => change({ cues: cues.map((cue, i) => i === index ? { ...cue, ...patch } : cue) });
  const start = Number(edit.lead_in_seconds);
  const end = timing.voiceDuration ? start + timing.voiceDuration : timing.target - Number(edit.tail_out_seconds);
  const estimated = estimateSubtitleCues(subtitle.text, start, end);
  return <section className="subtitle-timing" aria-label="字幕时间">
    <label className="full-label">字幕时间模式<select value={subtitle.timing_mode} onChange={(event) => change({ timing_mode: event.target.value })}>
      <option value="full">整镜头</option><option value="narration">配音期间</option><option value="cues">逐条定时</option>
    </select></label>
    {subtitle.timing_mode === "cues" ? <>
      <div className="editor-toolbar">
        <EditorButton icon={ListPlus} label="按正文估算分句时间" disabled={disabled || !estimated.length || cues.length > 0} onClick={() => change({ cues: estimated, timing_source: "estimated" })}>估算分句</EditorButton>
        <EditorButton icon={Plus} label="添加定时字幕" disabled={disabled || cues.length >= 100} onClick={() => {
          const next = cues.length ? Number(cues[cues.length - 1].end) : start;
          change({ cues: [...cues, { start: next, end: Math.min(timing.target, next + 1), text: "" }] });
        }} />
        {subtitle.timing_source === "estimated" ? <><span className="editor-warning" role="status">估算时间 · 待校准</span><EditorButton icon={Check} label="标记字幕时间已校准" disabled={disabled} onClick={() => change({ timing_source: "manual" })} /></> : null}
      </div>
      <ol className="subtitle-cues" aria-label="定时字幕列表">
        {cues.map((cue, index) => <li key={index}>
          <div className="editor-field-row">
            <label className="full-label">第 {index + 1} 条开始（秒）<input type="number" min="0" step="0.01" value={cue.start} onChange={(event) => update(index, { start: event.target.value })} /></label>
            <label className="full-label">第 {index + 1} 条结束（秒）<input type="number" min="0" step="0.01" max={timing.target} value={cue.end} onChange={(event) => update(index, { end: event.target.value })} /></label>
          </div>
          <div className="cue-text-row">
            <label className="full-label">第 {index + 1} 条字幕<textarea rows="2" value={cue.text} onChange={(event) => update(index, { text: event.target.value })} /></label>
            <EditorButton icon={Play} label={`试听第 ${index + 1} 条对应配音`} disabled={disabled || !hasVoice || !Number.isFinite(Number(cue.start)) || Number(cue.end) <= Number(cue.start)} onClick={() => onPlayCue(cue)} />
            <EditorButton icon={Trash2} label={`删除第 ${index + 1} 条字幕`} disabled={disabled} onClick={() => change({ cues: cues.filter((_, i) => i !== index) })} />
          </div>
        </li>)}
      </ol>
      {!cues.length ? <p className="muted">无定时字幕</p> : null}
      <p className="muted">时间基准：当前镜头起点</p>
    </> : null}
  </section>;
}
