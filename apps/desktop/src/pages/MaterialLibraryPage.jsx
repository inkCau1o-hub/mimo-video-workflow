import { useRef, useState } from "react";
import { pickList } from "../workflowData";
import { isTerminalJob } from "../api/jobMonitor";
import { JobProgress } from "../components/JobProgress";
import { VideoPreview } from "../components/VideoPreview";
import { Film } from "lucide-react";
import { EditorButton } from "../components/EditorButton";
import { projectMediaUrl } from "../workflowData";

export function MaterialLibraryPage({ activity, project, backendUrl, job, onTagMaterials, onUploadMaterials, onIndexSegments }) {
  const [paths, setPaths] = useState("");
  const [uploaded, setUploaded] = useState([]);
  const [selected, setSelected] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [windowSeconds, setWindowSeconds] = useState("8");
  const inputRef = useRef(null);
  const assets = pickList(activity, project, "material_assets");
  const uploadedByPath = new Map((activity?.metadata?.material_paths || []).map((file_path) => [file_path, { file_path }]));
  for (const item of uploaded) uploadedByPath.set(item.file_path, item);
  const entries = [...assets, ...[...uploadedByPath.values()].filter((item) => !assets.some((asset) => asset.file_path === item.file_path))];
  const isRunning = Boolean(job && !isTerminalJob(job));
  const tagPaths = [...new Set([...selected, ...paths.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)])];
  const selectedAssetIds = assets.filter((asset) => selected.includes(asset.file_path)).map((asset) => asset.asset_id);

  return (
    <div className="page-grid two-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>素材库</h2>
            <p>{assets.length} 个素材，{selected.length} 个已选择</p>
          </div>
          <button type="button" className="primary" disabled={!project || !tagPaths.length || isRunning || uploading} onClick={() => onTagMaterials(tagPaths.join("\n"))}>
            打标素材
          </button>
        </div>
        <div className="action-row">
          <button type="button" disabled={!project || uploading || isRunning} onClick={() => inputRef.current?.click()}>{uploading ? "正在上传..." : "选择素材（视频或图片）"}</button>
          <span className="muted">上传不自动打标</span>
        </div>
        <input ref={inputRef} className="hidden-input" type="file" accept="video/*,image/*,.mp4,.mov,.m4v,.avi,.mkv,.webm,.heic,.heif,.jpg,.jpeg,.png,.webp" multiple onChange={async (event) => {
          const files = Array.from(event.target.files || []);
          event.target.value = "";
          if (!files.length) return;
          setUploading(true);
          try {
            const payload = await onUploadMaterials(files);
            if (payload?.uploaded_videos) {
              setUploaded((items) => [...items, ...payload.uploaded_videos]);
              setSelected((items) => [...new Set([...items, ...payload.uploaded_videos.map((item) => item.file_path)])]);
            }
          } finally { setUploading(false); }
        }} />
        {uploading ? <progress className="job-meter" aria-label="素材上传进度" /> : null}
        <textarea
          aria-label="素材完整路径"
          value={paths}
          onChange={(event) => setPaths(event.target.value)}
          placeholder="每行一个素材（视频或图片）完整路径；图片导入时自动转正并转成 sRGB。"
        />
        <JobProgress job={job} title={job?.job_type === "materials.index" ? "素材片段分析进度" : "素材打标进度"} />
        <div className="segment-index-controls">
          <label className="full-label">分析片段长度（秒）<input type="number" min="2" max="30" step="1" value={windowSeconds} disabled={isRunning} onChange={(event) => setWindowSeconds(event.target.value)} /></label>
          <EditorButton icon={Film} label="分析选中素材的片段" disabled={!onIndexSegments || !selectedAssetIds.length || uploading || isRunning || !Number.isFinite(Number(windowSeconds)) || Number(windowSeconds) < 2 || Number(windowSeconds) > 30}
            onClick={() => onIndexSegments({ asset_ids: selectedAssetIds, window_seconds: Number(windowSeconds) })}>分析片段</EditorButton>
        </div>
      </section>
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>素材摘要</h2>
            <p>{assets.length} 个素材资产</p>
          </div>
        </div>
        <div className="asset-list">
          {!entries.length ? <div className="empty-state">尚无素材视频。</div> : null}
          {entries.map((asset) => (
            <article className="asset-row" key={asset.asset_id || asset.file_path}>
              <label className="checkbox-control"><input type="checkbox" checked={selected.includes(asset.file_path)} onChange={(event) => setSelected((items) => event.target.checked ? [...items, asset.file_path] : items.filter((item) => item !== asset.file_path))} />
                <strong>{asset.material_type || "视频素材"}{asset.asset_id ? ` · ${asset.asset_id}` : " · 待打标"}</strong>
              </label>
              <span>{asset.file_path}</span>
              <VideoPreview backendUrl={backendUrl} projectId={project?.project_id} path={asset.file_path} />
              <p>{asset.scene_summary}</p>
              <div className="tag-row">
                {(asset.visual_tags || []).map((tag) => <em key={tag}>{tag}</em>)}
              </div>
              {asset.metadata?.segment_index ? <details className="material-segments">
                <summary>素材片段 · {asset.metadata.segment_index.segments.length}</summary>
                {asset.metadata.segment_index.coverage_ratio < .999 ? <p className="editor-warning">仅索引部分时间范围，未分析区间仍有遗漏。</p> : null}
                {asset.metadata.segment_index.segments.map((segment) => <div className="material-segment" key={segment.segment_id}>
                  <img src={projectMediaUrl(backendUrl, project?.project_id, segment.thumbnail_path)} alt={segment.summary} loading="lazy" />
                  <div><strong>{segment.source_in.toFixed(2)} - {segment.source_out.toFixed(2)} 秒</strong><p>{segment.summary}</p>
                    <span className="muted">{segment.visual_tags.join("、")}</span>
                    {segment.burned_in_subtitles !== "no" ? <p className="editor-warning">{segment.burned_in_subtitles === "yes" ? "原素材带字幕" : "原字幕情况不确定"}</p> : null}
                  </div>
                </div>)}
              </details> : null}
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
