export const isTerminalJob = (job) => ["succeeded", "failed", "cancelled"].includes(job?.status);

export function jobStatusText(job) {
  if (!job) return "等待开始";
  const labels = { queued: "排队中", running: "执行中", succeeded: "已完成", failed: "失败", cancelled: "已取消" };
  return job.cancel_requested && !isTerminalJob(job) ? "正在停止" : labels[job.status] || job.status;
}

export function waitForPoll(ms, signal) {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    signal.addEventListener("abort", finish, { once: true });
  });
}

// A lost connection is not a failed render. Only the server decides a job's final state.
export async function monitorJob({ api, jobId, signal, onJob, onConnectionError, wait = waitForPoll }) {
  let failures = 0;
  while (!signal.aborted) {
    try {
      const { job } = await api.get(`/jobs/${encodeURIComponent(jobId)}`, { signal });
      if (signal.aborted) return null;
      if (!job?.job_id) throw new Error("任务响应缺少 job_id");
      failures = 0;
      onJob({ ...job, poll_error: "" });
      if (isTerminalJob(job)) return job;
    } catch (error) {
      if (signal.aborted) return null;
      failures += 1;
      onConnectionError(error);
      if ([404, 410].includes(error.status)) return null;
    }
    await wait(Math.min(15000, failures ? 1000 * 2 ** Math.min(failures, 4) : 1500), signal);
  }
  return null;
}
