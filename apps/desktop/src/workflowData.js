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
