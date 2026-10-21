import { useRef, useState } from "react";
import { RotateCcw, Save, Upload } from "lucide-react";
import { EditorButton } from "./EditorButton";
import { projectMediaUrl } from "../workflowData";

export const DEFAULT_AUDIO_SETTINGS = {
  normalize_voice: false, target_lufs: -16, voice_gain_db: 0, music_path: "", music_gain_db: -20,
  duck_music: true, loop_music: true, fade_in_seconds: 1, fade_out_seconds: 1.5,
};

export function validAudioSettings(settings) {
  return [["target_lufs", -24, -12], ["voice_gain_db", -24, 12], ["music_gain_db", -48, 0],
    ["fade_in_seconds", 0, 10], ["fade_out_seconds", 0, 10]].every(([key, min, max]) =>
    typeof settings[key] === "number" && Number.isFinite(settings[key]) && settings[key] >= min && settings[key] <= max);
}

function MusicPreview({ url }) {
  const [failed, setFailed] = useState(false);
  return <div className="music-preview">
    <audio controls preload="metadata" src={url} aria-label="背景音乐原音试听" onError={() => setFailed(true)} />
    {failed ? <p className="error-text" role="alert">音乐无法播放，请重新导入或检查文件格式。</p> : null}
  </div>;
}

export function AudioSettings({ settings, musicAssets, backendUrl, projectId, disabled, dirty, conflict,
  saving, message, onChange, onSave, onReset, onUpload }) {
  const input = useRef(null);
  const numeric = (key) => (event) => onChange({ [key]: event.target.value === "" ? "" : Number(event.target.value) });
  const toggle = (key) => (event) => onChange({ [key]: event.target.checked });
  const missingChoice = settings.music_path && !musicAssets.some((item) => item.file_path === settings.music_path);
  return <fieldset className="audio-settings" disabled={disabled || saving}>
    <legend>声音</legend>
    <label className="checkbox-control"><input type="checkbox" aria-label="统一配音音量" checked={settings.normalize_voice} onChange={toggle("normalize_voice")} />统一配音音量</label>
    <div className="audio-settings-grid">
      <label>目标响度（LUFS）<input aria-label="目标响度（LUFS）" type="number" min="-24" max="-12" step="1" value={settings.target_lufs} disabled={!settings.normalize_voice} onChange={numeric("target_lufs")} /></label>
      <label>配音音量（dB）<input aria-label="配音音量（dB）" type="number" min="-24" max="12" step="1" value={settings.voice_gain_db} onChange={numeric("voice_gain_db")} /></label>
    </div>
    <label className="full-label">背景音乐<select aria-label="背景音乐" value={settings.music_path} onChange={(event) => onChange({ music_path: event.target.value })}>
      <option value="">无</option>
      {missingChoice ? <option value={settings.music_path}>{settings.music_path.split(/[\\/]/).pop()}</option> : null}
      {musicAssets.map((item) => <option key={item.file_path} value={item.file_path}>{item.file_path.split(/[\\/]/).pop() || item.name}</option>)}
    </select></label>
    <input className="hidden-input" ref={input} type="file" accept=".wav,.mp3,.m4a,.aac,.flac,.ogg" aria-label="导入音乐文件" onChange={async (event) => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (file) await onUpload(file);
    }} />
    <EditorButton icon={Upload} label="导入背景音乐" disabled={!projectId || !onUpload} onClick={() => input.current?.click()}>导入音乐</EditorButton>
    {settings.music_path ? <>
      <MusicPreview key={settings.music_path} url={projectMediaUrl(backendUrl, projectId, settings.music_path)} />
      <label className="audio-volume">音乐音量 · {settings.music_gain_db} dB<input aria-label="音乐音量（dB）" type="range" min="-48" max="0" step="1" value={settings.music_gain_db} onChange={numeric("music_gain_db")} /></label>
      <div className="action-row">
        <label className="checkbox-control"><input type="checkbox" aria-label="配音时降低音乐" checked={settings.duck_music} onChange={toggle("duck_music")} />配音时降低音乐</label>
        <label className="checkbox-control"><input type="checkbox" aria-label="循环音乐" checked={settings.loop_music} onChange={toggle("loop_music")} />循环音乐</label>
      </div>
      <div className="audio-settings-grid">
        <label>音乐淡入（秒）<input aria-label="音乐淡入（秒）" type="number" min="0" max="10" step="0.1" value={settings.fade_in_seconds} onChange={numeric("fade_in_seconds")} /></label>
        <label>音乐淡出（秒）<input aria-label="音乐淡出（秒）" type="number" min="0" max="10" step="0.1" value={settings.fade_out_seconds} onChange={numeric("fade_out_seconds")} /></label>
      </div>
    </> : null}
    <div className="action-row">
      <EditorButton icon={Save} label="保存声音设置" disabled={!dirty || conflict || !onSave || !validAudioSettings(settings)} onClick={onSave}>保存声音</EditorButton>
      <EditorButton icon={RotateCcw} label="放弃声音修改并重新载入" disabled={!dirty} onClick={onReset} />
      <span className="muted">{dirty ? "声音设置未保存" : "声音设置已保存"}</span>
    </div>
    {conflict ? <p className="error-text" role="alert">声音设置已在其他位置修改，当前草稿已保留。</p> : null}
    {message ? <p role="status">{message}</p> : null}
  </fieldset>;
}
