import { useEffect, useState } from "react";
import { renderResult } from "../workflowData";
import { VideoPreview } from "./VideoPreview";
import { CoverEditor } from "./CoverEditor.jsx";

export function RenderedVideo({ activity, project, backendUrl, job, onGenerateCover, coverJob, onCancelJob, busy }) {
  const current = renderResult(activity, project, job);
  const [historyPath, setHistoryPath] = useState("");
  useEffect(() => { setHistoryPath(""); }, [current?.output_path]);
  const history = (activity?.metadata?.exports || []).filter((item) => item.output_path && item.output_path !== current?.output_path).slice().reverse();
  const result = historyPath ? history.find((item) => item.output_path === historyPath) : current;
  return (
    <div className="rendered-video">
      {history.length ? <label className="full-label">导出版本
        <select disabled={busy} value={historyPath} onChange={(event) => setHistoryPath(event.target.value)}>
          <option value="">当前内容</option>
          {history.map((item, index) => <option value={item.output_path} key={`${item.output_path}:${index}`}>历史 {history.length - index} · {String(item.output_path).split(/[\\/]/).pop()}</option>)}
        </select>
      </label> : null}
      {result?.output_path ? <>
        <strong>{historyPath ? "历史导出（可能与当前内容不同）" : "当前内容已导出"}</strong>
        <span className="project-path">{result.output_path}</span>
        <span className="muted">{[result.width && result.height ? `${result.width} × ${result.height}` : "", result.duration_seconds ? `${Number(result.duration_seconds).toFixed(1)} 秒` : "", result.segment_count ? `${result.segment_count} 个片段` : ""].filter(Boolean).join(" · ")}</span>
        {result.audio_settings ? <p className="muted">配音：{result.audio_settings.normalize_voice ? `响度目标 ${result.audio_settings.target_lufs} LUFS` : "原始响度"} · {result.audio_settings.voice_gain_db} dB<br />
          音乐：{result.audio_settings.music_path ? `${String(result.audio_settings.music_path).split(/[\\/]/).pop()} · ${result.audio_settings.music_gain_db} dB${result.audio_settings.duck_music ? " · 配音时降低" : ""}` : "无"}</p> : null}
        <VideoPreview backendUrl={backendUrl} projectId={project?.project_id} path={result.output_path}
          version={result.created_at || result.rendered_at || result.generated_at} downloadable />
        {result.warnings?.length ? <div className="warning-list">{result.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div> : null}
        {(onGenerateCover || activity?.metadata?.covers?.length) ? <CoverEditor key={`${project?.project_id}:${activity?.activity_id}:${result.output_path}`}
          result={result} activity={activity} project={project} backendUrl={backendUrl} onGenerateCover={onGenerateCover} job={coverJob} onCancelJob={onCancelJob} busy={busy} /> : null}
      </> : <div className="empty-state">当前内容尚无已导出视频。</div>}
    </div>
  );
}
