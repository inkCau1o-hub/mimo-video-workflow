import { useState } from "react";

const pages = [
  ["profile", "项目资料"],
  ["reference", "参考转写"],
  ["storyboard", "分镜分析"],
  ["materials", "素材库"],
  ["voiceover", "配音"],
  ["assembly", "合成"],
  ["results", "日志结果"],
];

function projectSummary(project) {
  const items = [
    project.has_reference ? "转写" : "",
    project.storyboard_shots ? `分镜 ${project.storyboard_shots}` : "",
    project.voiceover_segments ? `配音 ${project.voiceover_segments}` : "",
    project.has_assembly_plan ? "合成" : "",
  ].filter(Boolean);
  return items.length ? items.join(" · ") : "未开始";
}

export function Sidebar({
  activePage,
  currentProjectId,
  onChangePage,
  onDeleteProject,
  onOpenProject,
  onRenameProject,
  projects,
}) {
  const [editingProjectId, setEditingProjectId] = useState("");
  const [draftName, setDraftName] = useState("");
  const [confirmingDeleteId, setConfirmingDeleteId] = useState("");

  const startRename = (project) => {
    setEditingProjectId(project.project_id);
    setDraftName(project.name || "");
    setConfirmingDeleteId("");
  };

  const submitRename = (project) => {
    const value = draftName.trim();
    if (value && value !== project.name) onRenameProject(project, value);
    setEditingProjectId("");
    setDraftName("");
  };

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
              <article
                className={project.project_id === currentProjectId ? "project-row active" : "project-row"}
                key={project.project_id}
              >
                <button type="button" className="project-open" onClick={() => onOpenProject(project.project_id)}>
                  <strong>{project.name}</strong>
                  <span>{project.project_id}</span>
                  <em>{projectSummary(project)}</em>
                </button>
                <div className="project-actions">
                  {editingProjectId === project.project_id ? (
                    <>
                      <input
                        value={draftName}
                        onChange={(event) => setDraftName(event.target.value)}
                        placeholder="项目名称"
                      />
                      <button type="button" onClick={() => submitRename(project)}>
                        保存
                      </button>
                    </>
                  ) : (
                    <>
                      <button type="button" onClick={() => startRename(project)}>
                        重命名
                      </button>
                      {confirmingDeleteId === project.project_id ? (
                        <button type="button" className="danger" onClick={() => onDeleteProject(project)}>
                          确认删除
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="danger"
                          onClick={() => {
                            setConfirmingDeleteId(project.project_id);
                            setEditingProjectId("");
                          }}
                        >
                          删除
                        </button>
                      )}
                    </>
                  )}
                </div>
              </article>
            ))
          )}
        </div>
      </div>
    </aside>
  );
}
