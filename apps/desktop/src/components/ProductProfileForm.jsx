export const PRODUCT_PROFILE_FIELDS = [
  ["product_name", "产品名称", true, "例如：杜亚智能窗帘"],
  ["brand_name", "品牌/店铺名称", false, "例如：杜亚官方旗舰店"],
  ["features", "产品特点", true, "产品客观特征，例如：静音电机、智能控制、稳定耐用"],
  ["selling_points", "核心卖点", true, "最希望文案反复强化的价值点"],
  ["target_audience", "目标人群", false, "例如：装修用户、智能家居用户"],
  ["scenarios", "使用场景", false, "例如：卧室、客厅、全屋智能"],
  ["promotion", "价格/活动信息", false, "例如：源头工厂活动、套餐权益"],
  ["rewrite_requirements", "转写/改写要求", true, "例如：保留核心信息，适合短视频口播，控制在300字以内"],
  ["tone", "文案风格", false, "例如：口语化、可信赖、种草感"],
  ["forbidden_claims", "禁用表达/不能夸大", false, "不能承诺的效果、不能说的词"],
];

export function emptyProductProfile() {
  return PRODUCT_PROFILE_FIELDS.reduce((profile, [key]) => ({ ...profile, [key]: "" }), {});
}

export function missingRequiredProfileFields(profile) {
  return PRODUCT_PROFILE_FIELDS
    .filter(([, , required]) => required)
    .filter(([key]) => !String(profile?.[key] || "").trim())
    .map(([, label]) => label);
}

export function ProductProfileForm({ disabled = false, profile, onChange }) {
  const update = (key, value) => {
    onChange?.({ ...(profile || {}), [key]: value });
  };

  return (
    <div className="profile-form">
      {PRODUCT_PROFILE_FIELDS.map(([key, label, required, placeholder]) => (
        <label className="profile-field" key={key}>
          <span>
            {label}
            {required ? <em>必填</em> : null}
          </span>
          {key === "product_name" || key === "brand_name" ? (
            <input
              value={profile?.[key] || ""}
              onChange={(event) => update(key, event.target.value)}
              placeholder={placeholder}
              disabled={disabled}
            />
          ) : (
            <textarea
              className="mini-textarea"
              value={profile?.[key] || ""}
              onChange={(event) => update(key, event.target.value)}
              placeholder={placeholder}
              disabled={disabled}
            />
          )}
        </label>
      ))}
    </div>
  );
}
