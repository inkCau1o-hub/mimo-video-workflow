import { useEffect, useMemo, useState } from "react";
import { pickList, referenceText as selectReferenceText } from "../workflowData";

function splitLines(value) {
  return String(value || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function fileName(path) {
  return String(path || "").split("/").filter(Boolean).pop() || path;
}

export function ReferencePage({ activity, project, onSetReferenceText, onSetReferenceVideos, onTranscribeReference }) {
  const [referenceText, setReferenceText] = useState("");
  const [videoInput, setVideoInput] = useState("");
  const [videoQueue, setVideoQueue] = useState([]);
  const savedText = selectReferenceText(activity, project);
  const savedVideos = useMemo(() => pickList(activity, project, "reference_videos"), [activity, project]);

  useEffect(() => {
    setReferenceText(savedText);
  }, [savedText]);

  useEffect(() => {
    setVideoQueue(savedVideos);
  }, [savedVideos]);

  const addVideos = () => {
    const next = splitLines(videoInput);
    if (!next.length) return;
    const nextQueue = Array.from(new Set([...videoQueue, ...next]));
    setVideoQueue(nextQueue);
    onSetReferenceVideos?.(nextQueue);
    setVideoInput("");
  };

  const clearVideos = () => {
    setVideoQueue([]);
    onSetReferenceVideos?.([]);
  };
  const canTranscribe = Boolean(project && videoQueue.length > 0);

  return (
    <div className="page-grid two-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>参考转写</h2>
            <p>{activity ? `当前活动：${activity.name}` : "请先创建或打开项目。"}</p>
          </div>
          <button type="button" className="primary" onClick={() => onSetReferenceText(referenceText)}>
            保存文案
          </button>
        </div>

        <div className="section-header compact">
          <div>
            <h3>导入参考视频</h3>
            <p>输入绝对路径，每行一个；加入队列后再开始转写。</p>
          </div>
          <button type="button" onClick={addVideos}>
            加入队列
          </button>
        </div>
        <textarea
          className="small-textarea"
          value={videoInput}
          onChange={(event) => setVideoInput(event.target.value)}
          placeholder={"/Users/mac/Desktop/ai/reference.mp4\n/Users/mac/Desktop/ai/reference-2.mp4"}
        />
        <div className="queue-panel">
          <div className="queue-header">
            <strong>已导入视频</strong>
            <span>{videoQueue.length} 个</span>
          </div>
          {videoQueue.length === 0 ? (
            <div className="empty-state">还没有导入参考视频。</div>
          ) : (
            <div className="queue-list">
              {videoQueue.map((path) => (
                <div className="queue-row" key={path}>
                  <strong>{fileName(path)}</strong>
                  <span>{path}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="action-row">
          <button type="button" className="primary" onClick={() => onTranscribeReference(videoQueue)} disabled={!canTranscribe}>
            开始转写
          </button>
          <button type="button" onClick={clearVideos} disabled={!videoQueue.length}>
            清空队列
          </button>
        </div>
      </section>
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>转写文案</h2>
            <p>{savedText ? `${savedText.length} 字` : "尚未写入项目"}</p>
          </div>
        </div>
        <textarea
          value={referenceText}
          onChange={(event) => setReferenceText(event.target.value)}
          placeholder="转写完成后会显示在这里，也可以直接粘贴文案。"
        />
        <div className="action-row">
          <button type="button" className="primary" onClick={() => onSetReferenceText(referenceText)} disabled={!project}>
            保存到当前活动
          </button>
          <button type="button" onClick={() => navigator.clipboard?.writeText(referenceText)} disabled={!referenceText}>
            复制文案
          </button>
        </div>
        <pre className="readout compact-readout">{savedText || "等待参考文案..."}</pre>
      </section>
    </div>
  );
}
