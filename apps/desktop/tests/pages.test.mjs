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
    import { ReferencePage } from "./src/pages/ReferencePage.jsx";
    import { CopywritingPage } from "./src/pages/CopywritingPage.jsx";
    import { StoryboardPage } from "./src/pages/StoryboardPage.jsx";
    import { MaterialLibraryPage } from "./src/pages/MaterialLibraryPage.jsx";
    import { AssemblyPage } from "./src/pages/AssemblyPage.jsx";
    import { VoiceoverPage } from "./src/pages/VoiceoverPage.jsx";
    import { OpenProjectModal } from "./src/components/OpenProjectModal.jsx";
    import { JobProgress } from "./src/components/JobProgress.jsx";
    import { RenderedVideo } from "./src/components/RenderedVideo.jsx";
    const pages = { ReferencePage, CopywritingPage, StoryboardPage, MaterialLibraryPage, AssemblyPage, VoiceoverPage, OpenProjectModal, JobProgress, RenderedVideo };
    export const render = (name, props) => renderToStaticMarkup(React.createElement(pages[name], props));
    export function renderTree(name, props) {
      let tree;
      function CapturePage() {
        tree = pages[name](props);
        return tree;
      }
      renderToStaticMarkup(React.createElement(CapturePage));
      return tree;
    }
  `, resolveDir: root },
  bundle: true, platform: "node", format: "cjs", jsx: "automatic", write: false,
});
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const { render, renderTree } = module.exports;

test("rendered audio summary describes the selected export settings", () => {
  const html = render("RenderedVideo", { project: { project_id: "p1" }, activity: { metadata: { render_result: {
    output_path: "out.mp4", warnings: ["normalization skipped"], audio_settings: { normalize_voice: true, target_lufs: -16, voice_gain_db: -2,
      music_path: "media/music/fixture.wav", music_gain_db: -18, duck_music: true },
  } } } });
  assert.match(html, /-16 LUFS/);
  assert.match(html, /fixture.wav/);
  assert.match(html, /配音时降低/);
  assert.match(html, /normalization skipped/);
});
const project = { project_id: "p1", name: "Project", product_profile: {} };
const activity = {
  activity_id: "a1", name: "Activity", reference_videos: [],
  reference_copy: { source_text: "Editable reference" },
  copywriting: { rewrite: { text: "Editable final copy" } },
  storyboard_analysis: { shots: [{ shot_id: "s1", title: "Shot", script_text: "Script", tts_text: "Voice", duration_seconds: 4, metadata: { material_asset_id: "m1" } }] },
  material_assets: [{ asset_id: "m1", file_path: "C:\\material.mp4" }],
  voiceover_segments: [{ shot_id: "s1", segment_id: "v1", audio_path: "voice.mp3" }],
};
const props = { project, activity, backendUrl: "http://127.0.0.1:8765", jobs: [] };

function renderedPreviewUrl(result, job = {}, projectChanges = {}) {
  const html = render("RenderedVideo", {
    ...props, project: { ...project, ...projectChanges }, job,
    activity: { ...activity, metadata: { render_result: result } },
  });
  const source = html.match(/<video[^>]*\bsrc="([^"]+)"/);
  assert.ok(source, "the rendered result must have a video preview");
  return new URL(source[1].replaceAll("&amp;", "&"));
}

test("reference and copy screens contain editable saved text, not read-only summaries", () => {
  for (const [page, expected] of [["ReferencePage", "Editable reference"], ["CopywritingPage", "Editable final copy"]]) {
    const html = render(page, props);
    assert.match(html, new RegExp(`<textarea[^>]*>${expected}</textarea>`));
    assert.match(html, /保存文本/);
    assert.match(html, /已保存/);
  }
});

test("storyboard exposes script, voice, numeric duration and selected material", () => {
  const html = render("StoryboardPage", props);
  assert.match(html, /脚本文本/);
  assert.match(html, /配音文本/);
  assert.match(html, /type="number" min="0.1" step="0.1" value="4"/);
  assert.match(html, /value="m1" selected=""/);
  assert.match(html, /保存分镜/);
  assert.match(html, /<video[^>]*src="http:\/\/127.0.0.1:8765\/projects\/p1\/media\?path=/);
});

test("materials provide file upload, explicit selection and backend video preview", () => {
  const html = render("MaterialLibraryPage", props);
  assert.match(html, /type="file"/);
  assert.match(html, /multiple=""/);
  assert.match(html, /type="checkbox"/);
  assert.match(html, /上传不自动打标/);
  assert.match(html, /<video[^>]*\/projects\/p1\/media\?path=/);
});

test("uploaded but untagged materials remain visible after refreshing the snapshot", () => {
  const html = render("MaterialLibraryPage", { ...props, activity: { material_assets: [], metadata: { material_paths: ["restored.mp4"] } } });
  assert.match(html, /restored.mp4/);
  assert.match(html, /待打标/);
  assert.match(html, /<video/);
});

test("assembly has real render controls and only actual result produces download", () => {
  const empty = render("AssemblyPage", props);
  assert.match(empty, /导出视频/);
  assert.match(empty, /name="aspect_ratio"/);
  assert.match(empty, /字幕/);
  assert.match(empty, /仅项目目录内的 .mp4 路径/);
  assert.doesNotMatch(empty, /输入完整输出路径/);
  assert.doesNotMatch(empty, /下载视频/);
  const html = render("AssemblyPage", { ...props, activity: { ...activity, metadata: { render_result: { output_path: "done.mp4", width: 720, height: 1280, duration_seconds: 4, segment_count: 1 } } } });
  assert.match(html, /下载视频/);
  assert.match(html, /导出视频预览/);
  assert.match(html, /720 × 1280/);
});

test("assembly estimates narration plus gaps before a new plan exists", () => {
  const edited = structuredClone(activity);
  edited.voiceover_segments[0].duration_seconds = 3;
  edited.storyboard_analysis.shots[0].metadata.edit = { lead_in_seconds: .25, tail_out_seconds: .35 };
  const html = render("AssemblyPage", { ...props, activity: edited });
  assert.match(html, /预计时长/);
  assert.match(html, /3\.6s/);
  assert.doesNotMatch(html, />4s</);
});

test("failed jobs display their error even if logs exist", () => {
  const html = render("JobProgress", { job: { status: "failed", error: "encoder exploded", logs: ["started"] } });
  assert.match(html, /任务失败：encoder exploded/);
  assert.match(html, /started/);
  assert.match(html, /role="alert"/);
});

test("video downloads use backend attachments without buffering a blob", () => {
  const html = render("RenderedVideo", { ...props, activity: { ...activity, metadata: { render_result: { output_path: "成片 空格.mp4" } } } });
  assert.match(html, /<a href="[^"]*&amp;download=1" download="成片 空格.mp4">下载视频<\/a>/);
  assert.doesNotMatch(html, /blob:/);
});

test("invalidated outputs remain history without falsely showing a current render", () => {
  const html = render("RenderedVideo", { ...props, activity: { ...activity, metadata: { exports: [{ output_path: "old.mp4" }] } }, job: { status: "succeeded", result: { output_path: "old.mp4" } } });
  assert.match(html, /历史 1/);
  assert.match(html, /当前内容尚无已导出视频/);
  assert.doesNotMatch(html, /<video/);
});

test("rendered video version prefers the result's created_at", () => {
  const result = { output_path: "exports/completed.mp4", created_at: "2026-10-06T10:00:00", rendered_at: "other-render-time", generated_at: "other-generation-time" };
  const url = renderedPreviewUrl(result, { status: "running", updated_at: "poll-time" }, { updated_at: "project-time" });
  assert.equal(url.searchParams.get("v"), result.created_at);
  assert.equal(url.searchParams.get("path"), result.output_path);
});

test("polling and project updates never change an existing result's preview URL", () => {
  for (const timestamps of [{ created_at: "created" }, { rendered_at: "rendered" }, { generated_at: "generated" }, {}]) {
    const result = { output_path: "exports/completed.mp4", ...timestamps };
    const before = renderedPreviewUrl(result, { status: "queued", updated_at: "poll-1" }, { updated_at: "project-1" });
    for (const [index, status] of ["running", "succeeded", "failed"].entries()) {
      const after = renderedPreviewUrl(result, { status, updated_at: `poll-${index + 2}` }, { updated_at: `project-${index + 2}` });
      assert.equal(after.href, before.href);
    }
    if (!Object.keys(timestamps).length) assert.equal(before.searchParams.has("v"), false);
  }
});

test("a newly exported unique path changes the preview without any timestamp", () => {
  const before = renderedPreviewUrl({ output_path: "exports/first.mp4" });
  const after = renderedPreviewUrl({ output_path: "exports/second.mp4" });
  assert.notEqual(before.href, after.href);
  assert.equal(after.searchParams.get("path"), "exports/second.mp4");
  assert.equal(after.searchParams.has("v"), false);
});

test("external project dialog contains a required path field", () => {
  const html = render("OpenProjectModal", { open: true });
  assert.match(html, /role="dialog"/);
  assert.match(html, /打开外部项目/);
  assert.match(html, /required=""/);
  assert.match(html, /project.json/);
});

const partialVoiceover = {
  ...activity,
  storyboard_analysis: { shots: ["S01", "S02", "S03"].map((shot_id) => ({ shot_id, tts_text: `${shot_id} narration`, duration_seconds: 3 })) },
  voiceover_segments: ["S02", "S03"].map((shot_id) => ({ shot_id, segment_id: `voice-${shot_id}`, audio_path: `${shot_id}.mp3` })),
};

function pageButtons(tree) {
  if (Array.isArray(tree)) return tree.flatMap(pageButtons);
  if (!tree?.props) return [];
  return [...(tree.type === "button" ? [tree] : []), ...pageButtons(tree.props.children)];
}

test("partially voiced storyboards offer generation only for the missing shot", () => {
  const html = render("VoiceoverPage", { ...props, activity: partialVoiceover, onRegenerateVoiceover: () => {} });
  const shots = [...html.matchAll(/<article class="shot-row">([\s\S]*?)<\/article>/g)].map((match) => match[1]);
  assert.equal(shots.length, 3);
  assert.match(shots[0], /S01/);
  assert.match(shots[0], /未配音/);
  assert.match(shots[0], /生成此句/);
  for (const shot of shots.slice(1)) {
    assert.match(shot, /已配音/);
    assert.doesNotMatch(shot, /生成此句/);
  }
  assert.equal([...html.matchAll(/<audio\b/g)].length, 2);
  assert.equal([...html.matchAll(/重新生成/g)].length, 2);
});

test("a segment without an audio path still offers single-shot generation", () => {
  const html = render("VoiceoverPage", {
    ...props, onRegenerateVoiceover: () => {},
    activity: { ...partialVoiceover, voiceover_segments: [...partialVoiceover.voiceover_segments, { shot_id: "S01", segment_id: "empty", audio_path: "" }] },
  });
  const firstShot = html.match(/<article class="shot-row">([\s\S]*?)<\/article>/)[1];
  assert.match(firstShot, /未配音/);
  assert.match(firstShot, /生成此句/);
});

test("missing-shot generation calls the existing single-shot callback with the same settings", async () => {
  const singleCalls = [], fullCalls = [];
  const tree = renderTree("VoiceoverPage", {
    ...props, activity: partialVoiceover,
    onRegenerateVoiceover: async (payload) => singleCalls.push(payload),
    onGenerateVoiceovers: (payload) => fullCalls.push(payload),
  });
  const buttons = pageButtons(tree);
  const generateMissing = buttons.find((button) => button.props.children === "生成此句");
  assert.ok(generateMissing);
  assert.equal(generateMissing.props.disabled, false);
  await generateMissing.props.onClick();
  assert.deepEqual(singleCalls, [{ shot_id: "S01", voice: "", audio_format: "mp3", style_prompt: "自然、清晰、适合短视频口播" }]);
  assert.deepEqual(fullCalls, []);
  await buttons.find((button) => button.props.children === "重新生成").props.onClick();
  buttons.find((button) => button.props.children === "生成配音").props.onClick();
  const { shot_id, ...settings } = singleCalls[0];
  assert.deepEqual(singleCalls[1], { shot_id: "S02", ...settings });
  assert.deepEqual(fullCalls, [settings]);
  assert.deepEqual(partialVoiceover.voiceover_segments.map((segment) => segment.audio_path), ["S02.mp3", "S03.mp3"]);
});

test("single-shot generation follows running-job and project availability guards", () => {
  for (const input of [
    { project: null },
    { job: { status: "queued" } },
    { job: { status: "running" } },
  ]) {
    const buttons = pageButtons(renderTree("VoiceoverPage", { ...props, activity: partialVoiceover, onRegenerateVoiceover: () => {}, ...input }));
    for (const label of ["生成此句", "重新生成", "生成配音"]) {
      const button = buttons.find((item) => item.props.children === label);
      assert.ok(button, `${label} must remain visible`);
      assert.equal(button.props.disabled, true, `${label} must be disabled`);
    }
  }
  const buttons = pageButtons(renderTree("VoiceoverPage", { ...props, activity: partialVoiceover, job: { status: "failed" }, onRegenerateVoiceover: () => {} }));
  assert.equal(buttons.find((button) => button.props.children === "生成此句").props.disabled, false);
});

test("voiceover format choices only expose mp3 and wav", () => {
  const html = render("VoiceoverPage", props);
  assert.deepEqual([...html.matchAll(/<option\b[^>]*value="([^"]+)"/g)].map((match) => match[1]), ["mp3", "wav"]);
});
