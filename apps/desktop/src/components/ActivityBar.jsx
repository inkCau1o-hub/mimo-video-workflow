import { useState } from "react";

const steps = [
  ["transcript", "转写"],
  ["copywriting", "文案"],
  ["storyboard", "分镜"],
  ["voiceover", "配音"],
  ["assembly", "合成"],
];

export function ActivityBar({ project, activity, onCreateActivity, onSelectActivity }) {
  const [name, setName] = useState("");
  const activities = project?.activities || [];
  const status = activity?.status || {};

  const create = () => {
    const value = name.trim();
    onCreateActivity(value);
    setName("");
  };

  return (
    <section className="activity-bar">
      <div>
        <div className="section-label">当前活动</div>
        <div className="activity-title">{activity?.name || "等待创建项目"}</div>
      </div>
      <div className="activity-controls">
        <select
          value={activity?.activity_id || ""}
          onChange={(event) => onSelectActivity(event.target.value)}
          disabled={!activities.length}
        >
          {activities.length === 0 ? (
            <option value="">暂无活动</option>
          ) : (
            activities.map((item) => (
              <option value={item.activity_id} key={item.activity_id}>
                {item.name || item.activity_id}
              </option>
            ))
          )}
        </select>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="新活动名称"
          disabled={!project}
        />
        <button type="button" onClick={create} disabled={!project}>
          新建活动
        </button>
      </div>
      <div className="activity-steps">
        {steps.map(([key, label]) => (
          <span className={status[key] === "ready" ? "step-pill ready" : "step-pill"} key={key}>
            {label}
          </span>
        ))}
      </div>
    </section>
  );
}
