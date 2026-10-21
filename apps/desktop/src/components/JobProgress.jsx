import { isTerminalJob, jobStatusText } from "../api/jobMonitor";

export function JobProgress({ job, title = "任务进度", onCancelJob }) {
  const progress = Math.min(100, Math.max(0, Number(job?.progress) || 0));
  return (
    <div className="job-progress" aria-live="polite">
      <div className="queue-header"><strong>{title}</strong><span>{jobStatusText(job)}{job ? ` · ${progress.toFixed(0)}%` : ""}</span></div>
      <progress className="job-meter" value={progress} max="100" aria-label={title} />
      {job?.message ? <p className="muted">{job.message}</p> : null}
      {job?.status === "failed" ? <p className="error-text" role="alert">任务失败：{job.error || job.message || "后端未提供错误详情"}</p> : null}
      {job?.poll_error ? <p className="error-text" role="status">{job.poll_error}</p> : null}
      {job?.logs?.length ? <pre className="readout compact-readout">{job.logs.slice(-6).join("\n")}</pre> : null}
      {onCancelJob && job && !isTerminalJob(job) ? <button type="button" onClick={() => onCancelJob(job.job_id)} disabled={job.cancel_requested}>停止任务</button> : null}
    </div>
  );
}
