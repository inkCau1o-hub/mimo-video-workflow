import { isTerminalJob, jobStatusText } from "../api/jobMonitor";

export function JobLogPanel({ jobs, onCancelJob }) {
  return (
    <section className="side-panel-block">
      <div className="panel-title">执行记录</div>
      <div className="job-list">
        {jobs.length === 0 ? (
          <div className="muted">暂无任务</div>
        ) : (
          jobs.map((job) => (
            <div className="job-row" key={job.job_id}>
              <div>
                <strong>{job.job_type}</strong>
                <span>{job.job_id}</span>
              </div>
              <div className={`job-state ${job.status}`}>{jobStatusText(job)}</div>
              <progress value={job.progress || 0} max="100" />
              {job.status === "failed" ? <p className="error-text" role="alert">任务失败：{job.error || job.message || "后端未提供错误详情"}</p> : null}
              {job.poll_error ? <p className="error-text">{job.poll_error}</p> : null}
              {!isTerminalJob(job) && onCancelJob ? <button type="button" onClick={() => onCancelJob(job.job_id)} disabled={job.cancel_requested}>停止任务</button> : null}
            </div>
          ))
        )}
      </div>
    </section>
  );
}
