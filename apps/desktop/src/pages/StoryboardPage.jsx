import { useState } from "react";
import { finalCopyText, pickStoryboardAnalysis, productProfile, rewrittenCopy } from "../workflowData";

export function StoryboardPage({ activity, project, onAnalyzeStoryboard }) {
  const [duration, setDuration] = useState("30");
  const [shotCount, setShotCount] = useState("5");
  const inputText = finalCopyText(activity, project);
  const rewrite = rewrittenCopy(activity);
  const profile = productProfile(project);
  const analysis = pickStoryboardAnalysis(activity, project);
  const canAnalyze = Boolean(project && inputText.trim());

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
            <span>{inputText ? `${inputText.length} 字` : "未就绪"}</span>
          </div>
          <p>{inputText ? `将使用${rewrite ? "文案处理成稿" : "当前活动转写结果"}，并结合 ${profile.product_name || "项目产品"} 的产品资料生成分镜。` : "请先完成参考转写和文案处理。"}</p>
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
