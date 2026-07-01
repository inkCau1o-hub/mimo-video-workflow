const pages = [
  ["reference", "参考转写"],
  ["storyboard", "分镜分析"],
  ["materials", "素材库"],
  ["voiceover", "配音"],
  ["assembly", "合成"],
  ["results", "日志结果"],
];

export function Sidebar({ activePage, onChangePage, projects }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-section">
        <div className="section-label">Workflow</div>
        <nav className="nav-list">
          {pages.map(([id, label]) => (
            <button
              type="button"
              key={id}
              className={activePage === id ? "nav-item active" : "nav-item"}
              onClick={() => onChangePage(id)}
            >
              {label}
            </button>
          ))}
        </nav>
      </div>
      <div className="sidebar-section">
        <div className="section-label">Recent Projects</div>
        <div className="project-list">
          {projects.length === 0 ? (
            <div className="muted">暂无项目</div>
          ) : (
            projects.map((project) => (
              <div className="project-row" key={project.project_id}>
                <strong>{project.name}</strong>
                <span>{project.project_id}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </aside>
  );
}
