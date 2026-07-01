export function VoiceoverPage({ project, onGenerateVoiceovers }) {
  const segments = project?.voiceover_segments || [];

  return (
    <div className="page-grid single-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>配音</h2>
            <p>按分镜的 tts_text 生成配音片段。</p>
          </div>
          <button type="button" className="primary" onClick={onGenerateVoiceovers}>
            生成配音
          </button>
        </div>
        <div className="asset-list">
          {segments.length === 0 ? (
            <div className="empty-state">等待生成配音。</div>
          ) : (
            segments.map((segment) => (
              <article className="asset-row" key={segment.segment_id}>
                <strong>{segment.shot_id}</strong>
                <span>{segment.audio_path}</span>
                <p>{segment.text}</p>
              </article>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
