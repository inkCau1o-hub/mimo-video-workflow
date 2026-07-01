import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiClient, DEFAULT_BACKEND_URL } from "./api/client";
import { AgentConsole } from "./components/AgentConsole";
import { JobLogPanel } from "./components/JobLogPanel";
import { ProjectStatusPanel } from "./components/ProjectStatusPanel";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { AssemblyPage } from "./pages/AssemblyPage";
import { MaterialLibraryPage } from "./pages/MaterialLibraryPage";
import { ReferencePage } from "./pages/ReferencePage";
import { ResultsPage } from "./pages/ResultsPage";
import { StoryboardPage } from "./pages/StoryboardPage";
import { VoiceoverPage } from "./pages/VoiceoverPage";

function splitLines(value) {
  return String(value || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
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

  const api = useMemo(() => new ApiClient(backendUrl), [backendUrl]);
  const projectId = status?.project_id;

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

  const createProject = useCallback(async () => {
    try {
      const payload = await api.post("/projects", { name: "MiMo Prototype Project" });
      setStatus(payload.status);
      setProject(payload.project);
      addMessage("system", `已创建项目 ${payload.status.project_id}`);
      setConnected(true);
      setError("");
    } catch (err) {
      setError(err.message);
      addMessage("system", `创建项目失败: ${err.message}`);
    }
  }, [api, addMessage]);

  useEffect(() => {
    refreshProject();
  }, [refreshProject]);

  const pollJob = useCallback(async (jobId) => {
    for (let index = 0; index < 120; index += 1) {
      const payload = await api.get(`/jobs/${jobId}`);
      const job = payload.job;
      setJobs((items) => [job, ...items.filter((item) => item.job_id !== job.job_id)].slice(0, 12));
      if (job.status === "succeeded" || job.status === "failed") {
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

  const setReferenceText = useCallback(async (text) => {
    if (!projectId) {
      addMessage("system", "请先创建项目。");
      return;
    }
    try {
      const payload = await api.post(`/projects/${projectId}/reference/text`, { text });
      setStatus(payload.status);
      const snapshot = await api.get(`/projects/${projectId}/snapshot`);
      setProject(snapshot.project);
      addMessage("system", "参考文案已写入项目。");
    } catch (err) {
      setError(err.message);
      addMessage("system", `写入参考失败: ${err.message}`);
    }
  }, [api, addMessage, projectId]);

  const runAgentCommand = useCallback(async (command) => {
    addMessage("user", command);
    try {
      const payload = await api.post("/agent/command", { project_id: projectId, command });
      addMessage("system", JSON.stringify(payload, null, 2));
      if (payload.status) setStatus(payload.status);
    } catch (err) {
      setError(err.message);
      addMessage("system", `Agent 命令失败: ${err.message}`);
    }
  }, [api, addMessage, projectId]);

  const page = {
    reference: (
      <ReferencePage
        project={project}
        onSetReferenceText={setReferenceText}
        onTranscribeReference={(paths) => submitProjectJob(
          "/reference/transcribe",
          { videos: splitLines(paths), model_size: "medium" },
          "转写参考"
        )}
      />
    ),
    storyboard: (
      <StoryboardPage
        project={project}
        onAnalyzeStoryboard={(payload) => submitProjectJob("/storyboard/analyze", payload, "分析分镜")}
      />
    ),
    materials: (
      <MaterialLibraryPage
        project={project}
        onTagMaterials={(paths) => submitProjectJob("/materials/tag", { material_paths: splitLines(paths) }, "素材打标")}
      />
    ),
    voiceover: (
      <VoiceoverPage
        project={project}
        onGenerateVoiceovers={() => submitProjectJob("/voiceovers/generate", {}, "生成配音")}
      />
    ),
    assembly: (
      <AssemblyPage
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
        onCreateProject={createProject}
        onRefresh={refreshProject}
      />
      {error ? <div className="error-banner">{error}</div> : null}
      <div className="body-grid">
        <Sidebar activePage={activePage} onChangePage={setActivePage} projects={projects} />
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
