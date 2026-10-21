import { useState } from "react";
import { History, Redo2, RefreshCw, Undo2 } from "lucide-react";
import { historyRevision } from "../editor/shotEditing.js";
import { EditorButton } from "./EditorButton.jsx";

export function EditorHistory({ history, loading, error, dirty, busy, onReload, onAction }) {
  const [selected, setSelected] = useState("");
  const currentId = historyRevision(history);
  const entries = history?.entries || [];
  const selectedId = entries.some((entry) => entry.id === selected) ? selected : currentId || "";
  const blocked = busy || dirty || loading || !history;
  return <details className="editor-history">
    <summary>已保存版本 · {entries.length}{dirty ? " · 草稿未保存" : ""}</summary>
    <div className="history-toolbar">
      <EditorButton icon={Undo2} label="撤销已保存版本" disabled={blocked || !history?.can_undo} onClick={() => onAction("undo")} />
      <EditorButton icon={Redo2} label="重做已保存版本" disabled={blocked || !history?.can_redo} onClick={() => onAction("redo")} />
      <label className="full-label">版本
        <select aria-label="已保存版本" value={selectedId} disabled={busy || loading || !entries.length} onChange={(event) => setSelected(event.target.value)}>
          {!entries.length ? <option value="">暂无版本</option> : null}
          {entries.map((entry) => <option value={entry.id} key={entry.id}>{entry.id === currentId ? "当前 · " : ""}{entry.label || "镜头编辑"} · {entry.created_at || entry.id}</option>)}
        </select>
      </label>
      <EditorButton icon={History} label="从所选版本恢复为新版本" disabled={blocked || !selectedId || selectedId === currentId} onClick={() => onAction("restore", selectedId)}>恢复版本</EditorButton>
      <EditorButton icon={RefreshCw} label="刷新版本历史" disabled={busy || loading} onClick={onReload} />
    </div>
    {dirty ? <p className="muted">草稿未保存，暂不能撤销、重做或恢复已保存版本。</p> : null}
    {loading ? <p className="muted" role="status">正在读取版本历史...</p> : null}
    {error ? <p className="error-text" role="alert">{error}</p> : null}
  </details>;
}
