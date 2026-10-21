import { useState } from "react";
import { EditableText } from "../components/EditableText";
import { JobProgress } from "../components/JobProgress";
import {
  finalCopyText,
  generatedTitles,
  productProfile,
  referenceText as selectReferenceText,
  rewrittenCopy,
} from "../workflowData";

function terminalJobStatus(status) {
  return ["succeeded", "failed", "cancelled"].includes(status);
}

export function CopywritingPage({
  activity,
  job,
  onGenerateTitles,
  onGoStoryboard,
  onRewriteCopy,
  onSaveCopyText,
  project,
}) {
  const [titleCount, setTitleCount] = useState("5");
  const [textDirty, setTextDirty] = useState(false);
  const profile = productProfile(project);
  const referenceText = selectReferenceText(activity, project);
  const rewrite = rewrittenCopy(activity);
  const titles = generatedTitles(activity);
  const finalText = finalCopyText(activity, project);
  const isRunning = Boolean(job && !terminalJobStatus(job.status));
  const canRewrite = Boolean(project && referenceText.trim());
  const canTitle = Boolean(project && finalText.trim());

  return (
    <div className="page-grid two-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>文案处理</h2>
            <p>{activity ? `当前活动：${activity.name}` : "请先创建或打开项目。"}</p>
          </div>
          <span className={rewrite ? "status-pill ok" : "status-pill"}>{rewrite ? "已改写" : "待处理"}</span>
        </div>
        <div className="source-preview">
          <div className="queue-header">
            <strong>输入状态</strong>
            <span>{referenceText ? `${referenceText.length} 字转写` : "未就绪"}</span>
          </div>
          <p>{profile.product_name ? `将结合 ${profile.product_name} 的产品资料生成成稿文案。` : "请先在项目资料页补全产品信息。"}</p>
        </div>
        <div className="copywriting-actions">
          <button type="button" className="primary" onClick={() => onRewriteCopy?.({})} disabled={!canRewrite || isRunning || textDirty}>
            文案改写
          </button>
          <label className="inline-control">
            标题数量
            <input type="number" min="1" max="20" step="1" value={titleCount} onChange={(event) => setTitleCount(event.target.value)} disabled={isRunning} />
          </label>
          <button
            type="button"
            onClick={() => onGenerateTitles?.({ count: Number(titleCount) || 5 })}
            disabled={!canTitle || isRunning || textDirty || !Number.isInteger(Number(titleCount)) || Number(titleCount) < 1 || Number(titleCount) > 20}
          >
            标题生成
          </button>
          <button type="button" onClick={onGoStoryboard} disabled={!finalText.trim() || textDirty}>
            发送到分镜
          </button>
        </div>
        <JobProgress job={job} title="处理进度" />
      </section>
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>成稿与标题</h2>
            <p>{rewrite ? `${rewrite.length} 字成稿` : "改写完成后显示成稿文案。"}</p>
          </div>
          <button type="button" onClick={() => navigator.clipboard?.writeText(rewrite)} disabled={!rewrite}>
            复制成稿
          </button>
        </div>
        <EditableText label="成稿文案" savedValue={rewrite} onSave={onSaveCopyText} disabled={!project || isRunning} onDirtyChange={setTextDirty} />
        <div className="title-list">
          <div className="queue-header">
            <strong>标题</strong>
            <span>{titles.length} 个</span>
          </div>
          {titles.length ? (
            titles.map((title) => <div className="title-row" key={title}>{title}</div>)
          ) : (
            <div className="empty-state">还没有生成标题。</div>
          )}
        </div>
      </section>
    </div>
  );
}
