import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiClient, DEFAULT_BACKEND_URL } from "./api/client";
import { AgentConsole } from "./components/AgentConsole";
import { ActivityBar } from "./components/ActivityBar";
import { CreateProjectModal } from "./components/CreateProjectModal";
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

function splitLines(value) {
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean);
  return String(value || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatAgentResponse(payload) {
  const lines = [];
  if (payload.summary) lines.push(payload.summary);
  if (payload.requires_confirmation) lines.push("需要确认后才会执行。");
  if (payload.job) lines.push(`任务已创建: ${payload.job.job_type} / ${payload.job.job_id}`);
  if (payload.status && !payload.summary?.includes("当前项目")) {
    lines.push(
      [
        `当前活动: ${payload.status.current_activity_name || "未选择"}`,
        `参考文案: ${payload.status.has_reference ? "已就绪" : "未设置"}`,
        `成稿文案: ${payload.status.has_copywriting ? "已生成" : "未生成"}`,
        `分镜: ${payload.status.storyboard_shots || 0} 条`,
        `配音: ${payload.status.voiceover_segments || 0} 条`,
        `合成计划: ${payload.status.has_assembly_plan ? "已生成" : "未生成"}`,
      ].join("\n")
    );
  }
  return lines.filter(Boolean).join("\n\n") || "命令已处理。";
}

function isTerminalJob(job) {
  return ["succeeded", "failed", "cancelled"].includes(job?.status);
}

export default function App() {
  const [backendUrl] = useState(DEFAULT_BACKEND_URL);
  const [activePage, setActivePage] = useState("reference");
  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState(null);
  const [project, setProject] = useState(null);
  const [projects, setProjects] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [messages, setMessages] = useState([]);
  const [error, setError] = useState("");
  const [referenceValidation, setReferenceValidation] = useState(null);
  const [createProjectOpen, setCreateProjectOpen] = useState(false);

  const api = useMemo(() => new ApiClient(backendUrl), [backendUrl]);
  const projectId = status?.project_id;
  const currentActivityId = status?.current_activity_id || project?.current_activity_id || "";
  const currentActivity = useMemo(() => {
    const activities = project?.activities || [];
    return activities.find((item) => item.activity_id === currentActivityId) || activities[0] || null;
  }, [project, currentActivityId]);
  const referenceJob = useMemo(() => {
    return jobs.find((job) => job.job_type === "reference.transcribe" && !isTerminalJob(job))
      || jobs.find((job) => job.job_type === "reference.transcribe")
      || null;
  }, [jobs]);
  const copywritingJob = useMemo(() => {
    return jobs.find((job) => ["copy.rewrite", "copy.titles"].includes(job.job_type) && !isTerminalJob(job))
      || jobs.find((job) => ["copy.rewrite", "copy.titles"].includes(job.job_type))
      || null;
  }, [jobs]);

  const addMessage = useCallback((role, text) => {
    setMessages((items) => [...items, { role, text }]);
  }, []);

  const refreshProject = useCallback(async () => {
    try {
      await api.get("/health");
      setConnected(true);
      const projectList = await api.get("/projects");
      setProjects(projectList.projects || []);
      if (projectId) {
        const snapshot = await api.get(`/projects/${projectId}/snapshot`);
        setStatus(snapshot.status);
        setProject(snapshot.project);
      }
      setError("");
    } catch (err) {
      setConnected(false);
      setError(err.message);
    }
  }, [api, projectId]);

  const createProject = useCallback(async (input = {}) => {
    try {
      const payload = await api.post("/projects", input);
      setStatus(payload.status);
      setProject(payload.project);
      setActivePage("reference");
      setCreateProjectOpen(false);
      const projectList = await api.get("/projects");
      setProjects(projectList.projects || []);
      addMessage("system", `已创建项目 ${payload.status.project_id}`);
      setConnected(true);
      setError("");
    } catch (err) {
      setError(err.message);
      addMessage("system", `创建项目失败: ${err.message}`);
    }
  }, [api, addMessage]);

  const saveProductProfile = useCallback(async (productProfile) => {
    if (!projectId) {
      addMessage("system", "请先创建项目。");
      return;
    }
    try {
      const payload = await api.patch(`/projects/${projectId}`, { product_profile: productProfile });
      setStatus(payload.status);
      setProject(payload.project);
      const projectList = await api.get("/projects");
      setProjects(projectList.projects || []);
      addMessage("system", "项目资料已保存。");
      setError("");
    } catch (err) {
      setError(err.message);
      addMessage("system", `保存项目资料失败: ${err.message}`);
    }
  }, [api, addMessage, projectId]);

  const openProject = useCallback(async (nextProjectId) => {
    try {
      const snapshot = await api.get(`/projects/${nextProjectId}/snapshot`);
      setStatus(snapshot.status);
      setProject(snapshot.project);
      setActivePage("reference");
      addMessage("system", `已打开项目 ${nextProjectId}`);
      setConnected(true);
      setError("");
    } catch (err) {
      setError(err.message);
      addMessage("system", `打开项目失败: ${err.message}`);
    }
  }, [api, addMessage]);

  const renameProject = useCallback(async (projectItem, nextName) => {
    if (!nextName || nextName.trim() === projectItem.name) return;
    try {
      const payload = await api.patch(`/projects/${projectItem.project_id}`, { name: nextName.trim() });
      if (payload.status.project_id === projectId) {
        setStatus(payload.status);
        setProject(payload.project);
      }
      const projectList = await api.get("/projects");
      setProjects(projectList.projects || []);
      addMessage("system", `已重命名项目 ${projectItem.project_id}`);
    } catch (err) {
      setError(err.message);
      addMessage("system", `重命名项目失败: ${err.message}`);
    }
  }, [api, addMessage, projectId]);

  const deleteProject = useCallback(async (projectItem) => {
    try {
      const payload = await api.delete(`/projects/${projectItem.project_id}`);
      const remainingProjects = payload.projects || [];
      setProjects(remainingProjects);
      if (projectItem.project_id === projectId) {
        const nextProject = remainingProjects[0];
        if (nextProject) {
          const snapshot = await api.get(`/projects/${nextProject.project_id}/snapshot`);
          setStatus(snapshot.status);
          setProject(snapshot.project);
        } else {
          setStatus(null);
          setProject(null);
        }
      }
      addMessage("system", `已删除项目 ${projectItem.project_id}`);
    } catch (err) {
      setError(err.message);
      addMessage("system", `删除项目失败: ${err.message}`);
    }
  }, [api, addMessage, projectId]);

  const createActivity = useCallback(async (activityName = "") => {
    if (!projectId) {
      addMessage("system", "请先创建或打开项目。");
      return;
    }
    const defaultName = `活动 ${(project?.activities || []).length + 1}`;
    const name = activityName.trim() || defaultName;
    try {
      const payload = await api.post(`/projects/${projectId}/activities`, { name: name.trim() || defaultName });
      setStatus(payload.status);
      setProject(payload.project);
      addMessage("system", `已创建活动 ${payload.activity.activity_id}`);
    } catch (err) {
      setError(err.message);
      addMessage("system", `创建活动失败: ${err.message}`);
    }
  }, [api, addMessage, project, projectId]);

  const selectActivity = useCallback(async (activityId) => {
    if (!projectId || !activityId) return;
    try {
      const payload = await api.post(`/projects/${projectId}/activities/${activityId}/select`);
      setStatus(payload.status);
      setProject(payload.project);
      addMessage("system", `已切换活动 ${activityId}`);
    } catch (err) {
      setError(err.message);
      addMessage("system", `切换活动失败: ${err.message}`);
    }
  }, [api, addMessage, projectId]);

  useEffect(() => {
    refreshProject();
  }, [refreshProject]);

  const pollJob = useCallback(async (jobId) => {
    for (let index = 0; index < 120; index += 1) {
      const payload = await api.get(`/jobs/${jobId}`);
      const job = payload.job;
      setJobs((items) => [job, ...items.filter((item) => item.job_id !== job.job_id)].slice(0, 12));
      if (isTerminalJob(job)) {
        if (projectId) {
          const snapshot = await api.get(`/projects/${projectId}/snapshot`);
          setStatus(snapshot.status);
          setProject(snapshot.project);
        }
        return job;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    throw new Error(`job polling timed out: ${jobId}`);
  }, [api, projectId]);

  const cancelJob = useCallback(async (jobId) => {
    if (!jobId) return;
    try {
      const payload = await api.post(`/jobs/${jobId}/cancel`, {});
      const job = payload.job;
      setJobs((items) => [job, ...items.filter((item) => item.job_id !== job.job_id)].slice(0, 12));
      addMessage("system", `已请求停止任务 ${jobId}`);
    } catch (err) {
      setError(err.message);
      addMessage("system", `停止任务失败: ${err.message}`);
    }
  }, [api, addMessage]);

  const submitProjectJob = useCallback(async (path, payload, label) => {
    if (!projectId) {
      addMessage("system", "请先创建项目。");
      return;
    }
    try {
      const response = await api.post(`/projects/${projectId}${path}`, payload);
      const job = response.job;
      setJobs((items) => [job, ...items.filter((item) => item.job_id !== job.job_id)].slice(0, 12));
      addMessage("user", label);
      const finished = await pollJob(job.job_id);
      addMessage("system", `${label}: ${finished.status}`);
    } catch (err) {
      setError(err.message);
      addMessage("system", `${label}失败: ${err.message}`);
    }
  }, [api, addMessage, pollJob, projectId]);

  const setReferenceVideos = useCallback(async (videos) => {
    if (!projectId) {
      addMessage("system", "请先创建项目。");
      return;
    }
    try {
      const payload = await api.post(`/projects/${projectId}/reference/videos`, { videos });
      setStatus(payload.status);
      setProject(payload.project);
      setReferenceValidation(payload.validation || null);
      addMessage("system", `参考视频队列已保存，共 ${videos.length} 个。`);
    } catch (err) {
      setError(err.message);
      addMessage("system", `保存参考视频失败: ${err.message}`);
    }
  }, [api, addMessage, projectId]);

  const uploadReferenceVideos = useCallback(async (files) => {
    if (!projectId) {
      addMessage("system", "请先创建项目。");
      return null;
    }
    const selectedFiles = Array.from(files || []);
    if (!selectedFiles.length) return null;
    try {
      const formData = new FormData();
      for (const file of selectedFiles) {
        formData.append("videos", file, file.webkitRelativePath || file.name);
      }
      const payload = await api.upload(`/projects/${projectId}/reference/upload-videos`, formData);
      setStatus(payload.status);
      setProject(payload.project);
      setReferenceValidation(payload.validation || null);
      addMessage("system", `已导入参考视频 ${payload.uploaded_videos?.length || selectedFiles.length} 个。`);
      setError("");
      return payload;
    } catch (err) {
      setError(err.message);
      addMessage("system", `导入参考视频失败: ${err.message}`);
      return null;
    }
  }, [api, addMessage, projectId]);

  const validateReferenceVideos = useCallback(async (videos) => {
    if (!projectId || !videos?.length) {
      setReferenceValidation(null);
      return null;
    }
    try {
      const payload = await api.post(`/projects/${projectId}/reference/validate-videos`, { videos });
      setReferenceValidation(payload);
      return payload;
    } catch (err) {
      setError(err.message);
      addMessage("system", `校验视频路径失败: ${err.message}`);
      return null;
    }
  }, [api, addMessage, projectId]);

  const runAgentCommand = useCallback(async (command) => {
    addMessage("user", command);
    try {
      const payload = await api.post("/agent/command", { project_id: projectId, command });
      if (payload.projects) setProjects(payload.projects);
      if (payload.status) setStatus(payload.status);
      if (payload.project) setProject(payload.project);
      if (payload.deleted && payload.project_id === projectId) {
        const nextProject = (payload.projects || [])[0];
        if (nextProject) {
          const snapshot = await api.get(`/projects/${nextProject.project_id}/snapshot`);
          setStatus(snapshot.status);
          setProject(snapshot.project);
        } else {
          setStatus(null);
          setProject(null);
        }
      }
      addMessage("system", formatAgentResponse(payload));
      if (payload.job) {
        const finished = await pollJob(payload.job.job_id);
        addMessage("system", `${payload.summary || "Agent 任务"}: ${finished.status}`);
      }
    } catch (err) {
      setError(err.message);
      addMessage("system", `Agent 命令失败: ${err.message}`);
    }
  }, [api, addMessage, pollJob, projectId]);

  const page = {
    profile: (
      <ProductProfilePage
        project={project}
        onSaveProductProfile={saveProductProfile}
      />
    ),
    reference: (
      <ReferencePage
        activity={currentActivity}
        job={referenceJob}
        project={project}
        validation={referenceValidation}
        onCancelJob={cancelJob}
        onGoCopywriting={() => setActivePage("copywriting")}
        onValidateReferenceVideos={validateReferenceVideos}
        onSetReferenceVideos={setReferenceVideos}
        onUploadReferenceVideos={uploadReferenceVideos}
        onTranscribeReference={(payload) => {
          const videos = Array.isArray(payload?.videos) ? payload.videos : splitLines(payload);
          return validateReferenceVideos(videos).then((validation) => {
            if (!validation?.ok) {
              addMessage("system", "视频路径校验未通过，请先修正缺失路径。");
              setError("视频路径校验未通过，请使用完整路径。");
              return null;
            }
            return submitProjectJob(
              "/reference/transcribe",
              { videos: splitLines(videos), model_size: payload?.model_size || "medium" },
              "转写参考"
            );
          });
        }}
      />
    ),
    copywriting: (
      <CopywritingPage
        activity={currentActivity}
        job={copywritingJob}
        onGenerateTitles={(payload) => submitProjectJob("/copy/titles", payload, "标题生成")}
        onGoStoryboard={() => setActivePage("storyboard")}
        onRewriteCopy={(payload) => submitProjectJob("/copy/rewrite", payload, "文案改写")}
        project={project}
      />
    ),
    storyboard: (
      <StoryboardPage
        activity={currentActivity}
        project={project}
        onAnalyzeStoryboard={(payload) => submitProjectJob("/storyboard/analyze", payload, "分析分镜")}
      />
    ),
    materials: (
      <MaterialLibraryPage
        activity={currentActivity}
        project={project}
        onTagMaterials={(paths) => submitProjectJob("/materials/tag", { material_paths: splitLines(paths) }, "素材打标")}
      />
    ),
    voiceover: (
      <VoiceoverPage
        activity={currentActivity}
        backendUrl={backendUrl}
        project={project}
        onGenerateVoiceovers={(payload) => submitProjectJob("/voiceovers/generate", payload, "生成配音")}
      />
    ),
    assembly: (
      <AssemblyPage
        activity={currentActivity}
        project={project}
        onBuildAssemblyPlan={(outputPath) => submitProjectJob("/assembly/plan", { output_path: outputPath }, "生成合成计划")}
      />
    ),
    results: <ResultsPage project={project} jobs={jobs} />,
  }[activePage];

  return (
    <div className="app-shell">
      <TopBar
        backendUrl={backendUrl}
        status={status}
        connected={connected}
        onCreateProject={() => setCreateProjectOpen(true)}
        onRefresh={refreshProject}
      />
      <CreateProjectModal
        open={createProjectOpen}
        onClose={() => setCreateProjectOpen(false)}
        onCreate={createProject}
      />
      {error ? <div className="error-banner">{error}</div> : null}
      <ActivityBar
        activity={currentActivity}
        project={project}
        onCreateActivity={createActivity}
        onSelectActivity={selectActivity}
      />
      <div className="body-grid">
        <Sidebar
          activePage={activePage}
          currentProjectId={projectId}
          onChangePage={setActivePage}
          onDeleteProject={deleteProject}
          onOpenProject={openProject}
          onRenameProject={renameProject}
          projects={projects}
        />
        <main className="workspace">{page}</main>
        <aside className="right-rail">
          <AgentConsole onCommand={runAgentCommand} messages={messages} />
          <ProjectStatusPanel status={status} />
          <JobLogPanel jobs={jobs} />
        </aside>
      </div>
    </div>
  );
}
