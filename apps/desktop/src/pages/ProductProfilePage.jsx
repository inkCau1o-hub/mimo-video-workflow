import { useEffect, useMemo, useState } from "react";
import { ProductProfileForm, emptyProductProfile, missingRequiredProfileFields } from "../components/ProductProfileForm";
import { productProfile } from "../workflowData";

export function ProductProfilePage({ project, onSaveProductProfile }) {
  const savedProfile = useMemo(() => ({ ...emptyProductProfile(), ...productProfile(project) }), [project]);
  const [profile, setProfile] = useState(savedProfile);
  const [submitted, setSubmitted] = useState(false);
  const missingFields = missingRequiredProfileFields(profile);

  useEffect(() => {
    setProfile(savedProfile);
    setSubmitted(false);
  }, [savedProfile]);

  const save = () => {
    setSubmitted(true);
    if (missingFields.length) return;
    onSaveProductProfile?.(profile);
  };

  return (
    <div className="page-grid single-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>项目资料</h2>
            <p>{project ? "这些信息会进入后续 AI 改写、标题生成和分镜分析。" : "请先创建或打开项目。"}</p>
          </div>
          <button type="button" className="primary" onClick={save} disabled={!project}>
            保存资料
          </button>
        </div>
        <ProductProfileForm disabled={!project} profile={profile} onChange={setProfile} />
        {submitted && missingFields.length ? (
          <p className="error-text">请补全：{missingFields.join("、")}</p>
        ) : null}
      </section>
    </div>
  );
}
