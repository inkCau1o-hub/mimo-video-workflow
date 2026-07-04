import { useEffect, useMemo, useRef, useState } from "react";
import { pickList, productProfile, referenceText as selectReferenceText } from "../workflowData";

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

function nativeFilePath(file) {
  return file?.path || "";
}

function isLikelyVideoPath(path) {
  const text = String(path || "").toLowerCase();
  return videoExtensions.some((extension) => text.endsWith(extension));
}

function isLikelyVideoFile(file) {
  return isLikelyVideoPath(file?.path || file?.webkitRelativePath || file?.name || "");
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
  validation,
  onCancelJob,
  onGoCopywriting,
  onValidateReferenceVideos,
  onSetReferenceVideos,
  onTranscribeReference,
  onUploadReferenceVideos,
}) {
  const [videoInput, setVideoInput] = useState("");
  const [videoQueue, setVideoQueue] = useState([]);
  const [modelSize, setModelSize] = useState("medium");
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef(null);
  const folderInputRef = useRef(null);
  const savedText = selectReferenceText(activity, project);
  const profile = productProfile(project);
  const savedVideos = useMemo(() => pickList(activity, project, "reference_videos"), [activity, project]);
  const isRunning = Boolean(job && !terminalJobStatus(job.status));
  const validationByInput = useMemo(() => {
    const result = new Map();
    for (const item of validation?.videos || []) {
      result.set(item.input_path, item);
      result.set(item.resolved_path, item);
    }
    return result;
  }, [validation]);
  const hasMissingPaths = Boolean(videoQueue.length && validation && !validation.ok);

  useEffect(() => {
    setVideoQueue(savedVideos);
  }, [savedVideos]);

  useEffect(() => {
    if (project && videoQueue.length > 0) {
      onValidateReferenceVideos?.(videoQueue);
    }
  }, [onValidateReferenceVideos, project, videoQueue]);

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

  const addFiles = async (files) => {
    const selectedFiles = Array.from(files || []).filter(isLikelyVideoFile);
    if (!selectedFiles.length) return;

    const nativePaths = selectedFiles.map(nativeFilePath).filter((path) => path && isLikelyVideoPath(path));
    if (nativePaths.length) {
      addVideoPaths(nativePaths);
    }

    const filesNeedingUpload = selectedFiles.filter((file) => !nativeFilePath(file));
    if (!filesNeedingUpload.length) return;

    if (!onUploadReferenceVideos) {
      addVideoPaths(filesNeedingUpload.map((file) => file.webkitRelativePath || file.name).filter(Boolean));
      return;
    }

    setIsUploading(true);
    try {
      const payload = await onUploadReferenceVideos(filesNeedingUpload);
      const uploadedQueue = payload?.activity?.reference_videos || payload?.uploaded_videos?.map((item) => item.file_path) || [];
      if (uploadedQueue.length) {
        setVideoQueue(uploadedQueue);
      }
    } finally {
      setIsUploading(false);
    }
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
    addVideoPaths(textPaths.filter(isLikelyVideoPath));
    void addFiles(event.dataTransfer.files);
  };

  const sendToStoryboard = () => {
    onGoCopywriting?.();
  };

  const canTranscribe = Boolean(project && videoQueue.length > 0);
  const canStartTranscribe = canTranscribe && validation?.ok && validation?.videos?.length === videoQueue.length && !isUploading;

  return (
    <div className="page-grid two-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>参考转写</h2>
            <p>{activity ? `当前活动：${activity.name}` : "请先创建或打开项目。"}</p>
          </div>
          <span className={savedText ? "status-pill ok" : "status-pill"}>{savedText ? "已转写" : "未转写"}</span>
        </div>
        <div className="source-preview">
          <div className="queue-header">
            <strong>产品资料</strong>
            <span>{profile.product_name || "未填写"}</span>
          </div>
          <p>{profile.rewrite_requirements || "新建项目或项目资料页填写转写/改写要求后，后续 AI 处理会读取这里。"}</p>
        </div>
        <div className="next-step">
          <strong>下一步：文案处理</strong>
          <span>{savedText ? "转写结果已保存到当前活动，可以进入文案改写和标题生成。" : "先导入视频并完成转写。"}</span>
          <button type="button" onClick={onGoCopywriting} disabled={!savedText}>
            去处理
          </button>
        </div>

        <div className="section-header compact">
          <div>
            <h3>导入参考视频</h3>
            <p>拖拽视频、选择文件/文件夹，或输入绝对路径。</p>
          </div>
          <div className="button-group">
            <button type="button" onClick={() => fileInputRef.current?.click()} disabled={!project || isUploading}>
              {isUploading ? "正在导入..." : "选择视频"}
            </button>
            <button type="button" onClick={() => folderInputRef.current?.click()} disabled={!project || isUploading}>
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
            void addFiles(event.target.files);
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
            void addFiles(event.target.files);
            event.target.value = "";
          }}
        />
        <div
          className={[isDragging ? "drop-zone dragging" : "drop-zone", isUploading ? "uploading" : ""].filter(Boolean).join(" ")}
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
          <span>浏览器导入会先复制到当前项目；也可以在下方粘贴绝对路径。</span>
        </div>
        {isUploading ? <p className="upload-hint">正在保存视频到当前项目，请稍等...</p> : null}
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
                    {validationByInput.has(path) ? (
                      <em className={validationByInput.get(path)?.exists ? "path-state ok" : "path-state missing"}>
                        {validationByInput.get(path)?.exists ? "路径有效" : "路径缺失"}
                      </em>
                    ) : (
                      <em className="path-state">等待校验</em>
                    )}
                    {validationByInput.get(path)?.reason ? (
                      <small>{validationByInput.get(path).reason}</small>
                    ) : null}
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
            disabled={!canStartTranscribe || isRunning}
          >
            开始转写
          </button>
          <button type="button" onClick={() => onCancelJob?.(job?.job_id)} disabled={!isRunning || !job?.job_id}>
            停止任务
          </button>
          <button type="button" onClick={clearVideos} disabled={!videoQueue.length || isRunning || isUploading}>
            清空队列
          </button>
        </div>
        {hasMissingPaths ? (
          <p className="error-text">有视频路径缺失，请输入完整路径后再开始转写。</p>
        ) : null}
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
            <h2>转写结果</h2>
            <p>{savedText ? `已保存 ${savedText.length} 字，正文不在此页展开。` : "尚未生成转写结果"}</p>
          </div>
        </div>
        <div className={savedText ? "result-card ready" : "result-card"}>
          <strong>{savedText ? "转写已完成" : "等待转写"}</strong>
          <span>{savedText ? "原始文案已写入当前活动，并会作为分镜分析的输入。" : "导入参考视频后点击开始转写。"}</span>
        </div>
        <div className="action-row">
          <button type="button" className="primary" onClick={sendToStoryboard} disabled={!project || !savedText.trim()}>
            发送到文案处理
          </button>
          <button type="button" onClick={() => navigator.clipboard?.writeText(savedText)} disabled={!savedText}>
            复制转写文案
          </button>
        </div>
        <div className="pipeline-grid">
          <article>
            <strong>当前活动</strong>
            <span>{activity?.name || "未选择"}</span>
          </article>
          <article>
            <strong>参考视频</strong>
            <span>{videoQueue.length} 个</span>
          </article>
          <article>
            <strong>转写字数</strong>
            <span>{savedText ? `${savedText.length} 字` : "未生成"}</span>
          </article>
        </div>
      </section>
    </div>
  );
}
