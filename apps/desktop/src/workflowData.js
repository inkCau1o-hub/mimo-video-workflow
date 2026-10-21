export function pickReferenceCopy(activity, project) {
  return (activity ? activity.reference_copy : project?.reference_copy) || null;
}

export function pickStoryboardAnalysis(activity, project) {
  return (activity ? activity.storyboard_analysis : project?.storyboard_analysis) || null;
}

export function pickAssemblyPlan(activity, project) {
  return (activity ? activity.assembly_plan : project?.assembly_plan) || null;
}

export function pickList(activity, project, key) {
  const list = activity ? activity[key] : project?.[key];
  return Array.isArray(list) ? list : [];
}

export function referenceText(activity, project) {
  return pickReferenceCopy(activity, project)?.source_text || "";
}

export function copywriting(activity) {
  return activity?.copywriting || {};
}

export function rewrittenCopy(activity) {
  return copywriting(activity)?.rewrite?.text || "";
}

export function generatedTitles(activity) {
  const items = copywriting(activity)?.titles?.items;
  return Array.isArray(items) ? items : [];
}

export function finalCopyText(activity, project) {
  return rewrittenCopy(activity) || referenceText(activity, project);
}

export function productProfile(project) {
  return project?.product_profile || {};
}

export function productProfileComplete(project) {
  const profile = productProfile(project);
  return ["product_name", "features", "selling_points", "rewrite_requirements"].every((key) =>
    String(profile[key] || "").trim()
  );
}

export function projectMediaUrl(backendUrl, projectId, path, version = "") {
  if (!backendUrl || !projectId || !path) return "";
  return `${backendUrl.replace(/\/$/, "")}/projects/${encodeURIComponent(projectId)}/media?path=${encodeURIComponent(path)}${version ? `&v=${encodeURIComponent(version)}` : ""}`;
}

export function renderResult(activity, project, job) {
  if (activity || project) return activity?.metadata?.render_result || pickAssemblyPlan(activity, project)?.metadata?.render_result || null;
  if (job?.status === "succeeded" && job.result?.output_path) return job.result;
  return null;
}

export function updateShot(shots, shotId, change) {
  return shots.map((shot) => shot.shot_id === shotId
    ? { ...shot, ...change, metadata: { ...shot.metadata, ...change.metadata } }
    : shot);
}

export function validShots(shots) {
  return shots.length > 0 && shots.every((shot) => Number.isFinite(Number(shot.duration_seconds)) && Number(shot.duration_seconds) > 0);
}
