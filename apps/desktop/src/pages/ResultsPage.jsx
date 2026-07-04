function redactReferenceText(value) {
  if (!value || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map(redactReferenceText);
  const next = {};
  for (const [key, item] of Object.entries(value)) {
    if (key === "source_text" && typeof item === "string") {
      next[key] = item ? `[已隐藏原始转写正文，${item.length} 字]` : "";
    } else if (
      key === "text"
      && typeof item === "string"
      && value.created_at
      && ("source" in value || "title" in value)
    ) {
      next[key] = item ? `[已隐藏原始转写正文，${item.length} 字]` : "";
    } else {
      next[key] = redactReferenceText(item);
    }
  }
  return next;
}

export function ResultsPage({ project, jobs }) {
  const safeProject = redactReferenceText(project || {});

  return (
    <div className="page-grid two-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>项目快照</h2>
            <p>后端 ProductionProject 当前状态。</p>
          </div>
        </div>
        <pre className="readout">{JSON.stringify(safeProject, null, 2)}</pre>
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
