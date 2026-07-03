import { useMemo, useState } from "react";
import { pickList, pickStoryboardAnalysis } from "../workflowData";

function toFileUrl(path) {
  if (!path) return "";
  if (/^https?:\/\//.test(path) || path.startsWith("file://")) return path;
  return `file://${path}`;
}

export function VoiceoverPage({ activity, project, onGenerateVoiceovers }) {
  const [voice, setVoice] = useState("");
  const [audioFormat, setAudioFormat] = useState("mp3");
  const [stylePrompt, setStylePrompt] = useState("自然、清晰、适合短视频口播");
  const analysis = pickStoryboardAnalysis(activity, project);
  const shots = analysis?.shots || [];
  const segments = pickList(activity, project, "voiceover_segments");
  const canGenerate = Boolean(project && shots.length);
  const segmentByShot = useMemo(() => {
    const result = new Map();
    for (const segment of segments) {
      result.set(segment.shot_id, segment);
    }
    return result;
  }, [segments]);

  const submit = () => {
    onGenerateVoiceovers({
      voice: voice.trim(),
      audio_format: audioFormat,
      style_prompt: stylePrompt.trim(),
    });
  };

  return (
    <div className="page-grid two-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>配音</h2>
            <p>按分镜的 tts_text 生成配音片段。</p>
          </div>
          <button type="button" className="primary" onClick={submit} disabled={!canGenerate}>
            生成配音
          </button>
        </div>
        <div className="field-row">
          <label>
            声音
            <input value={voice} onChange={(event) => setVoice(event.target.value)} placeholder="默认声音" />
          </label>
          <label>
            音频格式
            <select value={audioFormat} onChange={(event) => setAudioFormat(event.target.value)}>
              <option value="mp3">mp3</option>
              <option value="wav">wav</option>
              <option value="m4a">m4a</option>
            </select>
          </label>
        </div>
        <label className="full-label">
          配音风格
          <input value={stylePrompt} onChange={(event) => setStylePrompt(event.target.value)} />
        </label>
        <div className="source-preview">
          <div className="queue-header">
            <strong>当前分镜旁白</strong>
            <span>{shots.length} 条</span>
          </div>
          {shots.length === 0 ? (
            <p>请先在分镜分析页生成分镜。</p>
          ) : (
            <div className="shot-list compact-list">
              {shots.map((shot) => (
                <article className="shot-row" key={shot.shot_id}>
                  <strong>{shot.shot_id} · {shot.title}</strong>
                  <p>{shot.tts_text || shot.script_text}</p>
                  <span>
                    {shot.duration_seconds}s
                    {segmentByShot.has(shot.shot_id) ? " · 已配音" : " · 未配音"}
                  </span>
                </article>
              ))}
            </div>
          )}
        </div>
      </section>
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>配音结果</h2>
            <p>{segments.length} 条音频片段</p>
          </div>
        </div>
        <div className="asset-list">
          {segments.length === 0 ? (
            <div className="empty-state">等待生成配音。</div>
          ) : (
            segments.map((segment) => (
              <article className="asset-row" key={segment.segment_id}>
                <strong>{segment.shot_id}{segment.segment_id ? ` · ${segment.segment_id}` : ""}</strong>
                <span>{segment.audio_path}</span>
                <p>{segment.text}</p>
                {segment.audio_path ? (
                  <audio controls src={toFileUrl(segment.audio_path)}>
                    <track kind="captions" />
                  </audio>
                ) : null}
              </article>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
