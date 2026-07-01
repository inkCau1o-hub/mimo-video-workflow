import { useState } from "react";

export function ReferencePage({ project, onSetReferenceText, onTranscribeReference }) {
  const [referenceText, setReferenceText] = useState("");
  const [videoPaths, setVideoPaths] = useState("");
  const savedText = project?.reference_copy?.source_text || "";

  return (
    <div className="page-grid two-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>参考转写</h2>
            <p>把参考视频转成文案，或直接粘贴参考文案。</p>
          </div>
          <button type="button" className="primary" onClick={() => onSetReferenceText(referenceText)}>
            保存当前文本
          </button>
        </div>
        <textarea
          value={referenceText}
          onChange={(event) => setReferenceText(event.target.value)}
          placeholder="粘贴参考文案，或先输入视频路径后触发转写。"
        />
        <div className="section-header compact">
          <div>
            <h3>参考视频路径</h3>
            <p>实验阶段先输入绝对路径，每行一个。</p>
          </div>
          <button type="button" onClick={() => onTranscribeReference(videoPaths)}>
            转写参考
          </button>
        </div>
        <textarea
          className="small-textarea"
          value={videoPaths}
          onChange={(event) => setVideoPaths(event.target.value)}
          placeholder="/Users/mac/Desktop/ai/reference.mp4"
        />
      </section>
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>已保存参考</h2>
            <p>{savedText ? `${savedText.length} 字` : "尚未写入项目"}</p>
          </div>
        </div>
        <pre className="readout">{savedText || "等待参考文案..."}</pre>
      </section>
    </div>
  );
}
