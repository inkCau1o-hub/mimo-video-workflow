import { useEffect, useRef, useState } from "react";
import { Check, Film, Play, Search } from "lucide-react";
import { EditorButton } from "./EditorButton";
import { ShotPreview } from "./ShotPreview";
import { shotEdit } from "../editor/shotEditing";
import { projectMediaUrl } from "../workflowData";

export function SegmentRecommendations({ shot, assets, targetDuration, backendUrl, projectId, activityId,
  aspectRatio, disabled, onRecommend, onApply, onGoMaterials }) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const request = useRef(0);
  const indexed = assets.filter((asset) => asset.metadata?.segment_index?.segments?.length);
  const key = JSON.stringify([projectId, activityId, shot, targetDuration, query,
    assets.map((asset) => [asset.asset_id, asset.file_path, asset.metadata?.segment_index?.index_id])]);
  useEffect(() => {
    request.current += 1;
    setResult(null); setError(""); setLoading(false); setPreview(null); setConfirmed(false);
    return () => { request.current += 1; };
  }, [key]);

  const search = async () => {
    const version = ++request.current;
    setLoading(true); setError(""); setResult(null); setPreview(null); setConfirmed(false);
    try {
      const response = await onRecommend(shot, { targetDuration, query, expectedScope: { projectId, activityId } });
      if (version !== request.current) return;
      if (!response || !Array.isArray(response.candidates)) throw new Error("项目内容已变化或查询未完成，请重新查询。");
      setResult(response);
    } catch (failure) {
      if (version === request.current) setError(`片段查询失败：${failure.message}`);
    } finally { if (version === request.current) setLoading(false); }
  };
  const selectedAsset = preview && assets.find((asset) => asset.asset_id === preview.asset_id);
  const previewEdit = preview && { ...shotEdit(shot), source_in: preview.source_in, source_out: preview.source_out };

  return <section className="segment-recommendations" aria-label="候选素材片段">
    <div className="section-header"><h3>候选片段</h3>
      <EditorButton icon={Film} label="打开素材片段分析" disabled={disabled || !onGoMaterials} onClick={onGoMaterials}>素材库</EditorButton>
    </div>
    <div className="segment-search">
      <input type="search" aria-label="片段检索词" value={query} maxLength={200} disabled={disabled} onChange={(event) => setQuery(event.target.value)} />
      <EditorButton icon={Search} label="查找候选片段" disabled={disabled || loading || !indexed.length || !onRecommend || !(targetDuration > 0)} onClick={search}>{loading ? "查询中..." : "查找片段"}</EditorButton>
    </div>
    {!indexed.length ? <p className="muted">尚无片段分析。</p> : null}
    {error ? <p className="error-text" role="alert">{error}</p> : null}
    {result?.message ? <p className="editor-warning" role="status">{result.message}</p> : null}
    {result?.unindexed_assets?.length ? <p className="muted">{result.unindexed_assets.length} 个素材尚未分析片段。</p> : null}
    {result?.partial_assets?.length ? <p className="editor-warning">{result.partial_assets.length} 个素材仅索引部分时间范围。</p> : null}
    {result?.unavailable_assets?.map((item) => <p className="error-text" key={item.asset_id}>{item.asset_id}：{item.reason}</p>)}
    <div className="segment-candidate-list">
      {(result?.candidates || []).map((candidate) => {
        const id = `${candidate.asset_id}:${candidate.segment_id}`;
        const active = preview?.asset_id === candidate.asset_id && preview?.segment_id === candidate.segment_id;
        const asset = assets.find((item) => item.asset_id === candidate.asset_id);
        return <article key={id} className={`segment-candidate ${active ? "selected" : ""}`}>
          {candidate.thumbnail_path ? <img alt={candidate.summary} src={projectMediaUrl(backendUrl, projectId, candidate.thumbnail_path)} loading="lazy" /> : null}
          <div className="segment-candidate-content">
            <strong>{String(asset?.file_path || candidate.asset_id).split(/[\\/]/).pop()}</strong>
            <span>{candidate.source_in.toFixed(2)} - {candidate.source_out.toFixed(2)} 秒 · 可用 {candidate.available_seconds.toFixed(2)} 秒</span>
            <p>{candidate.summary}</p>
            <p className="muted">匹配项：{candidate.matched_terms?.join("、") || "无"}</p>
            {candidate.missing_terms?.length ? <p className="editor-warning">缺少：{candidate.missing_terms.join("、")}</p> : null}
            {candidate.warnings?.map((warning) => <p className="editor-warning" key={warning}>{warning}</p>)}
            {candidate.uncertainties?.map((item, index) => <p className="muted" key={index}>{item}</p>)}
            <EditorButton icon={Play} label={`预览候选 ${candidate.segment_id} ${candidate.asset_id}`} disabled={disabled} onClick={() => { setPreview(candidate); setConfirmed(false); }}>预览选段</EditorButton>
          </div>
        </article>;
      })}
    </div>
    {preview && selectedAsset ? <div className="candidate-preview" aria-label="候选片段预览">
      <ShotPreview key={`${preview.asset_id}:${preview.segment_id}`} backendUrl={backendUrl} projectId={projectId} asset={selectedAsset} edit={previewEdit} aspectRatio={aspectRatio} />
      {preview.requires_confirmation ? <label className="checkbox-control"><input type="checkbox" aria-label="已预览并确认候选片段" checked={confirmed} disabled={disabled} onChange={(event) => setConfirmed(event.target.checked)} />已预览并确认候选片段</label> : null}
      <EditorButton icon={Check} label="采用候选片段" disabled={disabled || (preview.requires_confirmation && !confirmed)} onClick={() => onApply(preview, confirmed)}>采用片段</EditorButton>
    </div> : null}
  </section>;
}
