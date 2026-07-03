const statusItems = [
  ["current_activity_name", "当前活动", (value) => value || "未选择"],
  ["activity_count", "活动数", (value) => `${value || 0}`],
  ["has_reference", "参考文案", (value) => (value ? "已就绪" : "未设置")],
  ["storyboard_shots", "分镜数", (value) => `${value || 0}`],
  ["material_assets", "素材数", (value) => `${value || 0}`],
  ["voiceover_segments", "配音数", (value) => `${value || 0}`],
  ["has_assembly_plan", "合成计划", (value) => (value ? "已生成" : "未生成")],
];

export function ProjectStatusPanel({ status }) {
  return (
    <section className="side-panel-block">
      <div className="panel-title">项目状态</div>
      <div className="status-grid">
        {statusItems.map(([key, label, format]) => (
          <div className="status-row" key={key}>
            <span>{label}</span>
            <strong>{format(status?.[key])}</strong>
          </div>
        ))}
      </div>
      <div className="project-path">{status?.project_dir || "等待创建项目"}</div>
    </section>
  );
}
