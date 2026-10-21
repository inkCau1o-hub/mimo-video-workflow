import { useState } from "react";

export function OpenProjectModal({ open, onOpen, onClose }) {
  const [path, setPath] = useState("");
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  if (!open) return null;
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-panel narrow-modal" role="dialog" aria-modal="true" aria-label="打开外部项目">
        <form onSubmit={async (event) => {
          event.preventDefault();
          if (pending || !path.trim()) return;
          setPending(true);
          setFailed(false);
          try { setFailed(!await onOpen(path)); } finally { setPending(false); }
        }}>
          <div className="section-header"><h2>打开外部项目</h2></div>
          <label className="full-label">项目路径
            <input autoFocus required value={path} disabled={pending} onChange={(event) => setPath(event.target.value)} placeholder="项目文件夹或 project.json 的完整路径" />
          </label>
          {failed ? <p className="error-text" role="alert">打开失败，请检查项目路径和后端接口。详细错误见页面提示。</p> : null}
          <div className="modal-actions">
            <button type="button" disabled={pending} onClick={onClose}>取消</button>
            <button type="submit" className="primary" disabled={pending || !path.trim()}>{pending ? "正在打开..." : "打开项目"}</button>
          </div>
        </form>
      </section>
    </div>
  );
}
