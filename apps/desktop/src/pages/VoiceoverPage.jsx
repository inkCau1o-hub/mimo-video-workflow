import { useMemo, useState } from "react";
import { pickList, pickStoryboardAnalysis } from "../workflowData";
import { isTerminalJob } from "../api/jobMonitor";
import { JobProgress } from "../components/JobProgress";

function toProjectMediaUrl(backendUrl, projectId, segment) {
  const path = segment?.audio_path || "";
  if (!path) return "";
  if (/^https?:\/\//.test(path)) return path;
  if (!backendUrl || !projectId) return "";
  const baseUrl = backendUrl.replace(/\/$/, "");
  const version = segment?.metadata?.generated_at || "";
  const versionQuery = version ? `&v=${encodeURIComponent(version)}` : "";
  return `${baseUrl}/projects/${encodeURIComponent(projectId)}/media?path=${encodeURIComponent(path)}${versionQuery}`;
}

export function VoiceoverPage({ activity, backendUrl, project, job, onGenerateVoiceovers, onRegenerateVoiceover, onAlignVoiceovers }) {
  const [voice, setVoice] = useState("");
  const [audioFormat, setAudioFormat] = useState("mp3");
  const [stylePrompt, setStylePrompt] = useState("自然、清晰、适合短视频口播");
  const [regeneratingShotId, setRegeneratingShotId] = useState("");
  const analysis = pickStoryboardAnalysis(activity, project);
  const shots = analysis?.shots || [];
  const segments = pickList(activity, project, "voiceover_segments");
  const isRunning = Boolean(job && !isTerminalJob(job));
  const isBusy = isRunning || Boolean(regeneratingShotId);
  const canGenerate = Boolean(project?.project_id && shots.length && onGenerateVoiceovers && !isBusy);
  const canRegenerate = Boolean(project?.project_id && onRegenerateVoiceover && !isBusy);
  const canAlign = Boolean(project?.project_id && onAlignVoiceovers && segments.some((segment) => segment.audio_path) && !isBusy);
  const settings = { voice: voice.trim(), audio_format: audioFormat, style_prompt: stylePrompt.trim() };
  const segmentByShot = useMemo(() => {
    const result = new Map();
    for (const segment of segments) {
      result.set(segment.shot_id, segment);
    }
    return result;
  }, [segments]);

  const submit = () => {
    if (canGenerate) onGenerateVoiceovers(settings);
  };

  const regenerateShot = async (shot) => {
    if (!shot?.shot_id || !canRegenerate) return;
    setRegeneratingShotId(shot.shot_id);
    try {
      await onRegenerateVoiceover({
        shot_id: shot.shot_id,
        ...settings,
      });
    } finally {
      setRegeneratingShotId("");
    }
  };

  return (
    <div className="page-grid two-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>配音</h2>
            <p>按分镜的 tts_text 生成配音片段。</p>
          </div>
          <div className="action-row">
            <button type="button" onClick={() => canAlign && onAlignVoiceovers()} disabled={!canAlign}
              title="用现有配音取逐字时间并更新自动字幕，配音本身不变">按配音对齐字幕</button>
            <button type="button" className="primary" onClick={submit} disabled={!canGenerate}>
              生成配音
            </button>
          </div>
        </div>
        <div className="field-row">
          <label>
            声音
            <input value={voice} onChange={(event) => setVoice(event.target.value)} placeholder="默认声音" disabled={isBusy} />
          </label>
          <label>
            音频格式
            <select value={audioFormat} onChange={(event) => setAudioFormat(event.target.value)} disabled={isBusy}>
              <option value="mp3">mp3</option>
              <option value="wav">wav</option>
            </select>
          </label>
        </div>
        <label className="full-label">
          配音风格
          <input value={stylePrompt} onChange={(event) => setStylePrompt(event.target.value)} disabled={isBusy} />
        </label>
        <JobProgress job={job} title="配音进度" />
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
                  <div className="asset-row-toolbar">
                    <strong>{shot.shot_id} · {shot.title}</strong>
                    {!segmentByShot.get(shot.shot_id)?.audio_path ? (
                      <button type="button" onClick={() => regenerateShot(shot)} disabled={!canRegenerate || !shot.shot_id}>
                        {regeneratingShotId === shot.shot_id ? "生成中..." : "生成此句"}
                      </button>
                    ) : null}
                  </div>
                  <p>{shot.tts_text || shot.script_text}</p>
                  <span>
                    {shot.duration_seconds}s
                    {segmentByShot.get(shot.shot_id)?.audio_path ? " · 已配音" : " · 未配音"}
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
                <div className="asset-row-toolbar">
                  <strong>{segment.shot_id}{segment.segment_id ? ` · ${segment.segment_id}` : ""}</strong>
                  <button
                    type="button"
                    onClick={() => regenerateShot(segment)}
                    disabled={!canRegenerate || !segment.shot_id}
                  >
                    {regeneratingShotId === segment.shot_id ? "生成中..." : "重新生成"}
                  </button>
                </div>
                <span>{segment.audio_path}</span>
                <p>{segment.text}</p>
                {segment.audio_path && project?.project_id ? (
                  <audio controls preload="metadata" src={toProjectMediaUrl(backendUrl, project.project_id, segment)}>
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
