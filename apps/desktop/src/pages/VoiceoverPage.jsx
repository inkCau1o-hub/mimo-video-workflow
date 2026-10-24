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

// Pauses inside a voiceover (from its alignment) that are longer than the limit, with the backend's 10 ms tolerance.
const longPauses = (segment, limit) => (segment?.metadata?.subtitle_alignment?.pauses || []).filter((pause) => pause.seconds > limit + 0.01);

// What the last shortening did, read from its job result while the voiceovers are still the ones it left
// (after an undo, a regeneration or a stricter limit the old result no longer describes them).
function shortenSummary(job, segments, flagged) {
  if (job?.job_type !== "voiceovers.pauses" || job.status !== "succeeded" || !job.result) return "";
  const shortened = job.result.shortened || [];
  if (!shortened.length) return flagged ? "" : "没有需要压短的停顿。";
  const current = new Map(segments.map((segment) => [segment.shot_id, segment.audio_path]));
  if (shortened.some((item) => current.get(item.shot_id) !== item.audio_path)) return "";
  const saved = shortened.reduce((sum, item) => sum + Number(item.saved_seconds || 0), 0);
  const kept = (job.result.alignment || []).filter((item) => !item.applied).length;
  return `已压短 ${shortened.length} 段配音，共缩短 ${saved.toFixed(2)} 秒。${kept ? `${kept} 个镜头的字幕不是自动生成的，没有跟着更新，请在编辑器里检查。` : ""}`;
}

export function VoiceoverPage({ activity, backendUrl, project, job, onGenerateVoiceovers, onRegenerateVoiceover, onAlignVoiceovers, onShortenPauses }) {
  const [voice, setVoice] = useState("");
  const [audioFormat, setAudioFormat] = useState("mp3");
  const [stylePrompt, setStylePrompt] = useState("自然、清晰、适合短视频口播");
  const [regeneratingShotId, setRegeneratingShotId] = useState("");
  const [pauseLimit, setPauseLimit] = useState(() => Number(activity?.metadata?.pause_limit_seconds ?? 0.5));
  const analysis = pickStoryboardAnalysis(activity, project);
  const shots = analysis?.shots || [];
  const segments = pickList(activity, project, "voiceover_segments");
  const isRunning = Boolean(job && !isTerminalJob(job));
  const isBusy = isRunning || Boolean(regeneratingShotId);
  const canGenerate = Boolean(project?.project_id && shots.length && onGenerateVoiceovers && !isBusy);
  const canRegenerate = Boolean(project?.project_id && onRegenerateVoiceover && !isBusy);
  const canAlign = Boolean(project?.project_id && onAlignVoiceovers && segments.some((segment) => segment.audio_path) && !isBusy);
  const limit = Number(pauseLimit);
  const validLimit = Number.isFinite(limit) && limit >= 0.1 && limit <= 3;
  const flagged = validLimit ? segments.reduce((count, segment) => count + longPauses(segment, limit).length, 0) : 0;
  const measured = segments.some((segment) => segment.metadata?.subtitle_alignment);
  const canShorten = Boolean(project?.project_id && onShortenPauses && flagged > 0 && !isBusy);
  const summary = shortenSummary(job, segments, flagged);
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
        <div className="action-row">
          <label className="full-label">句内停顿上限（秒）
            <input aria-label="句内停顿上限（秒）" type="number" min="0.1" max="3" step="0.1" value={pauseLimit} disabled={isBusy}
              onChange={(event) => setPauseLimit(event.target.value)} />
          </label>
          <button type="button" onClick={() => canShorten && onShortenPauses({ pause_limit_seconds: limit })} disabled={!canShorten}
            title="超过上限的停顿压到上限值，原配音文件保留，可撤销">压短超长停顿</button>
        </div>
        {flagged ? <p className="editor-warning" role="status">{`${flagged} 处句内停顿超过 ${limit} 秒`}</p>
          : segments.length > 0 && !measured ? <p className="muted">先点“按配音对齐字幕”检测句内停顿。</p> : null}
        {summary ? <p className="muted" role="status">{summary}</p> : null}
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
                {validLimit ? longPauses(segment, limit).map((pause) => (
                  <p className="editor-warning" key={`${pause.start}-${pause.end}`}>
                    {`句内停顿 ${pause.seconds.toFixed(2)} 秒${pause.before || pause.after ? `（${pause.before}｜${pause.after}）` : ""}`}
                  </p>
                )) : null}
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
