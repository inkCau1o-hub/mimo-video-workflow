import { MODULE_INFO, describeModule, findModule } from "../launcher/moduleState.js";

export function HomePage({ modules, onOpen }) {
  const cards = [MODULE_INFO.feed, MODULE_INFO.animation].map((info) => {
    const view = info.id === "feed"
      ? { state: "running", label: "可用", ready: true, message: "" }
      : describeModule(findModule(modules, info.id));
    const blocked = view.state === "unavailable";
    return { info, view, blocked };
  });

  return (
    <main className="home-page">
      <header className="home-header">
        <h1>MiMo Workflow</h1>
        <p>选择要使用的功能。两个功能各自保存项目，互不影响。</p>
      </header>
      <div className="home-cards">
        {cards.map(({ info, view, blocked }) => (
          <section key={info.id} className={blocked ? "home-card disabled" : "home-card"} aria-disabled={blocked}>
            <div className="home-card-head">
              <h2>{info.name}</h2>
              <span className={`module-pill ${view.state}`}>{view.label}</span>
            </div>
            <p>{info.description}</p>
            {blocked ? <p className="home-card-note" role="note">{view.message}</p> : null}
            <button type="button" className="primary" disabled={blocked} onClick={() => onOpen(info.id)}>
              进入{info.name}
            </button>
          </section>
        ))}
      </div>
    </main>
  );
}
