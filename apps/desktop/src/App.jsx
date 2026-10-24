import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { ApiClient, DEFAULT_BACKEND_URL } from "./api/client";
import { isTerminalJob } from "./api/jobMonitor";
import { WorkflowController } from "./api/workflowController";
import { AgentConsole } from "./components/AgentConsole";
import { ActivityBar } from "./components/ActivityBar";
import { CreateProjectModal } from "./components/CreateProjectModal";
import { OpenProjectModal } from "./components/OpenProjectModal";
import { JobLogPanel } from "./components/JobLogPanel";
import { ProjectStatusPanel } from "./components/ProjectStatusPanel";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { AssemblyPage } from "./pages/AssemblyPage";
import { CopywritingPage } from "./pages/CopywritingPage";
import { MaterialLibraryPage } from "./pages/MaterialLibraryPage";
import { ProductProfilePage } from "./pages/ProductProfilePage";
import { ReferencePage } from "./pages/ReferencePage";
import { ResultsPage } from "./pages/ResultsPage";
import { StoryboardPage } from "./pages/StoryboardPage";
import { VoiceoverPage } from "./pages/VoiceoverPage";

const pageKeys = new Set(["profile", "reference", "copywriting", "storyboard", "materials", "voiceover", "assembly", "results"]);
const splitLines = (value) => String(value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);

export default function App() {
  const backendUrl = DEFAULT_BACKEND_URL;
  const [launchOptions] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    const page = params.get("start_page") || "reference";
    return { autoOpenLatest: params.get("auto_open") === "latest", startPage: pageKeys.has(page) ? page : "reference" };
  });
  const [activePage, setActivePage] = useState(launchOptions.startPage);
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  const [openProjectOpen, setOpenProjectOpen] = useState(false);
  const controller = useMemo(() => {
    let storage;
    try { storage = window.localStorage; } catch { /* Browsing without storage remains supported. */ }
    return new WorkflowController(new ApiClient(backendUrl), { storage, autoOpenLatest: launchOptions.autoOpenLatest });
  }, [backendUrl, launchOptions]);
  const { connected, project, status, projects, jobs, messages, error, notice, validation, pending, loading, editorHistory, historyLoading, historyError } = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  const projectId = project?.project_id;
  const currentActivityId = status?.current_activity_id || project?.current_activity_id || "";
  const activity = project?.activities?.find((item) => item.activity_id === currentActivityId) || null;
  const latestJob = (...types) => jobs.find((job) => types.includes(job.job_type) && !isTerminalJob(job))
    || jobs.find((job) => types.includes(job.job_type)) || null;
  const submitProjectJob = controller.submitProjectJob;
  const scopeKey = `${projectId || "none"}:${currentActivityId}`;

  useEffect(() => {
    void controller.refreshProject();
    return controller.stop;
  }, [controller]);

  const page = {
    profile: <ProductProfilePage project={project} onSaveProductProfile={controller.saveProductProfile} />,
    reference: <ReferencePage
      activity={activity} project={project} job={latestJob("reference.transcribe")} validation={validation}
      onCancelJob={controller.cancelJob} onGoCopywriting={() => setActivePage("copywriting")}
      onValidateReferenceVideos={controller.validateReferenceVideos} onSetReferenceVideos={controller.setReferenceVideos}
      onUploadReferenceVideos={controller.uploadReferenceVideos} onTranscribeReference={controller.transcribeReference}
      onSaveReferenceText={controller.saveReferenceText}
    />,
    copywriting: <CopywritingPage
      activity={activity} project={project} job={latestJob("copy.rewrite", "copy.titles")}
      onGenerateTitles={(payload) => submitProjectJob("/copy/titles", payload, "标题生成")}
      onGoStoryboard={() => setActivePage("storyboard")}
      onRewriteCopy={(payload) => submitProjectJob("/copy/rewrite", payload, "文案改写")}
      onSaveCopyText={controller.saveCopyText}
    />,
    storyboard: <StoryboardPage
      activity={activity} project={project} backendUrl={backendUrl} job={latestJob("storyboard.analyze", "voiceovers.generate", "voiceovers.regenerate", "assembly.render", "assembly.plan")}
      onAnalyzeStoryboard={(payload) => submitProjectJob("/storyboard/analyze", payload, "分析分镜")}
      onSaveShots={controller.saveShots}
      history={editorHistory} historyLoading={historyLoading} historyError={historyError}
      onLoadHistory={controller.loadEditorHistory} onHistoryAction={controller.editorHistoryAction}
      onRecommendSegments={controller.recommendSegments} onGoMaterials={() => setActivePage("materials")}
      onGoVoiceover={() => setActivePage("voiceover")}
      busy={Boolean(pending) || loading} onRefreshProject={controller.refreshProject}
      editorJobRunning={jobs.some((item) => !isTerminalJob(item))}
    />,
    materials: <MaterialLibraryPage
      activity={activity} project={project} backendUrl={backendUrl} job={latestJob("materials.tag", "materials.index")}
      onUploadMaterials={controller.uploadMaterialVideos}
      onTagMaterials={(paths) => submitProjectJob("/materials/tag", { material_paths: splitLines(paths) }, "素材打标")}
      onIndexSegments={(payload) => submitProjectJob("/materials/index", payload, "素材片段分析")}
    />,
    voiceover: <VoiceoverPage
      activity={activity} project={project} backendUrl={backendUrl} job={latestJob("voiceovers.generate", "voiceovers.regenerate", "voiceovers.align")}
      onGenerateVoiceovers={(payload) => submitProjectJob("/voiceovers/generate", payload, "生成配音")}
      onRegenerateVoiceover={(payload) => submitProjectJob("/voiceovers/regenerate", payload, `重新生成配音 ${payload.shot_id}`)}
      onAlignVoiceovers={() => submitProjectJob("/voiceovers/align", {}, "按配音对齐字幕")}
    />,
    assembly: <AssemblyPage
      activity={activity} project={project} backendUrl={backendUrl}
      job={latestJob("assembly.render", "assembly.plan")} renderJob={latestJob("assembly.render")}
      coverJob={latestJob("covers.render")} onGenerateCover={controller.generateCover}
      onCancelJob={controller.cancelJob}
      onSaveAudioSettings={controller.saveAudioSettings} onUploadMusic={controller.uploadMusic}
      onSetNarrationMode={controller.setNarrationMode}
      busy={Boolean(pending) || jobs.some((item) => !isTerminalJob(item))}
      onBuildAssemblyPlan={(outputPath) => submitProjectJob("/assembly/plan", { output_path: outputPath }, "生成合成计划")}
      onRenderVideo={(payload) => submitProjectJob("/assembly/render", payload, "导出视频")}
    />,
    results: <ResultsPage activity={activity} project={project} jobs={jobs} backendUrl={backendUrl} renderJob={latestJob("assembly.render")} />,
  }[activePage];

  return (
    <div className="app-shell">
      <TopBar backendUrl={backendUrl} status={status} connected={connected} busy={loading || Boolean(pending)}
        onCreateProject={() => setCreateProjectOpen(true)} onOpenExternalProject={() => setOpenProjectOpen(true)} onRefresh={controller.refreshProject} />
      <CreateProjectModal open={createProjectOpen} onClose={() => setCreateProjectOpen(false)} onCreate={async (input) => {
        const result = await controller.createProject(input);
        if (result) { setCreateProjectOpen(false); setActivePage("reference"); }
        return result;
      }} />
      <OpenProjectModal open={openProjectOpen} onClose={() => setOpenProjectOpen(false)} onOpen={async (path) => {
        const result = await controller.openExternalProject(path);
        if (result) { setOpenProjectOpen(false); setActivePage("reference"); }
        return result;
      }} />
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {notice ? <div className="notice-banner" role="status">{notice}</div> : null}
      <ActivityBar key={projectId || "none"} activity={activity} project={project} busy={loading || Boolean(pending)}
        onCreateActivity={controller.createActivity} onSelectActivity={controller.selectActivity} />
      <div className="body-grid">
        <Sidebar activePage={activePage} currentProjectId={projectId} projects={projects} onChangePage={setActivePage}
          onDeleteProject={controller.deleteProject} onOpenProject={controller.openProject} onRenameProject={controller.renameProject} />
        <main className="workspace" aria-busy={loading || Boolean(pending)}>
          {loading ? <div className="empty-state" role="status">正在加载项目...</div> : (
            <>
              {pending ? <div className="pending-status" role="status">{pending}...</div> : null}
              <fieldset className="workspace-fields" key={scopeKey} disabled={Boolean(pending)}>
                {page}
              </fieldset>
            </>
          )}
        </main>
        <aside className="right-rail">
          <AgentConsole onCommand={controller.runAgentCommand} messages={messages} />
          <ProjectStatusPanel status={status} />
          <JobLogPanel jobs={jobs} onCancelJob={controller.cancelJob} />
        </aside>
      </div>
    </div>
  );
}
