import { useEffect, useRef, useState } from "react";
import { describeModule, findModule, pollDelay } from "./moduleState.js";

// Shows the AI 动画 workbench in this window. Its UI and service run separately;
// this component only asks the backend to start it and embeds its page.
export function AnimationModule({ api, modules, onModules }) {
  const record = findModule(modules, "animation");
  const view = describeModule(record);
  const [requestError, setRequestError] = useState("");
  const requested = useRef(false);

  useEffect(() => {
    if (requested.current || !record?.available || record.status === "running" || record.status === "starting") return;
    requested.current = true;
    api.post("/modules/animation/start")
      .then(() => api.get("/modules"))
      .then((data) => onModules(data.modules))
      .catch((error) => setRequestError(error.message || String(error)));
  }, [api, onModules, record]);

  useEffect(() => {
    if (view.state !== "starting") return undefined;
    const timer = setTimeout(() => {
      api.get("/modules").then((data) => onModules(data.modules)).catch(() => {});
    }, pollDelay(record));
    return () => clearTimeout(timer);
  }, [api, onModules, record, view.state]);

  const retry = () => {
    requested.current = false;
    setRequestError("");
    api.post("/modules/animation/start")
      .then(() => api.get("/modules"))
      .then((data) => onModules(data.modules))
      .catch((error) => setRequestError(error.message || String(error)));
  };

  const restart = () => {
    setRequestError("");
    api.post("/modules/animation/restart")
      .then(() => api.get("/modules"))
      .then((data) => onModules(data.modules))
      .catch((error) => setRequestError(error.message || String(error)));
  };

  if (view.ready) {
    return (
      <>
        {record.restart_required ? (
          <div className="module-notice" role="status">
            <span>模型设置已更改，重启 AI 动画后生效。</span>
            <button type="button" onClick={restart}>重启 AI 动画</button>
          </div>
        ) : null}
        <iframe className="module-frame" title="AI 动画" src={record.url} />
      </>
    );
  }
  const message = requestError || view.message;
  return (
    <div className="module-placeholder" role="status">
      <h2>AI 动画 · {view.label}</h2>
      {message ? <pre className="module-message">{message}</pre> : null}
      {view.state === "failed" || requestError ? <button type="button" onClick={retry}>重新启动</button> : null}
    </div>
  );
}
