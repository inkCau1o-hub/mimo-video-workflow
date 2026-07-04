import { useState } from "react";
import { pickStoryboardAnalysis, productProfile, referenceText as selectReferenceText } from "../workflowData";

export function StoryboardPage({ activity, project, onAnalyzeStoryboard }) {
  const [duration, setDuration] = useState("30");
  const [shotCount, setShotCount] = useState("5");
  const referenceText = selectReferenceText(activity, project);
  const profile = productProfile(project);
  const analysis = pickStoryboardAnalysis(activity, project);
  const canAnalyze = Boolean(project && referenceText.trim());

  return (
    <div className="page-grid two-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>分镜分析</h2>
            <p>根据参考文案生成结构化分镜。</p>
          </div>
          <button
            type="button"
            className="primary"
            onClick={() => onAnalyzeStoryboard({ target_duration_seconds: Number(duration), shot_count: Number(shotCount) })}
            disabled={!canAnalyze}
          >
            分析分镜
          </button>
        </div>
        <div className="source-preview">
          <div className="queue-header">
            <strong>输入状态</strong>
            <span>{referenceText ? `${referenceText.length} 字` : "未就绪"}</span>
          </div>
          <p>{referenceText ? `将使用当前活动已保存的转写结果，并结合 ${profile.product_name || "项目产品"} 的产品资料生成分镜。` : "请先在参考转写页生成文案。"}</p>
        </div>
        <div className="field-row">
          <label>
            目标时长
            <input value={duration} onChange={(event) => setDuration(event.target.value)} />
          </label>
          <label>
            分镜数
            <input value={shotCount} onChange={(event) => setShotCount(event.target.value)} />
          </label>
        </div>
        <div className="shot-list">
          {(analysis?.shots || []).map((shot) => (
            <article className="shot-row" key={shot.shot_id}>
              <strong>{shot.shot_id} · {shot.title}</strong>
              <p>{shot.tts_text || shot.script_text}</p>
              <span>{shot.duration_seconds}s</span>
            </article>
          ))}
        </div>
      </section>
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>分镜 JSON</h2>
            <p>供调试和后续 Tauri 集成使用。</p>
          </div>
        </div>
        <pre className="readout">{JSON.stringify(analysis || {}, null, 2)}</pre>
      </section>
    </div>
  );
}
