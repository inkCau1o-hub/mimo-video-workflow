import { useState } from "react";

export function MaterialLibraryPage({ activity, project, onTagMaterials }) {
  const [paths, setPaths] = useState("");
  const assets = activity?.material_assets || project?.material_assets || [];

  return (
    <div className="page-grid two-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>素材库</h2>
            <p>输入素材视频路径，后端抽帧并用视觉模型打标。</p>
          </div>
          <button type="button" className="primary" onClick={() => onTagMaterials(paths)}>
            打标素材
          </button>
        </div>
        <textarea
          value={paths}
          onChange={(event) => setPaths(event.target.value)}
          placeholder="/Users/mac/Desktop/ai/material.mp4"
        />
      </section>
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>素材摘要</h2>
            <p>{assets.length} 个素材资产</p>
          </div>
        </div>
        <div className="asset-list">
          {assets.map((asset) => (
            <article className="asset-row" key={asset.asset_id || asset.file_path}>
              <strong>{asset.material_type || "material"}</strong>
              <span>{asset.file_path}</span>
              <p>{asset.scene_summary}</p>
              <div className="tag-row">
                {(asset.visual_tags || []).map((tag) => <em key={tag}>{tag}</em>)}
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
