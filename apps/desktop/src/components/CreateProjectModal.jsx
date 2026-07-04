import { useEffect, useMemo, useState } from "react";
import { ProductProfileForm, emptyProductProfile, missingRequiredProfileFields } from "./ProductProfileForm";

export function CreateProjectModal({ onClose, onCreate, open }) {
  const [projectName, setProjectName] = useState("");
  const [profile, setProfile] = useState(() => emptyProductProfile());
  const [submitted, setSubmitted] = useState(false);
  const missingFields = useMemo(() => missingRequiredProfileFields(profile), [profile]);

  useEffect(() => {
    if (!open) return;
    setProjectName("");
    setProfile(emptyProductProfile());
    setSubmitted(false);
  }, [open]);

  if (!open) return null;

  const submit = (event) => {
    event.preventDefault();
    setSubmitted(true);
    if (missingFields.length) return;
    const productName = profile.product_name.trim();
    onCreate?.({
      name: projectName.trim() || productName,
      product_profile: profile,
    });
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-panel" role="dialog" aria-modal="true" aria-label="新建项目">
        <form onSubmit={submit}>
          <div className="section-header">
            <div>
              <h2>新建项目</h2>
              <p>先固定产品资料，后续转写、改写、标题和分镜都会读取这里。</p>
            </div>
            <button type="button" onClick={onClose}>
              取消
            </button>
          </div>
          <label className="profile-field">
            <span>项目名称</span>
            <input
              value={projectName}
              onChange={(event) => setProjectName(event.target.value)}
              placeholder="不填时使用产品名称"
            />
          </label>
          <ProductProfileForm profile={profile} onChange={setProfile} />
          {submitted && missingFields.length ? (
            <p className="error-text">请补全：{missingFields.join("、")}</p>
          ) : null}
          <div className="modal-actions">
            <button type="button" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="primary">
              创建项目
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
