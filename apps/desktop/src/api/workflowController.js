import { isTerminalJob, jobStatusText, monitorJob } from "./jobMonitor.js";
import { historyRevision, historySummary } from "../editor/shotEditing.js";

const readStored = (storage, key, fallback) => {
  try { return JSON.parse(storage?.getItem(key) || "null") || fallback; } catch { return fallback; }
};

export class WorkflowController {
  constructor(api, { storage, autoOpenLatest = false } = {}) {
    this.api = api;
    this.storage = storage;
    this.autoOpenLatest = autoOpenLatest;
    this.storageKey = `mimo.workflow:${api.baseUrl}`;
    this.listeners = new Set();
    this.monitors = new Map();
    this.scope = {};
    this.snapshotVersion = 0;
    this.validationVersion = 0;
    this.historyVersion = 0;
    this.state = {
      connected: false, project: null, status: null, projects: [], jobs: [], messages: [],
      error: "", notice: "", validation: null, loading: false, pending: "",
      editorHistory: null, historyLoading: false, historyError: "",
    };
  }

  subscribe = (listener) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  getSnapshot = () => this.state;
  set = (patch) => {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  };
  current = (scope) => scope === this.scope;
  message = (text, role = "system") => this.set({ messages: [...this.state.messages, { role, text }].slice(-100) });
  persist = (key, value) => {
    try { this.storage?.setItem(`${this.storageKey}:${key}`, JSON.stringify(value)); } catch { /* Storage may be disabled. */ }
  };
  stop = () => {
    for (const controller of this.monitors.values()) controller.abort();
    this.monitors.clear();
    this.scope = {};
  };
  beginScope = (projectId = "", activityId = "") => {
    this.stop();
    this.needsRefresh = false;
    this.scope = { projectId, activityId };
    this.snapshotVersion += 1;
    this.historyVersion += 1;
    this.set({ project: null, status: null, jobs: [], validation: null, messages: [], error: "", notice: "", loading: true, pending: "", editorHistory: null, historyLoading: false, historyError: "" });
    return this.scope;
  };

  commitSnapshot = (payload, scope) => {
    if (!this.current(scope) || !payload?.project) return false;
    const project = payload.project;
    if (scope.projectId && scope.projectId !== project.project_id) return false;
    const activity = project.activities?.find((item) => item.activity_id === scope.activityId)
      || project.activities?.find((item) => item.activity_id === project.current_activity_id)
      || project.activities?.[0];
    scope.projectId = project.project_id;
    scope.activityId = activity?.activity_id || "";
    const status = { ...payload.status, project_id: project.project_id, name: project.name };
    // The UI selection must not follow a background job's old current_activity_id.
    if (activity) Object.assign(status, {
      current_activity_id: activity.activity_id, current_activity_name: activity.name,
      has_reference: Boolean(activity.reference_copy?.source_text),
      has_copywriting: Boolean(activity.copywriting?.rewrite?.text),
      storyboard_shots: activity.storyboard_analysis?.shots?.length || 0,
      material_assets: activity.material_assets?.length || 0,
      voiceover_segments: activity.voiceover_segments?.length || 0,
      has_assembly_plan: Boolean(activity.assembly_plan),
    });
    this.persist("selection", { projectId: scope.projectId, activityId: scope.activityId });
    this.historyVersion += 1;
    this.set({ project: { ...project, current_activity_id: scope.activityId }, status, connected: true,
      editorHistory: historySummary(activity?.metadata?.edit_history)
        || (project.current_activity_id === scope.activityId ? payload.history : null) || null,
      historyLoading: false, historyError: "" });
    return true;
  };

  loadSnapshot = async (scope = this.scope) => {
    if (!scope.projectId || !this.current(scope)) return null;
    const version = ++this.snapshotVersion;
    const payload = await this.api.get(`/projects/${encodeURIComponent(scope.projectId)}/snapshot`);
    if (!this.current(scope) || version !== this.snapshotVersion) return null;
    this.commitSnapshot(payload, scope);
    return payload;
  };

  listProjects = async (scope = this.scope) => {
    const payload = await this.api.get("/projects");
    if (this.current(scope)) this.set({ projects: payload.projects || [] });
    return payload.projects || [];
  };

  refreshProject = async () => {
    if (this.state.pending || this.state.loading) return;
    const scope = this.scope;
    try {
      await this.api.get("/health");
      if (!this.current(scope)) return;
      this.set({ connected: true, error: "" });
      const projects = await this.listProjects(scope);
      if (!this.current(scope)) return;
      if (scope.projectId) {
        await this.loadSelectedSnapshot(scope);
        await this.recoverJobs(scope);
      } else {
        const saved = readStored(this.storage, `${this.storageKey}:selection`, {});
        const target = projects.find((item) => item.project_id === saved.projectId)
          || (this.autoOpenLatest ? projects[0] : null);
        if (target) await this.openProject(target.project_id, target.project_id === saved.projectId ? saved.activityId : "");
      }
    } catch (error) {
      if (this.current(scope)) this.set({ connected: false, error: error.message });
    }
  };

  openProject = async (projectId, activityId = "") => {
    const scope = this.beginScope(projectId, activityId);
    try {
      await this.loadSelectedSnapshot(scope);
      await this.recoverJobs(scope);
      return this.current(scope) ? this.state.project : null;
    } catch (error) {
      if (this.current(scope)) this.set({ error: `打开项目失败: ${error.message}` });
      return null;
    } finally {
      if (this.current(scope)) this.set({ loading: false });
    }
  };

  loadSelectedSnapshot = async (scope) => {
    const payload = await this.loadSnapshot(scope);
    if (payload && this.current(scope) && scope.activityId && payload.project.current_activity_id !== scope.activityId) {
      const selected = await this.api.post(`/projects/${encodeURIComponent(scope.projectId)}/activities/${encodeURIComponent(scope.activityId)}/select`, { activity_id: scope.activityId });
      this.commitSnapshot(selected, scope);
    }
    return payload;
  };

  openExternalProject = async (path) => {
    if (!path.trim()) return null;
    return this.navigateRequest("/projects/open", { path: path.trim() }, "打开外部项目");
  };
  createProject = (input) => this.navigateRequest("/projects", input, "创建项目");

  navigateRequest = async (path, input, label) => {
    const scope = this.beginScope();
    try {
      const payload = await this.api.post(path, input);
      if (!this.current(scope)) return null;
      this.commitSnapshot(payload, scope);
      await this.listProjects(scope);
      await this.recoverJobs(scope);
      return this.current(scope) ? payload : null;
    } catch (error) {
      if (this.current(scope)) this.set({ error: this.errorText(error, label) });
      return null;
    } finally {
      if (this.current(scope)) this.set({ loading: false });
    }
  };

  selectActivity = async (activityId) => {
    const projectId = this.scope.projectId;
    if (!projectId || !activityId || activityId === this.scope.activityId) return;
    const scope = this.beginScope(projectId, activityId);
    try {
      const payload = await this.api.post(`/projects/${encodeURIComponent(projectId)}/activities/${encodeURIComponent(activityId)}/select`, { activity_id: activityId });
      this.commitSnapshot(payload, scope);
      await this.recoverJobs(scope);
    } catch (error) {
      if (this.current(scope)) this.set({ error: `切换活动失败: ${error.message}` });
    } finally {
      if (this.current(scope)) this.set({ loading: false });
    }
  };

  errorText = (error, label) => error.status === 409
    ? `${label}发生版本冲突，请刷新项目后重新载入；当前草稿未覆盖。${error.message}`
    : [404, 405, 501].includes(error.status)
    ? `${label}不可用，请确认后端已更新并支持该接口。${error.message}`
    : `${label}失败: ${error.message}`;

  runAction = async (label, action, { requiresProject = true } = {}) => {
    if (this.state.loading || this.state.pending) return null;
    const scope = this.scope;
    if (requiresProject && !scope.projectId) { this.set({ error: "请先创建或打开项目。" }); return null; }
    this.snapshotVersion += 1;
    this.historyVersion += 1;
    this.set({ pending: label, error: "", historyLoading: false });
    try {
      const payload = await action(scope);
      if (!this.current(scope)) return null;
      if (payload?.project) this.commitSnapshot(payload, scope);
      this.message(`${label}已提交。`);
      return payload;
    } catch (error) {
      if (this.current(scope)) this.set({ error: this.errorText(error, label) });
      return null;
    } finally {
      if (this.current(scope)) {
        this.snapshotVersion += 1;
        this.set({ pending: "" });
        if (this.needsRefresh) {
          this.needsRefresh = false;
          void this.refreshAfterJob(scope);
        }
      }
    }
  };

  save = (path, input, label) => this.runAction(label, async (scope) => {
    const payload = await this.api.post(`/projects/${encodeURIComponent(scope.projectId)}${path}`, { ...input, activity_id: scope.activityId });
    if (!this.current(scope)) return null;
    // Older reference/text responses contain status only, so reload the real snapshot.
    return payload.project ? payload : this.loadSnapshot(scope);
  });
  saveReferenceText = (text) => this.save("/reference/text", { text }, "保存参考文本");
  saveCopyText = (text) => this.save("/copy/text", { text }, "保存文案");
  matchesEditorScope = (expectedScope) => !expectedScope || (
    expectedScope.projectId === this.scope.projectId && expectedScope.activityId === this.scope.activityId
  );

  loadEditorHistory = async () => {
    const scope = this.scope;
    if (!scope.projectId || this.state.pending || this.state.loading) return null;
    const version = ++this.historyVersion;
    const snapshotVersion = this.snapshotVersion;
    this.set({ historyLoading: true, historyError: "" });
    try {
      const payload = await this.api.get(`/projects/${encodeURIComponent(scope.projectId)}/editor/history?activity_id=${encodeURIComponent(scope.activityId)}`);
      if (!this.current(scope) || version !== this.historyVersion || snapshotVersion !== this.snapshotVersion) return null;
      if (!payload.history || !Array.isArray(payload.history.entries)) throw new Error("后端未返回版本历史。");
      this.set({ editorHistory: payload.history });
      return payload.history;
    } catch (error) {
      if (this.current(scope) && version === this.historyVersion && snapshotVersion === this.snapshotVersion) {
        this.set({ historyError: this.errorText(error, "读取版本历史"), editorHistory: null });
      }
      return null;
    } finally {
      if (this.current(scope) && version === this.historyVersion) this.set({ historyLoading: false });
    }
  };

  saveShots = (shots, { label = "编辑镜头", baseRevision = historyRevision(this.state.editorHistory) ?? "", expectedScope } = {}) => {
    if (!this.matchesEditorScope(expectedScope)) return Promise.resolve(null);
    return this.save("/storyboard/shots", { shots, label, base_revision: baseRevision }, "保存分镜");
  };

  recommendSegments = async (shot, { targetDuration, query = "", expectedScope } = {}) => {
    if (!this.matchesEditorScope(expectedScope) || !this.scope.projectId) return null;
    const scope = this.scope;
    const snapshotVersion = this.snapshotVersion;
    const payload = await this.api.post(`/projects/${encodeURIComponent(scope.projectId)}/materials/recommendations`, {
      activity_id: scope.activityId, shot, target_duration: targetDuration, query,
    });
    return this.current(scope) && snapshotVersion === this.snapshotVersion ? payload : null;
  };

  editorHistoryAction = (action, { revisionId, label, baseRevision = historyRevision(this.state.editorHistory), dirty = false, expectedScope } = {}) => {
    if (!this.matchesEditorScope(expectedScope)) return Promise.resolve(null);
    if (dirty) { this.set({ error: "请先保存或放弃草稿，再操作已保存版本。" }); return Promise.resolve(null); }
    if (!this.state.editorHistory || !["undo", "redo", "restore", "checkpoint"].includes(action)) return Promise.resolve(null);
    if ((action === "undo" && !this.state.editorHistory.can_undo) || (action === "redo" && !this.state.editorHistory.can_redo)) return Promise.resolve(null);
    if (action === "restore" && !this.state.editorHistory.entries.some((entry) => entry.id === revisionId)) return Promise.resolve(null);
    return this.runAction("更新版本历史", async (scope) => {
      const payload = await this.api.post(`/projects/${encodeURIComponent(scope.projectId)}/editor/history`, {
        activity_id: scope.activityId, action, base_revision: baseRevision ?? "",
        ...(revisionId ? { revision_id: revisionId } : {}), ...(label ? { label } : {}),
      });
      if (!this.current(scope)) return null;
      if (!payload.project || !payload.history) throw new Error("后端未返回恢复后的项目和版本历史，请刷新确认。");
      return payload;
    });
  };
  setReferenceVideos = (videos) => this.save("/reference/videos", { videos }, "保存参考视频");

  uploadVideos = (kind, files) => this.runAction("上传视频", async (scope) => {
    const body = new FormData();
    body.append("activity_id", scope.activityId);
    for (const file of Array.from(files || [])) body.append("videos", file, file.webkitRelativePath || file.name);
    const payload = await this.api.upload(`/projects/${encodeURIComponent(scope.projectId)}/${kind}/upload-videos`, body);
    if (!this.current(scope)) return null;
    if (!payload.project) await this.loadSnapshot(scope);
    return payload;
  });
  uploadReferenceVideos = (files) => this.uploadVideos("reference", files);
  uploadMaterialVideos = (files) => this.uploadVideos("materials", files);

  saveAudioSettings = (audioSettings, { baseRevision = "", expectedScope } = {}) => {
    if (!this.matchesEditorScope(expectedScope)) return Promise.resolve(null);
    return this.save("/audio/settings", { audio_settings: audioSettings, base_revision: baseRevision }, "保存声音设置");
  };

  setNarrationMode = (narration_mode) => this.runAction("切换旁白模式", (scope) => this.api.patch(
    `/projects/${encodeURIComponent(scope.projectId)}/activities/${encodeURIComponent(scope.activityId)}`, { narration_mode }));

  uploadMusic = (file, { expectedScope } = {}) => {
    if (!this.matchesEditorScope(expectedScope)) return Promise.resolve(null);
    return this.runAction("导入背景音乐", async (scope) => {
      const body = new FormData();
      body.append("activity_id", scope.activityId);
      body.append("music", file, file.name);
      const payload = await this.api.upload(`/projects/${encodeURIComponent(scope.projectId)}/audio/upload-music`, body);
      return this.current(scope) ? payload : null;
    });
  };

  validateReferenceVideos = async (videos) => {
    const scope = this.scope;
    const version = ++this.validationVersion;
    if (!scope.projectId || !videos?.length) { this.set({ validation: null }); return null; }
    try {
      const payload = await this.api.post(`/projects/${encodeURIComponent(scope.projectId)}/reference/validate-videos`, { videos, activity_id: scope.activityId });
      if (!this.current(scope) || version !== this.validationVersion) return null;
      this.set({ validation: payload });
      return payload;
    } catch (error) {
      if (this.current(scope) && version === this.validationVersion) this.set({ error: error.message, validation: null });
      return null;
    }
  };

  transcribeReference = async (input) => {
    const scope = this.scope;
    const validation = await this.validateReferenceVideos(input.videos);
    if (!this.current(scope) || !validation) return null;
    if (!validation.ok) { this.set({ error: "视频路径校验未通过，请使用完整路径。" }); return null; }
    return this.submitProjectJob("/reference/transcribe", input, "转写参考");
  };

  saveProductProfile = (product_profile) => this.runAction("保存项目资料", (scope) => this.api.patch(`/projects/${encodeURIComponent(scope.projectId)}`, { product_profile }));
  renameProject = (project, name) => this.runAction("重命名项目", async (scope) => {
    const payload = await this.api.patch(`/projects/${encodeURIComponent(project.project_id)}`, { name: name.trim() });
    await this.listProjects(scope);
    return project.project_id === scope.projectId ? payload : null;
  });
  deleteProject = (project) => this.runAction("删除项目", async (scope) => {
    const payload = await this.api.delete(`/projects/${encodeURIComponent(project.project_id)}`);
    if (!this.current(scope)) return null;
    this.set({ projects: payload.projects || [] });
    if (scope.projectId === project.project_id) {
      this.persist("selection", {});
      if (payload.projects?.[0]) await this.openProject(payload.projects[0].project_id);
      else { this.beginScope(); this.set({ loading: false }); }
    }
    return payload;
  });
  createActivity = (name) => this.runAction("创建活动", async (scope) => {
    const payload = await this.api.post(`/projects/${encodeURIComponent(scope.projectId)}/activities`, { name: name.trim(), activity_id: scope.activityId });
    if (!this.current(scope)) return null;
    const nextScope = this.beginScope(scope.projectId, payload.activity?.activity_id || payload.project?.current_activity_id);
    this.commitSnapshot(payload, nextScope);
    await this.recoverJobs(nextScope);
    if (this.current(nextScope)) this.set({ loading: false });
    return null;
  });

  cacheJobs = () => {
    const key = `jobs:${this.scope.projectId}`;
    const old = readStored(this.storage, `${this.storageKey}:${key}`, []);
    const merged = new Map(old.map((job) => [job.job_id, job]));
    for (const job of this.state.jobs) {
      const { job_id, project_id, activity_id, job_type, status, progress, created_at, error, message } = job;
      merged.set(job_id, { job_id, project_id, activity_id, job_type, status, progress, created_at, error, message });
    }
    this.persist(key, [...merged.values()].slice(-100));
  };
  belongs = (job, scope) => job.project_id === scope.projectId && (job.activity_id || "") === scope.activityId;
  upsertJob = (job, scope) => {
    if (!this.current(scope) || !this.belongs(job, scope)) return;
    const old = this.state.jobs.find((item) => item.job_id === job.job_id);
    const jobs = old ? this.state.jobs.map((item) => item.job_id === job.job_id ? { ...item, ...job } : item) : [job, ...this.state.jobs];
    this.set({ jobs });
    this.cacheJobs();
  };

  recoverJobs = async (scope) => {
    if (!this.current(scope) || !scope.projectId) return;
    const cached = readStored(this.storage, `${this.storageKey}:jobs:${scope.projectId}`, []);
    let jobs;
    try {
      const payload = await this.api.get(`/projects/${encodeURIComponent(scope.projectId)}/jobs`);
      jobs = payload.jobs || [];
      if (this.current(scope)) this.set({ notice: "" });
    } catch (error) {
      jobs = cached;
      if (this.current(scope)) this.set({ notice: [404, 405, 501].includes(error.status)
        ? "当前后端尚不支持项目任务列表，仅恢复本机已记录的任务。"
        : `任务列表暂不可用，正在恢复本机任务: ${error.message}` });
    }
    if (!this.current(scope)) return;
    jobs = jobs.map((job) => ({ ...cached.find((item) => item.job_id === job.job_id), ...job }))
      .filter((job) => this.belongs(job, scope))
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    // Keep a just-submitted job if a concurrent listing has not included it yet.
    const listedIds = new Set(jobs.map((job) => job.job_id));
    const merged = [...this.state.jobs.filter((job) => !listedIds.has(job.job_id)), ...jobs];
    this.set({ jobs: merged.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || ""))) });
    this.cacheJobs();
    for (const job of this.state.jobs) if (!isTerminalJob(job)) this.watchJob(job, scope);
    if (jobs.some(isTerminalJob)) await this.refreshAfterJob(scope);
  };

  refreshAfterJob = async (scope) => {
    if (!this.current(scope)) return;
    if (this.state.pending) { this.needsRefresh = true; return; }
    try { await this.loadSnapshot(scope); } catch (error) {
      if (this.current(scope)) this.set({ error: `任务状态已更新，但项目刷新失败: ${error.message}` });
    }
  };

  watchJob = (job, scope) => {
    if (!this.current(scope) || this.monitors.has(job.job_id)) return;
    const controller = new AbortController();
    this.monitors.set(job.job_id, controller);
    void monitorJob({
      api: this.api, jobId: job.job_id, signal: controller.signal,
      onJob: (next) => this.upsertJob({ ...job, ...next }, scope),
      onConnectionError: (error) => {
        const last = this.state.jobs.find((item) => item.job_id === job.job_id) || job;
        this.upsertJob({ ...last, poll_error: [404, 410].includes(error.status)
          ? "后端已找不到此任务，可能已重启；状态未知，请刷新确认。"
          : `连接中断，正在重试: ${error.message}` }, scope);
      },
    }).then(async (finished) => {
      if (!this.current(scope) || !finished) return;
      this.message(`${finished.job_type}: ${jobStatusText(finished)}${finished.error ? `；${finished.error}` : ""}`);
      if (finished.status === "failed") this.set({ error: `任务失败: ${finished.error || finished.message || finished.job_type}` });
      await this.refreshAfterJob(scope);
    }).finally(() => {
      if (this.monitors.get(job.job_id) === controller) this.monitors.delete(job.job_id);
    });
  };

  acceptJob = (job, scope) => {
    if (!job?.job_id) throw new Error("后端未返回任务编号");
    const scoped = { ...job, project_id: job.project_id || scope.projectId, activity_id: job.activity_id || scope.activityId };
    this.upsertJob(scoped, scope);
    if (isTerminalJob(scoped)) {
      if (scoped.status === "failed" && this.current(scope)) this.set({ error: `任务失败: ${scoped.error || scoped.message || scoped.job_type}` });
      void this.refreshAfterJob(scope);
    } else this.watchJob(scoped, scope);
    return scoped;
  };
  generateCover = (input, { expectedScope } = {}) => {
    if (!this.matchesEditorScope(expectedScope)) return Promise.resolve(null);
    return this.submitProjectJob("/covers/render", input, "生成封面");
  };
  submitProjectJob = (path, input, label) => this.runAction(label, async (scope) => {
    const payload = await this.api.post(`/projects/${encodeURIComponent(scope.projectId)}${path}`, { ...input, activity_id: scope.activityId });
    if (this.current(scope)) this.acceptJob(payload.job, scope);
    return payload;
  });
  cancelJob = (jobId) => this.runAction("停止任务", async (scope) => {
    const payload = await this.api.post(`/jobs/${encodeURIComponent(jobId)}/cancel`, { activity_id: scope.activityId });
    if (this.current(scope)) this.acceptJob(payload.job, scope);
    return payload;
  });
  runAgentCommand = (command) => this.runAction("Agent 命令", async (scope) => {
    this.message(command, "user");
    const payload = await this.api.post("/agent/command", { project_id: scope.projectId, activity_id: scope.activityId, command });
    if (!this.current(scope)) return null;
    if (payload.projects) this.set({ projects: payload.projects });
    if (payload.summary) this.message(payload.summary);
    if (payload.requires_confirmation) this.message("需要确认后才会执行。");
    const nextId = payload.project?.project_id;
    const nextActivity = payload.project?.current_activity_id || payload.status?.current_activity_id;
    if (payload.deleted && payload.project_id === scope.projectId) {
      this.persist("selection", {});
      if (payload.projects?.[0]) await this.openProject(payload.projects[0].project_id);
      else { this.beginScope(); this.set({ loading: false }); }
      return null;
    }
    if ((nextId && nextId !== scope.projectId) || (nextActivity && nextActivity !== scope.activityId)) {
      await this.openProject(nextId || scope.projectId, nextActivity);
      return null;
    }
    if (payload.job) this.acceptJob(payload.job, scope);
    if (!payload.project && !payload.job) await this.loadSnapshot(scope);
    return payload;
  }, { requiresProject: false });
}
