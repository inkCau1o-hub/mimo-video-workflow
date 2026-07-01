import { useState } from "react";

export function AssemblyPage({ project, onBuildAssemblyPlan }) {
  const [outputPath, setOutputPath] = useState("");
  const plan = project?.assembly_plan;
  const command = plan?.metadata?.renderer?.command;

  return (
    <div className="page-grid two-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>合成</h2>
            <p>根据分镜、素材和配音生成 ffmpeg 合成计划。</p>
          </div>
          <button type="button" className="primary" onClick={() => onBuildAssemblyPlan(outputPath)}>
            生成计划
          </button>
        </div>
        <label className="full-label">
          输出视频路径
          <input
            value={outputPath}
            onChange={(event) => setOutputPath(event.target.value)}
            placeholder="/Users/mac/Documents/final_video.mp4"
          />
        </label>
        <div className="shot-list">
          {(plan?.items || []).map((item) => (
            <article className="shot-row" key={item.item_id}>
              <strong>{item.shot_id}</strong>
              <p>素材 {item.material_asset_id} · 配音 {item.voiceover_segment_id}</p>
              <span>{item.duration_seconds}s</span>
            </article>
          ))}
        </div>
      </section>
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>命令预览</h2>
            <p>当前只预览，不自动渲染最终视频。</p>
          </div>
        </div>
        <pre className="readout">{Array.isArray(command) ? command.join(" ") : command || "等待生成合成计划..."}</pre>
      </section>
    </div>
  );
}
