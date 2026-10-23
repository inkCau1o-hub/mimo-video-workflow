// Pure launcher state helpers (no React), shared by the shell and its tests.

export const MODULE_IDS = Object.freeze(["home", "feed", "animation"]);
export const STORAGE_KEY = "mimo.launcher.module";

export const MODULE_INFO = Object.freeze({
  feed: Object.freeze({
    id: "feed",
    name: "信息流剪辑",
    description: "参考转写、文案、分镜、素材、配音到合成，做信息流短视频。",
  }),
  animation: Object.freeze({
    id: "animation",
    name: "AI 动画",
    description: "由导演、视觉、动画、代码、审查几个 Agent 协作生成 MG 动画。",
  }),
});

const readStorage = (storage) => {
  try {
    return storage?.getItem(STORAGE_KEY) || "";
  } catch {
    return "";
  }
};

// `?module=` wins; legacy feed links (`start_page`) open the feed; otherwise the last module, else home.
export function resolveInitialModule(search = "", storage) {
  const params = new URLSearchParams(search);
  const requested = params.get("module");
  if (MODULE_IDS.includes(requested)) return requested;
  if (params.has("start_page")) return "feed";
  const remembered = readStorage(storage);
  return remembered === "feed" || remembered === "animation" ? remembered : "home";
}

export function rememberModule(storage, moduleId) {
  if (moduleId !== "feed" && moduleId !== "animation") return;
  try {
    storage?.setItem(STORAGE_KEY, moduleId);
  } catch {
    /* Private windows may refuse storage; the launcher still works. */
  }
}

// Turn a backend module record into what the UI shows.
export function describeModule(record) {
  if (!record) return { state: "checking", label: "检查中", ready: false, message: "正在检查模块状态…" };
  if (!record.available) {
    return { state: "unavailable", label: "未安装", ready: false, message: record.reason || "模块不可用" };
  }
  switch (record.status) {
    case "running":
      return { state: "running", label: "运行中", ready: Boolean(record.url), message: "" };
    case "starting":
      return { state: "starting", label: "启动中", ready: false, message: "正在启动服务，首次启动可能需要几十秒…" };
    case "failed":
      return {
        state: "failed",
        label: "启动失败",
        ready: false,
        message: [record.error || "服务启动失败", record.log_path ? `日志：${record.log_path}` : ""].filter(Boolean).join("\n"),
      };
    default:
      return { state: "stopped", label: "未启动", ready: false, message: "" };
  }
}

export const findModule = (modules, id) => (Array.isArray(modules) ? modules.find((item) => item?.id === id) : undefined);

// Poll quickly while a module is starting, slowly otherwise.
export const pollDelay = (record) => (record?.status === "starting" ? 1000 : 5000);
