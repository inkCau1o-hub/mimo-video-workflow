export function ResultsPage({ project, jobs }) {
  return (
    <div className="page-grid two-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>项目快照</h2>
            <p>后端 ProductionProject 当前状态。</p>
          </div>
        </div>
        <pre className="readout">{JSON.stringify(project || {}, null, 2)}</pre>
      </section>
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>Job 日志</h2>
            <p>每个后端任务的状态、错误和日志。</p>
          </div>
        </div>
        <pre className="readout">{JSON.stringify(jobs, null, 2)}</pre>
      </section>
    </div>
  );
}
