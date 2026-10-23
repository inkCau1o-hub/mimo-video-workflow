import test from "node:test";
import assert from "node:assert/strict";
import {
  STORAGE_KEY, describeModule, findModule, pollDelay, rememberModule, resolveInitialModule,
} from "../src/launcher/moduleState.js";

const memory = (initial = {}) => {
  const data = { ...initial };
  return { getItem: (key) => data[key] ?? null, setItem: (key, value) => { data[key] = String(value); }, data };
};
const broken = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } };

test("initial module: explicit param, legacy feed links, remembered choice, then home", () => {
  assert.equal(resolveInitialModule("?module=animation", memory()), "animation");
  assert.equal(resolveInitialModule("?module=home", memory({ [STORAGE_KEY]: "feed" })), "home");
  assert.equal(resolveInitialModule("?auto_open=latest&start_page=voiceover", memory()), "feed");
  assert.equal(resolveInitialModule("?auto_open=latest", memory({ [STORAGE_KEY]: "animation" })), "animation");
  assert.equal(resolveInitialModule("", memory({ [STORAGE_KEY]: "bogus" })), "home");
  assert.equal(resolveInitialModule("?module=nope", memory()), "home");
  assert.equal(resolveInitialModule("", broken), "home");
  assert.equal(resolveInitialModule("", undefined), "home");
});

test("only real features are remembered and storage failures are ignored", () => {
  const storage = memory();
  rememberModule(storage, "home");
  assert.equal(storage.data[STORAGE_KEY], undefined);
  rememberModule(storage, "animation");
  assert.equal(storage.data[STORAGE_KEY], "animation");
  assert.doesNotThrow(() => rememberModule(broken, "feed"));
});

test("module records map to clear UI states", () => {
  assert.equal(describeModule(undefined).state, "checking");
  const missing = describeModule({ available: false, reason: "未找到 AI 动画目录" });
  assert.deepEqual([missing.state, missing.ready, missing.message], ["unavailable", false, "未找到 AI 动画目录"]);
  assert.equal(describeModule({ available: true, status: "starting" }).state, "starting");
  assert.equal(describeModule({ available: true, status: "running", url: "" }).ready, false);
  assert.equal(describeModule({ available: true, status: "running", url: "http://127.0.0.1:5174/" }).ready, true);
  const failed = describeModule({ available: true, status: "failed", error: "进程已退出", log_path: "/x/animation.log" });
  assert.equal(failed.state, "failed");
  assert.match(failed.message, /进程已退出[\s\S]*日志：\/x\/animation\.log/);
  assert.equal(describeModule({ available: true, status: "stopped" }).label, "未启动");
});

test("helpers", () => {
  assert.equal(findModule([{ id: "feed" }, { id: "animation", status: "running" }], "animation").status, "running");
  assert.equal(findModule(null, "animation"), undefined);
  assert.equal(pollDelay({ status: "starting" }), 1000);
  assert.equal(pollDelay({ status: "running" }), 5000);
});
