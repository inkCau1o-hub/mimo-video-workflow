import { useRef, useState } from "react";
import { Camera, Crosshair, Download } from "lucide-react";
import { projectMediaUrl } from "../workflowData.js";
import { EditorButton } from "./EditorButton.jsx";
import { JobProgress } from "./JobProgress.jsx";

export const DEFAULT_COVER = { time_seconds: 0, title: "", position: "top", font_size: 64, color: "#FFFFFF", background: true };
export function coverErrors(settings, duration) {
  const errors = [];
  if (settings.time_seconds === "" || !Number.isFinite(Number(settings.time_seconds)) || Number(settings.time_seconds) < 0 || Number(settings.time_seconds) >= duration) errors.push("选帧时间必须在成片范围内，且早于结尾。");
  if (typeof settings.title !== "string" || settings.title.length > 80 || settings.title.split(/\r?\n/).length > 3) errors.push("封面标题最多 80 字、3 行。");
  if (settings.font_size === "" || !Number.isFinite(Number(settings.font_size)) || Number(settings.font_size) < 16 || Number(settings.font_size) > 96) errors.push("封面字号必须为 16 到 96。");
  if (!["top", "center", "bottom"].includes(settings.position) || !/^#[0-9a-f]{6}$/i.test(settings.color) || typeof settings.background !== "boolean") errors.push("封面样式无效。");
  return errors;
}

export function CoverEditor({ result, activity, project, backendUrl, onGenerateCover, job, onCancelJob, busy = false }) {
  const covers = (activity?.metadata?.covers || []).filter((item) => item.source_video_path === result.output_path).slice().reverse();
  const [settings, setSettings] = useState(() => ({ ...DEFAULT_COVER, ...covers[0]?.settings }));
  const [selectedCover, setSelectedCover] = useState("");
  const [duration, setDuration] = useState(Number(result.duration_seconds) || 0);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const video = useRef(null), inFlight = useRef(false);
  const cover = covers.find((item) => item.image_path === selectedCover) || covers[0];
  const errors = coverErrors(settings, duration);
  const disabled = busy || submitting;
  const patch = (value) => { setSettings((previous) => ({ ...previous, ...value })); setMessage(""); };
  const seek = (value) => {
    patch({ time_seconds: value });
    if (video.current && value !== "" && Number.isFinite(Number(value)) && Number(value) >= 0 && Number(value) < duration) {
      video.current.pause(); video.current.currentTime = Number(value);
    }
  };
  const generate = async () => {
    if (disabled || inFlight.current || errors.length || !onGenerateCover) return;
    inFlight.current = true; setSubmitting(true); setMessage("");
    try {
      const submitted = await onGenerateCover({ source_video_path: result.output_path,
        settings: { ...settings, time_seconds: Number(settings.time_seconds), font_size: Number(settings.font_size) } },
      { expectedScope: { projectId: project.project_id, activityId: activity.activity_id } });
      if (!submitted || submitted.status === "failed") setMessage("封面生成未完成，设置已保留。");
      else setSelectedCover("");
    } catch { setMessage("封面生成失败，设置已保留。"); }
    finally { inFlight.current = false; setSubmitting(false); }
  };
  return <section className="cover-editor" aria-label="成片封面">
    <h3>成片封面</h3>
    {onGenerateCover ? <>
      <fieldset className="cover-fields" disabled={disabled}>
        <legend className="sr-only">封面设置</legend>
        <video ref={video} className="cover-frame-preview" aria-label="封面选帧预览" controls muted preload="metadata"
          src={projectMediaUrl(backendUrl, project?.project_id, result.output_path, result.created_at)}
          onLoadedMetadata={(event) => { const media = event.currentTarget; if (Number.isFinite(media.duration)) setDuration(media.duration); if (Number(settings.time_seconds) < media.duration) media.currentTime = Number(settings.time_seconds); }} />
        <div className="editor-field-row">
          <label className="full-label">封面时间（秒）<input type="number" min="0" max={duration || undefined} step="0.01" value={settings.time_seconds} onChange={(event) => seek(event.target.value)} /></label>
          <EditorButton icon={Crosshair} label="使用当前视频帧" disabled={disabled} onClick={() => seek(Math.max(0, Math.min(video.current?.currentTime || 0, duration - 0.001)))} />
        </div>
        <label className="full-label">封面选帧<input type="range" min="0" max={Math.max(0, duration - 0.001)} step="0.001" value={Number(settings.time_seconds) || 0} onChange={(event) => seek(event.target.value)} /></label>
        <label className="full-label">封面标题<textarea className="mini-textarea" maxLength={80} value={settings.title} onChange={(event) => patch({ title: event.target.value })} /></label>
        <div className="editor-field-row">
          <label className="full-label">标题位置<select value={settings.position} onChange={(event) => patch({ position: event.target.value })}><option value="top">顶部</option><option value="center">居中</option><option value="bottom">底部</option></select></label>
          <label className="full-label">封面字号（720p）<input type="number" min="16" max="96" value={settings.font_size} onChange={(event) => patch({ font_size: event.target.value })} /></label>
        </div>
        <div className="subtitle-color-row">
          <label className="custom-color" title="封面标题颜色"><span className="sr-only">封面标题颜色</span><input type="color" value={settings.color} onChange={(event) => patch({ color: event.target.value.toUpperCase() })} /></label>
          <label className="checkbox-control"><input type="checkbox" checked={settings.background} onChange={(event) => patch({ background: event.target.checked })} />标题背景</label>
        </div>
        {errors.length ? <ul className="editor-errors" role="alert">{errors.map((error) => <li key={error}>{error}</li>)}</ul> : null}
        <EditorButton icon={Camera} label="生成封面" className="primary" disabled={disabled || errors.length > 0} onClick={generate}>生成封面</EditorButton>
      </fieldset>
      {message ? <p className="error-text" role="alert">{message}</p> : null}
      {job ? <JobProgress job={job} title="封面生成" onCancelJob={onCancelJob} /> : null}
    </> : null}
    {cover ? <div className="cover-result">
      <label className="full-label">已生成封面<select value={cover.image_path} onChange={(event) => setSelectedCover(event.target.value)}>
        {covers.map((item, index) => <option key={item.image_path} value={item.image_path}>{index === 0 ? "最新" : `历史 ${covers.length - index}`} · {Number(item.frame_time_seconds).toFixed(2)} 秒 · {item.settings?.title || "无标题"}</option>)}
      </select></label>
      <img src={projectMediaUrl(backendUrl, project?.project_id, cover.image_path)} alt={cover.settings?.title || "已生成封面"} width={cover.width} height={cover.height} />
      <a className="button-link" href={`${projectMediaUrl(backendUrl, project?.project_id, cover.image_path)}&download=1`} download><Download size={16} aria-hidden="true" />下载封面 PNG</a>
    </div> : null}
  </section>;
}
