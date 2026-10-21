import { useEffect, useState } from "react";
import { projectMediaUrl } from "../workflowData";

export function VideoPreview({ backendUrl, projectId, path, version, downloadable = false }) {
  const url = projectMediaUrl(backendUrl, projectId, path, version);
  const [error, setError] = useState("");
  useEffect(() => { setError(""); }, [url]);
  if (!url) return null;
  return (
    <div className="video-preview">
      <video key={url} controls preload="metadata" src={url} aria-label={downloadable ? "导出视频预览" : "素材预览"} onError={() => setError("视频预览不可用，请检查后端媒体文件或浏览器格式支持。") } />
      {error ? <p className="error-text" role="alert">{error}</p> : null}
      {downloadable ? <div className="action-row">
        <a href={`${url}&download=1`} download={String(path).split(/[\\/]/).pop() || "video.mp4"}>下载视频</a>
        <a href={url} target="_blank" rel="noreferrer">打开视频</a>
      </div> : null}
    </div>
  );
}
