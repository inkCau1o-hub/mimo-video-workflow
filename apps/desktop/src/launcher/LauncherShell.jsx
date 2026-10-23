import { useCallback, useEffect, useMemo, useState } from "react";
import App from "../App.jsx";
import { ApiClient, DEFAULT_BACKEND_URL } from "../api/client";
import { HomePage } from "../pages/HomePage.jsx";
import { AnimationModule } from "./AnimationModule.jsx";
import { MODULE_INFO, rememberModule, resolveInitialModule } from "./moduleState.js";

const safeStorage = () => {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
};

// One window, two independent features. Visited features stay mounted (hidden) so
// switching back keeps their state.
export function LauncherShell() {
  const api = useMemo(() => new ApiClient(DEFAULT_BACKEND_URL), []);
  const storage = useMemo(safeStorage, []);
  const [active, setActive] = useState(() => resolveInitialModule(window.location.search, storage));
  const [visited, setVisited] = useState(() => new Set([active]));
  const [modules, setModules] = useState(null);

  const refreshModules = useCallback(() => {
    api.get("/modules").then((data) => setModules(data.modules)).catch(() => setModules((current) => current));
  }, [api]);

  useEffect(() => { refreshModules(); }, [refreshModules]);

  const open = useCallback((moduleId) => {
    setActive(moduleId);
    setVisited((current) => (current.has(moduleId) ? current : new Set([...current, moduleId])));
    rememberModule(storage, moduleId);
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("module", moduleId);
      window.history.replaceState(null, "", url);
    } catch {
      /* URL updates are a convenience only. */
    }
    if (moduleId !== "feed") refreshModules();
  }, [refreshModules, storage]);

  return (
    <div className="launcher-shell">
      {active !== "home" ? (
        <nav className="module-bar" aria-label="功能切换">
          <button type="button" className="module-home" onClick={() => open("home")}>首页</button>
          {[MODULE_INFO.feed, MODULE_INFO.animation].map((info) => (
            <button key={info.id} type="button" aria-current={active === info.id ? "page" : undefined}
              className={active === info.id ? "module-tab active" : "module-tab"} onClick={() => open(info.id)}>
              {info.name}
            </button>
          ))}
        </nav>
      ) : null}
      {active === "home" ? <HomePage modules={modules} onOpen={open} /> : null}
      {visited.has("feed") ? (
        <div className="module-view" hidden={active !== "feed"}><App /></div>
      ) : null}
      {visited.has("animation") ? (
        <div className="module-view animation" hidden={active !== "animation"}>
          <AnimationModule api={api} modules={modules} onModules={setModules} />
        </div>
      ) : null}
    </div>
  );
}
