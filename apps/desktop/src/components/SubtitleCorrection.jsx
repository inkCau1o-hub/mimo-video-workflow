import { useState } from "react";
import { Replace } from "lucide-react";
import { EditorButton } from "./EditorButton.jsx";
import { applySubtitleCorrection, subtitleCorrections } from "../editor/subtitleEditing.js";

export function SubtitleCorrection({ shots, disabled, onChange }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const matches = subtitleCorrections(shots, from, to);
  return <details className="subtitle-correction">
    <summary>字幕批量纠词</summary>
    <fieldset disabled={disabled}>
      <legend className="sr-only">字幕纠词</legend>
      <div className="editor-field-row">
        <label className="full-label">原词<input maxLength="100" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
        <label className="full-label">正确词 / 品牌词<input maxLength="100" value={to} onChange={(event) => setTo(event.target.value)} /></label>
      </div>
      <p className="muted" role="status">{matches.length} 个镜头 · {matches.reduce((sum, item) => sum + item.count, 0)} 处字幕 · 配音正文不变</p>
      {matches.length ? <ul className="correction-preview" aria-label="纠词预览">{matches.map((item) => <li key={item.shotId}><strong>{item.shotId}</strong><del>{item.before}</del><ins>{item.after}</ins></li>)}</ul> : null}
      <EditorButton icon={Replace} label="将字幕纠词应用到草稿" disabled={disabled || !matches.length} onClick={() => onChange((value) => applySubtitleCorrection(value, from, to))}>应用到草稿</EditorButton>
    </fieldset>
  </details>;
}
