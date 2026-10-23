import { useMemo, useState } from "react";
import { pickAssemblyPlan, pickList, pickStoryboardAnalysis, referenceText as selectReferenceText } from "../workflowData";
import { isTerminalJob } from "../api/jobMonitor";
import { JobProgress } from "../components/JobProgress";
import { RenderedVideo } from "../components/RenderedVideo";
import { AudioSettings, DEFAULT_AUDIO_SETTINGS } from "../components/AudioSettings";
import { shotTiming } from "../editor/shotEditing";

function readyLabel(ready) {
  return ready ? "已就绪" : "未就绪";
}

function indexBy(items, key) {
  const result = new Map();
  for (const item of items || []) {
    if (item?.[key]) result.set(item[key], item);
  }
  return result;
}

export function AssemblyPage({ activity, project, backendUrl, job, renderJob, onBuildAssemblyPlan, onRenderVideo, onCancelJob,
  onSaveAudioSettings, onUploadMusic, onGenerateCover, onSetNarrationMode, coverJob, busy = false }) {
  const [outputPath, setOutputPath] = useState("");
  const [aspectRatio, setAspectRatio] = useState("9:16");
  const [subtitles, setSubtitles] = useState(true);
  const [audioDraft, setAudioDraft] = useState(null);
  const [audioBase, setAudioBase] = useState("");
  const [audioBusy, setAudioBusy] = useState(false);
  const [audioMessage, setAudioMessage] = useState("");
  const audioRevision = activity?.metadata?.audio_revision || "";
  const audioSettings = audioDraft || { ...DEFAULT_AUDIO_SETTINGS, ...activity?.metadata?.audio_settings };
  const audioConflict = audioDraft !== null && audioBase !== audioRevision;
  const scope = { projectId: project?.project_id, activityId: activity?.activity_id };
  const changeAudio = (change) => {
    if (audioDraft === null) setAudioBase(audioRevision);
    setAudioDraft({ ...audioSettings, ...change }); setAudioMessage("");
  };
  const referenceText = selectReferenceText(activity, project);
  const analysis = pickStoryboardAnalysis(activity, project);
  const assets = pickList(activity, project, "material_assets");
  const segments = pickList(activity, project, "voiceover_segments");
  // Activities saved before the mode existed narrate with voiceover.
  const withNarration = activity?.narration_mode !== "none";
  const plan = pickAssemblyPlan(activity, project);
  const command = plan?.metadata?.renderer?.command;
  const canBuild = Boolean(project && analysis?.shots?.length);
  const isRunning = busy || Boolean(job && !isTerminalJob(job));
  const canRender = canBuild && assets.length > 0 && (segments.length > 0 || !withNarration) && !isRunning && !audioBusy && audioDraft === null;
  const assetById = useMemo(() => indexBy(assets, "asset_id"), [assets]);
  const segmentById = useMemo(() => indexBy(segments, "segment_id"), [segments]);
  const segmentByShot = useMemo(() => indexBy(segments, "shot_id"), [segments]);
  const planItemByShot = useMemo(() => indexBy(plan?.items || [], "shot_id"), [plan]);
  const validation = plan?.metadata?.renderer?.validation;

  return (
    <div className="page-grid two-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>合成</h2>
            <p>{activity?.name || "等待选择活动"}</p>
          </div>
        </div>
        <div className="pipeline-grid">
          <div><span>转写文案</span><strong>{readyLabel(Boolean(referenceText.trim()))}</strong></div>
          <div><span>分镜脚本</span><strong>{analysis?.shots?.length || 0} 条</strong></div>
          <div><span>配音音频</span><strong>{withNarration ? `${segments.length} 条` : "无旁白"}</strong></div>
          <div><span>素材资产</span><strong>{assets.length} 个</strong></div>
        </div>
        <label className="full-label">
          旁白
          <select aria-label="旁白" value={withNarration ? "voiceover" : "none"} disabled={isRunning || !onSetNarrationMode}
            onChange={(event) => onSetNarrationMode?.(event.target.value)}>
            <option value="voiceover">使用配音</option>
            <option value="none">无旁白，只用音乐（时长按分镜）</option>
          </select>
        </label>
        <label className="full-label">
          输出视频路径
          <input
            value={outputPath}
            onChange={(event) => setOutputPath(event.target.value)}
            placeholder="仅项目目录内的 .mp4 路径；留空自动命名。"
          />
          <span className="muted">相对路径从项目目录起算，不覆盖已有文件。其他位置请下载后另存。</span>
        </label>
        <fieldset className="render-settings" disabled={isRunning}>
          <legend>导出设置</legend>
          <div className="segmented-control" role="group" aria-label="画面比例">
            {["9:16", "16:9"].map((ratio) => <label key={ratio} className={aspectRatio === ratio ? "selected" : ""}>
              <input type="radio" name="aspect_ratio" value={ratio} checked={aspectRatio === ratio} onChange={() => setAspectRatio(ratio)} />
              {ratio === "9:16" ? "竖屏 9:16" : "横屏 16:9"}
            </label>)}
          </div>
          <label className="checkbox-control"><input type="checkbox" checked={subtitles} onChange={(event) => setSubtitles(event.target.checked)} />字幕</label>
        </fieldset>
        <AudioSettings settings={audioSettings} musicAssets={activity?.metadata?.music_assets || []}
          backendUrl={backendUrl} projectId={project?.project_id} disabled={isRunning || !project} saving={audioBusy}
          dirty={audioDraft !== null} conflict={audioConflict} message={audioMessage} onChange={changeAudio}
          onReset={() => { setAudioDraft(null); setAudioMessage(""); }}
          onSave={onSaveAudioSettings ? async () => {
            setAudioBusy(true);
            try {
              const result = await onSaveAudioSettings(audioDraft, { baseRevision: audioBase, expectedScope: scope });
              if (result) { setAudioDraft(null); setAudioMessage("声音设置已保存"); }
              else setAudioMessage("保存未完成，声音草稿已保留。");
            } catch { setAudioMessage("保存失败，声音草稿已保留。"); }
            finally { setAudioBusy(false); }
          } : undefined}
          onUpload={onUploadMusic ? async (file) => {
            setAudioBusy(true);
            try {
              const result = await onUploadMusic(file, { expectedScope: scope });
              if (result?.uploaded_music) changeAudio({ music_path: result.uploaded_music.file_path });
            } catch { setAudioMessage("音乐导入失败，原设置未改变。"); }
            finally { setAudioBusy(false); }
          } : undefined} />
        <div className="action-row">
          <button type="button" onClick={() => onBuildAssemblyPlan(outputPath)} disabled={!canBuild || isRunning}>生成合成计划</button>
          <button type="button" className="primary" onClick={() => onRenderVideo({
            aspect_ratio: aspectRatio, subtitles, width: aspectRatio === "9:16" ? 720 : 1280,
            audio_revision: audioRevision,
            height: aspectRatio === "9:16" ? 1280 : 720,
            ...(outputPath.trim() ? { output_path: outputPath.trim() } : {}),
          })} disabled={!canRender}>导出视频</button>
        </div>
        <JobProgress job={job} title="合成 / 导出进度" onCancelJob={onCancelJob} />
        <div className="assembly-table">
          <div className="assembly-row header">
            <span>镜头</span>
            <span>画面 / 文案</span>
            <span>素材</span>
            <span>配音</span>
            <span>预计时长</span>
          </div>
          {(analysis?.shots || []).length === 0 ? (
            <div className="empty-state">等待分镜脚本。</div>
          ) : (
            (analysis?.shots || []).map((shot) => {
              const planItem = planItemByShot.get(shot.shot_id);
              const materialId = shot.metadata?.material_asset_id || planItem?.material_asset_id;
              const asset = assetById.get(materialId);
              const segment = withNarration ? segmentById.get(planItem?.voiceover_segment_id) || segmentByShot.get(shot.shot_id) : null;
              const duration = planItem?.duration_seconds ?? shotTiming(shot, asset, segment?.audio_path ? segment : null).target;
              return (
                <article className="assembly-row" key={shot.shot_id}>
                  <strong>{shot.shot_id}</strong>
                  <div>
                    <strong>{shot.title || "未命名镜头"}</strong>
                    <p>{shot.tts_text || shot.script_text}</p>
                  </div>
                  <div>
                    <strong>
                      {asset?.material_type || "素材"}
                      {materialId ? ` · ${materialId}` : ""}
                    </strong>
                    <p>{asset?.scene_summary || (shot.material_requirements || []).join("、") || "等待素材"}</p>
                  </div>
                  <div>
                    <strong>{segment?.segment_id || (withNarration ? "待生成" : "无旁白")}</strong>
                    <p>{segment?.audio_path || (withNarration ? "等待配音" : "按分镜时长")}</p>
                  </div>
                  <span>
                    {Number.isFinite(duration) ? Number(duration.toFixed(2)) : "--"}s
                  </span>
                </article>
              );
            })
          )}
        </div>
      </section>
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>导出结果</h2>
          </div>
        </div>
        <RenderedVideo activity={activity} project={project} backendUrl={backendUrl} job={renderJob} onGenerateCover={onGenerateCover} coverJob={coverJob} onCancelJob={onCancelJob} busy={busy} />
        <details>
          <summary>合成命令</summary>
          <pre className="readout">{Array.isArray(command) ? command.join(" ") : command || "等待生成合成计划..."}</pre>
        </details>
        {validation?.warnings?.length ? (
          <div className="warning-list">
            {validation.warnings.map((warning) => (
              <div key={warning}>{warning}</div>
            ))}
          </div>
        ) : null}
      </section>
    </div>
  );
}
