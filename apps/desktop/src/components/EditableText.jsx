import { useEffect, useState } from "react";
import { useEditableValue } from "../useEditableValue";

export function EditableText({ label, savedValue, onSave, disabled, onDirtyChange }) {
  const draft = useEditableValue(savedValue);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => { onDirtyChange?.(draft.dirty); }, [draft.dirty, onDirtyChange]);
  return (
    <div className="text-editor">
      <label className="full-label">{label}
        <textarea value={draft.value} disabled={disabled || saving} onChange={(event) => { draft.setValue(event.target.value); setMessage(""); }} />
      </label>
      {draft.conflict ? <p className="error-text">后台内容已更新，当前保留未保存修改；重新载入可查看最新内容。</p> : null}
      <div className="action-row">
        <button type="button" className="primary" disabled={disabled || saving || !draft.dirty || !draft.value.trim()} onClick={async () => {
          setSaving(true);
          const value = draft.value;
          try {
            const result = await onSave(value);
            if (result) { draft.saved(value); setMessage("已保存"); }
            else setMessage("未保存，请检查页面错误提示。");
          } finally { setSaving(false); }
        }}>{saving ? "正在保存..." : "保存文本"}</button>
        <button type="button" disabled={disabled || saving || !draft.dirty} onClick={() => { draft.reset(); setMessage(""); }}>重新载入</button>
        <span role="status" className="muted">{message || (draft.dirty ? "有未保存修改" : savedValue ? "已保存" : "尚无文本")}</span>
      </div>
    </div>
  );
}
