export function pickReferenceCopy(activity, project) {
  return activity?.reference_copy || project?.reference_copy || null;
}

export function pickStoryboardAnalysis(activity, project) {
  return activity?.storyboard_analysis || project?.storyboard_analysis || null;
}

export function pickAssemblyPlan(activity, project) {
  return activity?.assembly_plan || project?.assembly_plan || null;
}

export function pickList(activity, project, key) {
  const activityList = activity?.[key];
  if (Array.isArray(activityList) && activityList.length > 0) return activityList;
  const projectList = project?.[key];
  if (Array.isArray(projectList) && projectList.length > 0) return projectList;
  return [];
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
