import { useEffect, useMemo, useRef, useState } from "react";
import { pickList, referenceText as selectReferenceText } from "../workflowData";

const whisperModels = ["tiny", "base", "small", "medium", "large-v3"];
const videoExtensions = [".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"];

function splitLines(value) {
  return String(value || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function fileName(path) {
  return String(path || "").split(/[\\/]/).filter(Boolean).pop() || path;
}

function filePath(file) {
  return file?.path || file?.webkitRelativePath || file?.name || "";
}

function isLikelyVideoPath(path) {
  const text = String(path || "").toLowerCase();
  return videoExtensions.some((extension) => text.endsWith(extension));
}

function terminalJobStatus(status) {
  return ["succeeded", "failed", "cancelled"].includes(status);
}

function progressText(job) {
  if (!job) return "等待开始";
  const value = Number(job.progress || 0).toFixed(0);
  return `${job.status} · ${value}%${job.message ? ` · ${job.message}` : ""}`;
}

export function ReferencePage({
  activity,
  job,
  project,
  onCancelJob,
  onGoStoryboard,
  onSetReferenceText,
  onSetReferenceVideos,
  onTranscribeReference,
}) {
  const [referenceText, setReferenceText] = useState("");
  const [videoInput, setVideoInput] = useState("");
  const [videoQueue, setVideoQueue] = useState([]);
  const [modelSize, setModelSize] = useState("medium");
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef(null);
  const folderInputRef = useRef(null);
  const savedText = selectReferenceText(activity, project);
  const savedVideos = useMemo(() => pickList(activity, project, "reference_videos"), [activity, project]);
  const isRunning = Boolean(job && !terminalJobStatus(job.status));

  useEffect(() => {
    setReferenceText(savedText);
  }, [savedText]);

  useEffect(() => {
    setVideoQueue(savedVideos);
  }, [savedVideos]);

  const addVideoPaths = (paths) => {
    const next = paths.map((path) => String(path || "").trim()).filter(Boolean);
    if (!next.length) return;
    const nextQueue = Array.from(new Set([...videoQueue, ...next]));
    setVideoQueue(nextQueue);
    onSetReferenceVideos?.(nextQueue);
  };

  const addVideos = () => {
    addVideoPaths(splitLines(videoInput));
    setVideoInput("");
  };

  const addFiles = (files) => {
    const paths = Array.from(files || [])
      .map(filePath)
      .filter((path) => path && isLikelyVideoPath(path));
    addVideoPaths(paths);
  };

  const removeVideo = (path) => {
    const nextQueue = videoQueue.filter((item) => item !== path);
    setVideoQueue(nextQueue);
    onSetReferenceVideos?.(nextQueue);
  };

  const clearVideos = () => {
    setVideoQueue([]);
    onSetReferenceVideos?.([]);
  };

  const dropVideos = (event) => {
    event.preventDefault();
    setIsDragging(false);
    const textPaths = splitLines(event.dataTransfer.getData("text/plain"));
    const filePaths = Array.from(event.dataTransfer.files || []).map(filePath);
    addVideoPaths([...filePaths, ...textPaths].filter((path) => !path || isLikelyVideoPath(path)));
  };

  const sendToStoryboard = async () => {
    await onSetReferenceText(referenceText);
    onGoStoryboard?.();
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
          <button type="button" className="primary" onClick={() => onSetReferenceText(referenceText)} disabled={!project}>
            保存文案
          </button>
        </div>
        <div className="next-step">
          <strong>下一步：生成分镜</strong>
          <span>{savedText ? "文案已就绪，可以进入分镜分析。" : "先导入视频并完成转写，或直接粘贴参考文案。"}</span>
          <button type="button" onClick={onGoStoryboard} disabled={!savedText}>
            去分镜
          </button>
        </div>

        <div className="section-header compact">
          <div>
            <h3>导入参考视频</h3>
            <p>拖拽视频、选择文件/文件夹，或输入绝对路径。</p>
          </div>
          <div className="button-group">
            <button type="button" onClick={() => fileInputRef.current?.click()} disabled={!project}>
              选择视频
            </button>
            <button type="button" onClick={() => folderInputRef.current?.click()} disabled={!project}>
              选择文件夹
            </button>
          </div>
        </div>
        <input
          ref={fileInputRef}
          className="hidden-input"
          type="file"
          accept="video/*,.mp4,.mov,.m4v,.avi,.mkv,.webm"
          multiple
          onChange={(event) => {
            addFiles(event.target.files);
            event.target.value = "";
          }}
        />
        <input
          ref={folderInputRef}
          className="hidden-input"
          type="file"
          multiple
          webkitdirectory=""
          onChange={(event) => {
            addFiles(event.target.files);
            event.target.value = "";
          }}
        />
        <div
          className={isDragging ? "drop-zone dragging" : "drop-zone"}
          onDragEnter={(event) => {
            event.preventDefault();
            setIsDragging(true);
          }}
          onDragOver={(event) => {
            event.preventDefault();
            setIsDragging(true);
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={dropVideos}
        >
          <strong>拖拽视频文件到这里</strong>
          <span>Finder 拖入、文件选择、或下方粘贴路径都可以加入当前活动。</span>
        </div>
        <textarea
          className="small-textarea"
          value={videoInput}
          onChange={(event) => setVideoInput(event.target.value)}
          placeholder={"/Users/mac/Desktop/ai/reference.mp4\n/Users/mac/Desktop/ai/reference-2.mp4"}
        />
        <div className="action-row">
          <button type="button" onClick={addVideos} disabled={!project || !videoInput.trim()}>
            加入队列
          </button>
          <label className="inline-control">
            Whisper模型
            <select value={modelSize} onChange={(event) => setModelSize(event.target.value)} disabled={isRunning}>
              {whisperModels.map((model) => (
                <option value={model} key={model}>{model}</option>
              ))}
            </select>
          </label>
        </div>
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
                  <div>
                    <strong>{fileName(path)}</strong>
                    <span>{path}</span>
                  </div>
                  <button type="button" onClick={() => removeVideo(path)} disabled={isRunning}>
                    移除
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="action-row">
          <button
            type="button"
            className="primary"
            onClick={() => onTranscribeReference({ videos: videoQueue, model_size: modelSize })}
            disabled={!canTranscribe || isRunning}
          >
            开始转写
          </button>
          <button type="button" onClick={() => onCancelJob?.(job?.job_id)} disabled={!isRunning || !job?.job_id}>
            停止任务
          </button>
          <button type="button" onClick={clearVideos} disabled={!videoQueue.length || isRunning}>
            清空队列
          </button>
        </div>
        <div className="job-progress">
          <div className="queue-header">
            <strong>转写进度</strong>
            <span>{progressText(job)}</span>
          </div>
          <div className="progress-track">
            <div style={{ width: `${Math.min(100, Math.max(0, Number(job?.progress || 0)))}%` }} />
          </div>
          <pre className="readout compact-readout">
            {(job?.logs || []).slice(-6).join("\n") || job?.error || "等待转写任务..."}
          </pre>
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
          <button type="button" onClick={sendToStoryboard} disabled={!project || !referenceText.trim()}>
            发送到分镜
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
