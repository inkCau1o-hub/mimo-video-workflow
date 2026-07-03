import { useState } from "react";

function readyLabel(ready) {
  return ready ? "已就绪" : "未就绪";
}

export function AssemblyPage({ activity, project, onBuildAssemblyPlan }) {
  const [outputPath, setOutputPath] = useState("");
  const referenceText = activity?.reference_copy?.source_text || project?.reference_copy?.source_text || "";
  const analysis = activity?.storyboard_analysis || project?.storyboard_analysis;
  const assets = activity?.material_assets || project?.material_assets || [];
  const segments = activity?.voiceover_segments || project?.voiceover_segments || [];
  const plan = activity?.assembly_plan || project?.assembly_plan;
  const command = plan?.metadata?.renderer?.command;
  const canBuild = Boolean(project && analysis?.shots?.length);

  return (
    <div className="page-grid two-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>合成</h2>
            <p>根据分镜、素材和配音生成 ffmpeg 合成计划。</p>
          </div>
        </div>
        <div className="pipeline-grid">
          <div><span>转写文案</span><strong>{readyLabel(Boolean(referenceText.trim()))}</strong></div>
          <div><span>分镜脚本</span><strong>{analysis?.shots?.length || 0} 条</strong></div>
          <div><span>配音音频</span><strong>{segments.length} 条</strong></div>
          <div><span>素材资产</span><strong>{assets.length} 个</strong></div>
        </div>
        <label className="full-label">
          输出视频路径
          <input
            value={outputPath}
            onChange={(event) => setOutputPath(event.target.value)}
            placeholder="/Users/mac/Documents/final_video.mp4"
          />
        </label>
        <button type="button" className="primary" onClick={() => onBuildAssemblyPlan(outputPath)} disabled={!canBuild}>
          生成合成计划
        </button>
        <div className="shot-list">
          {(analysis?.shots || []).map((shot) => {
            const planItem = (plan?.items || []).find((item) => item.shot_id === shot.shot_id);
            return (
              <article className="shot-row" key={shot.shot_id}>
                <strong>{shot.shot_id} · {shot.title}</strong>
                <p>{shot.tts_text || shot.script_text}</p>
                <span>
                  {planItem ? `素材 ${planItem.material_asset_id || "-"} · 配音 ${planItem.voiceover_segment_id || "-"}` : "等待合成计划"}
                </span>
              </article>
            );
          })}
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
