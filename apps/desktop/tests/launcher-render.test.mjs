import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const root = fileURLToPath(new URL("../", import.meta.url));

const bundle = await build({
  stdin: { contents: `
    import React from "react";
    import { renderToStaticMarkup } from "react-dom/server";
    import { HomePage } from "./src/pages/HomePage.jsx";
    import { AnimationModule } from "./src/launcher/AnimationModule.jsx";
    const parts = { HomePage, AnimationModule };
    export const render = (name, props) => renderToStaticMarkup(React.createElement(parts[name], props));
  `, resolveDir: root },
  bundle: true, platform: "node", format: "cjs", jsx: "automatic", write: false,
});
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const { render } = module.exports;

const api = { get: async () => ({ modules: [] }), post: async () => ({}) };
const feed = { id: "feed", name: "信息流剪辑", available: true, status: "running" };

test("home shows both features and explains a missing AI 动画 install", () => {
  const html = render("HomePage", {
    modules: [feed, { id: "animation", available: false, reason: "依赖未安装（缺少 node_modules）" }],
    onOpen() {},
  });
  assert.match(html, /进入信息流剪辑/);
  assert.match(html, /进入AI 动画/);
  assert.match(html, /依赖未安装/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>进入AI 动画/);
});

test("home enables AI 动画 when it is installed", () => {
  const html = render("HomePage", { modules: [feed, { id: "animation", available: true, status: "stopped" }], onOpen() {} });
  assert.doesNotMatch(html, /<button[^>]*disabled=""[^>]*>进入AI 动画/);
  assert.match(html, /未启动/);
});

test("animation module shows progress, failure details, then embeds the workbench", () => {
  const starting = render("AnimationModule", { api, onModules() {}, modules: [feed, { id: "animation", available: true, status: "starting" }] });
  assert.match(starting, /启动中/);
  const failed = render("AnimationModule", {
    api, onModules() {},
    modules: [feed, { id: "animation", available: true, status: "failed", error: "进程已退出（代码 1）", log_path: "/tmp/animation.log" }],
  });
  assert.match(failed, /启动失败/);
  assert.match(failed, /\/tmp\/animation\.log/);
  assert.match(failed, /重新启动/);
  const ready = render("AnimationModule", {
    api, onModules() {}, modules: [feed, { id: "animation", available: true, status: "running", url: "http://127.0.0.1:5174/" }],
  });
  assert.match(ready, /<iframe[^>]*src="http:\/\/127\.0\.0\.1:5174\/"/);
  assert.doesNotMatch(ready, /重启 AI 动画/);
  const stale = render("AnimationModule", {
    api, onModules() {},
    modules: [feed, { id: "animation", available: true, status: "running", url: "http://127.0.0.1:5174/", restart_required: true }],
  });
  assert.match(stale, /模型设置已更改/);
  assert.match(stale, /<iframe/);
});
