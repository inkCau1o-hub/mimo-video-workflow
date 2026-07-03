import { useMemo, useState } from "react";
import { pickAssemblyPlan, pickList, pickStoryboardAnalysis, referenceText as selectReferenceText } from "../workflowData";

function readyLabel(ready) {
  return ready ? "已就绪" : "未就绪";
}

function indexBy(items, key) {
  const result = new Map();
  for (const item of items || []) {
    if (item?.[key]) result.set(item[key], item);
  }
  return result;
}

export function AssemblyPage({ activity, project, onBuildAssemblyPlan }) {
  const [outputPath, setOutputPath] = useState("");
  const referenceText = selectReferenceText(activity, project);
  const analysis = pickStoryboardAnalysis(activity, project);
  const assets = pickList(activity, project, "material_assets");
  const segments = pickList(activity, project, "voiceover_segments");
  const plan = pickAssemblyPlan(activity, project);
  const command = plan?.metadata?.renderer?.command;
  const canBuild = Boolean(project && analysis?.shots?.length);
  const assetById = useMemo(() => indexBy(assets, "asset_id"), [assets]);
  const segmentById = useMemo(() => indexBy(segments, "segment_id"), [segments]);
  const segmentByShot = useMemo(() => indexBy(segments, "shot_id"), [segments]);
  const planItemByShot = useMemo(() => indexBy(plan?.items || [], "shot_id"), [plan]);
  const validation = plan?.metadata?.renderer?.validation;

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
        <div className="assembly-table">
          <div className="assembly-row header">
            <span>镜头</span>
            <span>画面 / 文案</span>
            <span>素材</span>
            <span>配音</span>
            <span>时长</span>
          </div>
          {(analysis?.shots || []).length === 0 ? (
            <div className="empty-state">等待分镜脚本。</div>
          ) : (
            (analysis?.shots || []).map((shot) => {
              const planItem = planItemByShot.get(shot.shot_id);
              const asset = assetById.get(planItem?.material_asset_id);
              const segment = segmentById.get(planItem?.voiceover_segment_id) || segmentByShot.get(shot.shot_id);
              return (
                <article className="assembly-row" key={shot.shot_id}>
                  <strong>{shot.shot_id}</strong>
                  <div>
                    <strong>{shot.title || "未命名镜头"}</strong>
                    <p>{shot.tts_text || shot.script_text}</p>
                  </div>
                  <div>
                    <strong>
                      {asset?.material_type || "素材"}
                      {planItem?.material_asset_id ? ` · ${planItem.material_asset_id}` : ""}
                    </strong>
                    <p>{asset?.scene_summary || (shot.material_requirements || []).join("、") || "等待素材"}</p>
                  </div>
                  <div>
                    <strong>{segment?.segment_id || "待生成"}</strong>
                    <p>{segment?.audio_path || "等待配音"}</p>
                  </div>
                  <span>
                    {planItem?.duration_seconds || shot.duration_seconds || 0}s
                  </span>
                </article>
              );
            })
          )}
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
        {validation?.warnings?.length ? (
          <div className="warning-list">
            {validation.warnings.map((warning) => (
              <div key={warning}>{warning}</div>
            ))}
          </div>
        ) : null}
      </section>
    </div>
  );
}
