export function TopBar({ backendUrl, status, connected, onCreateProject, onRefresh }) {
  return (
    <header className="top-bar">
      <div>
        <div className="app-title">MiMo Workflow</div>
        <div className="app-subtitle">React prototype connected to local Python workflow API</div>
      </div>
      <div className="top-actions">
        <div className={connected ? "status-pill ok" : "status-pill"}>
          {connected ? "Backend Online" : "Backend Offline"}
        </div>
        <div className="top-meta">
          <span>{status?.name || "未创建项目"}</span>
          <span>{backendUrl}</span>
        </div>
        <button type="button" onClick={onRefresh}>刷新</button>
        <button type="button" className="primary" onClick={onCreateProject}>新建项目</button>
      </div>
    </header>
  );
}
