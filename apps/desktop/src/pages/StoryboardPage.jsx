import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Copy, Mic, Redo2, RefreshCw, RotateCcw, Save, Trash2, Undo2 } from "lucide-react";
import { finalCopyText, pickStoryboardAnalysis, pickList, updateShot } from "../workflowData";
import { isTerminalJob } from "../api/jobMonitor";
import { JobProgress } from "../components/JobProgress";
import { EditorButton } from "../components/EditorButton";
import { EditorHistory } from "../components/EditorHistory";
import { ShotInspector } from "../components/ShotInspector";
import { SubtitleCorrection } from "../components/SubtitleCorrection";
import { SegmentRecommendations } from "../components/SegmentRecommendations";
import { useShotDraft } from "../editor/useShotDraft";
import { applySegmentCandidate, deleteShot, duplicateShot, editShotSettings, editShotFraming, historyRevision, moveShot, replaceShotMaterial, serializeShots, shotErrors, shotTiming, voiceForShot } from "../editor/shotEditing";

export function StoryboardPage({ activity, project, backendUrl, job, onAnalyzeStoryboard, onSaveShots,
  history, historyLoading = false, historyError = "", onLoadHistory, onHistoryAction, onGoVoiceover, onRefreshProject,
  busy = false, editorJobRunning = false, onRecommendSegments, onGoMaterials }) {
  const [duration, setDuration] = useState("30");
  const [shotCount, setShotCount] = useState("5");
  const [selectedId, setSelectedId] = useState("");
  const [aspectRatio, setAspectRatio] = useState("9:16");
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState("");
  const [measurements, setMeasurements] = useState({});
  const mounted = useRef(false);
  const inFlight = useRef(false);
  const analysis = pickStoryboardAnalysis(activity, project);
  const savedShots = analysis?.shots || [];
  const scope = `${project?.project_id || ""}:${activity?.activity_id || ""}`;
  const draft = useShotDraft(savedShots, historyRevision(history), scope);
  const assets = pickList(activity, project, "material_assets");
  const segments = pickList(activity, project, "voiceover_segments");
  const isRunning = editorJobRunning || Boolean(job && !isTerminalJob(job));
  const disabled = saving || busy || isRunning || !project;
  const inputText = finalCopyText(activity, project);
  const canAnalyze = Boolean(project && inputText.trim() && Number(duration) > 0 && Number.isInteger(Number(shotCount)) && Number(shotCount) > 0 && !draft.dirty && !disabled);
  const selected = draft.value.find((shot) => shot.shot_id === selectedId) || draft.value[0];
  const selectedIndex = draft.value.indexOf(selected);
  const expectedScope = { projectId: project?.project_id, activityId: activity?.activity_id || "" };

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (project && !history && !busy) void onLoadHistory?.();
  }, [project, history, busy, onLoadHistory]);

  const shotInfo = (shot) => {
    const asset = assets.find((item) => item.asset_id === shot.metadata?.material_asset_id);
    const voice = voiceForShot(shot, draft.value, segments, savedShots);
    const mediaKey = `media:${asset?.asset_id}:${asset?.file_path}`;
    const voiceKey = `voice:${voice?.segment_id}:${voice?.audio_path}:${voice?.metadata?.generated_at || ""}`;
    const timing = shotTiming(shot, asset, voice, { media: measurements[mediaKey], voice: measurements[voiceKey] });
    return { asset, voice, timing, mediaKey, voiceKey, errors: shotErrors(shot, timing) };
  };
  const invalidCount = draft.value.filter((shot) => shotInfo(shot).errors.length).length;
  const selectedInfo = selected ? shotInfo(selected) : null;
  const change = (transform) => { if (!disabled) { draft.setValue(transform); setSaveMessage(""); } };
  const measure = (key, value) => setMeasurements((previous) => previous[key] === value ? previous : { ...previous, [key]: value });

  const save = async () => {
    if (inFlight.current || disabled || !draft.dirty || draft.conflict || invalidCount || !history || !draft.value.length) return;
    inFlight.current = true;
    setSaving(true);
    setSaveMessage("");
    try {
      const shots = serializeShots(draft.value);
      const payload = await onSaveShots(shots, { baseRevision: draft.revision, label: "编辑镜头", expectedScope });
      if (!mounted.current) return;
      if (payload?.project) {
        const savedActivity = payload.project.activities?.find((item) => item.activity_id === expectedScope.activityId);
        const returnedShots = pickStoryboardAnalysis(savedActivity, payload.project)?.shots || shots;
        draft.saved(returnedShots, historyRevision(payload.history));
        setSaveMessage("分镜已保存");
      } else setSaveMessage("保存未完成，草稿已保留。请检查页面错误提示。");
    } catch (error) {
      if (mounted.current) setSaveMessage(`保存失败，草稿已保留：${error.message}`);
    } finally {
      inFlight.current = false;
      if (mounted.current) setSaving(false);
    }
  };

  const historyAction = async (action, revisionId) => {
    if (inFlight.current || draft.dirty || disabled || !history) return;
    inFlight.current = true;
    setSaving(true);
    setSaveMessage("");
    try {
      const result = await onHistoryAction?.(action, { revisionId, baseRevision: historyRevision(history), dirty: draft.dirty, expectedScope });
      if (mounted.current) setSaveMessage(result ? "已更新保存版本" : "版本操作未完成，请检查页面错误提示。");
    } catch (error) {
      if (mounted.current) setSaveMessage(`版本操作失败：${error.message}`);
    } finally {
      inFlight.current = false;
      if (mounted.current) setSaving(false);
    }
  };

  return <div className="storyboard-workbench">
    <header className="editor-heading">
      <h2>镜头工作台</h2>
      <span className="muted">{draft.value.length} 个镜头 · {draft.dirty ? "未保存" : "已保存"}</span>
      <div className="editor-toolbar" role="group" aria-label="草稿操作">
        <EditorButton icon={Undo2} label="撤销草稿" disabled={disabled || !draft.past.length} onClick={draft.undo} />
        <EditorButton icon={Redo2} label="重做草稿" disabled={disabled || !draft.future.length} onClick={draft.redo} />
        <EditorButton icon={RotateCcw} label="放弃草稿并重新载入" disabled={disabled || !draft.dirty} onClick={() => { draft.reset(); setSaveMessage(""); }} />
        <EditorButton icon={Save} label="保存分镜" className="primary" disabled={disabled || !draft.dirty || !draft.value.length || Boolean(invalidCount) || draft.conflict || !history || historyLoading || !onSaveShots} onClick={save}>{saving ? "正在保存..." : "保存分镜"}</EditorButton>
        <EditorButton icon={Mic} label="打开配音" disabled={disabled || draft.dirty || !onGoVoiceover} onClick={onGoVoiceover}>配音</EditorButton>
      </div>
    </header>
    <details className="analysis-controls" open={!draft.value.length}>
      <summary>分镜分析 · {inputText.length} 字</summary>
      <div className="analysis-fields">
        <label className="full-label">目标时长<input type="number" min="1" value={duration} disabled={disabled} onChange={(event) => setDuration(event.target.value)} /></label>
        <label className="full-label">分镜数<input type="number" min="1" step="1" value={shotCount} disabled={disabled} onChange={(event) => setShotCount(event.target.value)} /></label>
        <button type="button" className="primary" title="分析分镜" aria-label="分析分镜" disabled={!canAnalyze || !onAnalyzeStoryboard} onClick={() => onAnalyzeStoryboard({ target_duration_seconds: Number(duration), shot_count: Number(shotCount) })}>分析分镜</button>
      </div>
    </details>
    <JobProgress job={job} title="当前活动任务" />
    {isRunning ? <p className="editor-warning" role="status">当前活动任务运行中，镜头编辑与版本操作暂不可用。</p> : null}
    {draft.conflict ? <div className="editor-conflict" role="alert"><span>后台版本已变化，草稿仍保留。刷新后重新载入再编辑。</span><EditorButton icon={RefreshCw} label="刷新项目" disabled={disabled} onClick={onRefreshProject} /></div> : null}
    {invalidCount ? <p className="error-text" role="alert">{invalidCount} 个镜头参数无效，修正后才能保存。</p> : null}
    <p className="editor-save-status muted" role="status">{saveMessage || (historyLoading ? "正在读取版本历史..." : historyError || (!history && project ? "等待版本历史，暂不可保存。" : ""))}</p>
    {draft.value.length ? <SubtitleCorrection key={scope} shots={draft.value} disabled={disabled} onChange={change} /> : null}
    <div className="editor-body">
      <div className="editor-shot-list" role="list" aria-label="镜头顺序">
        {draft.value.map((shot, index) => <div className={`editor-shot-item ${selected?.shot_id === shot.shot_id ? "selected" : ""}`} role="listitem" key={shot.shot_id}>
          <button type="button" title={`选择镜头 ${index + 1}`} aria-label={`选择镜头 ${index + 1}`} aria-pressed={selected?.shot_id === shot.shot_id} className="editor-shot-select" onClick={() => setSelectedId(shot.shot_id)}>
            <strong>{String(index + 1).padStart(2, "0")} · {shot.title || "未命名镜头"}</strong>
            <span>{shot.tts_text || shot.script_text || "无旁白"}</span>
            <small>{shot.shot_id}{shotInfo(shot).errors.length ? " · 参数有误" : ""}</small>
          </button>
        </div>)}
      </div>
      <section className="editor-selected-shot" aria-label="当前镜头">
        {selected ? <>
          <div className="selected-shot-toolbar">
            <strong>{selectedIndex + 1} · {selected.title || "未命名镜头"}</strong>
            <div className="editor-toolbar">
              <EditorButton icon={ArrowUp} label="上移镜头" disabled={disabled || selectedIndex === 0} onClick={() => change((shots) => moveShot(shots, selected.shot_id, -1))} />
              <EditorButton icon={ArrowDown} label="下移镜头" disabled={disabled || selectedIndex === draft.value.length - 1} onClick={() => change((shots) => moveShot(shots, selected.shot_id, 1))} />
              <EditorButton icon={Copy} label="复制镜头" disabled={disabled} onClick={() => {
                const shots = duplicateShot(draft.value, selected.shot_id);
                change(shots); setSelectedId(shots[selectedIndex + 1].shot_id);
              }} />
              <EditorButton icon={Trash2} label="删除镜头" className="danger" disabled={disabled || draft.value.length <= 1} onClick={() => {
                change((shots) => deleteShot(shots, selected.shot_id));
                setSelectedId(draft.value[selectedIndex + 1]?.shot_id || draft.value[selectedIndex - 1]?.shot_id || "");
              }} />
            </div>
          </div>
          <ShotInspector key={`${scope}:${selected.shot_id}`} shot={selected} {...selectedInfo} assets={assets} backendUrl={backendUrl} projectId={project?.project_id}
            aspectRatio={aspectRatio} onAspectRatio={setAspectRatio} disabled={disabled}
            onEdit={(patch) => change((shots) => updateShot(shots, selected.shot_id, patch))}
            onSettings={(patch) => change((shots) => editShotSettings(shots, selected.shot_id, patch))}
            onFraming={(ratio, patch) => change((shots) => editShotFraming(shots, selected.shot_id, ratio, patch))}
            hasNext={selectedIndex < draft.value.length - 1}
            onMaterial={(materialId) => change((shots) => replaceShotMaterial(shots, selected.shot_id, materialId))}
            onMediaDuration={(value) => measure(selectedInfo.mediaKey, value)} onVoiceDuration={(value) => measure(selectedInfo.voiceKey, value)} />
          <SegmentRecommendations key={selected.shot_id} shot={selected} assets={assets} targetDuration={selectedInfo.timing.target}
            backendUrl={backendUrl} projectId={project?.project_id} activityId={activity?.activity_id} aspectRatio={aspectRatio}
            disabled={disabled} onRecommend={onRecommendSegments} onGoMaterials={draft.dirty ? undefined : onGoMaterials}
            onApply={(candidate, confirmed) => change((shots) => applySegmentCandidate(shots, selected.shot_id, candidate, confirmed))} />
        </> : <div className="empty-state">尚无分镜。</div>}
      </section>
    </div>
    <EditorHistory history={history} loading={historyLoading} error={historyError} dirty={draft.dirty} busy={disabled} onReload={onLoadHistory} onAction={historyAction} />
    <details className="editor-debug"><summary>调试 · 分镜 JSON</summary><pre className="readout">{JSON.stringify(analysis || {}, null, 2)}</pre></details>
  </div>;
}
