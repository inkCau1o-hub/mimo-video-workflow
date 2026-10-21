export function TopBar({ backendUrl, status, connected, busy, onCreateProject, onOpenExternalProject, onRefresh }) {
  return (
    <header className="top-bar">
      <div>
        <div className="app-title">MiMo Workflow</div>
      </div>
      <div className="top-actions">
        <div className={connected ? "status-pill ok" : "status-pill"}>
          {connected ? "Backend Online" : "Backend Offline"}
        </div>
        <div className="top-meta">
          <span>{status?.name || "未创建项目"}</span>
          <span>{backendUrl}</span>
        </div>
        <button type="button" onClick={onRefresh} disabled={busy}>刷新</button>
        <button type="button" onClick={onOpenExternalProject} disabled={busy}>打开外部项目</button>
        <button type="button" className="primary" onClick={onCreateProject} disabled={busy}>新建项目</button>
      </div>
    </header>
  );
}
