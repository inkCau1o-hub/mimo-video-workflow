export function JobLogPanel({ jobs }) {
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
              <div className={`job-state ${job.status}`}>{job.status}</div>
              <progress value={job.progress || 0} max="100" />
              {job.error ? <p className="error-text">{job.error}</p> : null}
            </div>
          ))
        )}
      </div>
    </section>
  );
}
