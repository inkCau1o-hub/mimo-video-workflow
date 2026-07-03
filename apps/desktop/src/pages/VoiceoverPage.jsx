export function VoiceoverPage({ activity, project, onGenerateVoiceovers }) {
  const analysis = activity?.storyboard_analysis || project?.storyboard_analysis;
  const shots = analysis?.shots || [];
  const segments = activity?.voiceover_segments || project?.voiceover_segments || [];
  const canGenerate = Boolean(project && shots.length);

  return (
    <div className="page-grid single-column">
      <section className="workspace-section">
        <div className="section-header">
          <div>
            <h2>配音</h2>
            <p>按分镜的 tts_text 生成配音片段。</p>
          </div>
          <button type="button" className="primary" onClick={onGenerateVoiceovers} disabled={!canGenerate}>
            生成配音
          </button>
        </div>
        <div className="source-preview">
          <div className="queue-header">
            <strong>当前分镜旁白</strong>
            <span>{shots.length} 条</span>
          </div>
          {shots.length === 0 ? (
            <p>请先在分镜分析页生成分镜。</p>
          ) : (
            <div className="shot-list compact-list">
              {shots.map((shot) => (
                <article className="shot-row" key={shot.shot_id}>
                  <strong>{shot.shot_id} · {shot.title}</strong>
                  <p>{shot.tts_text || shot.script_text}</p>
                  <span>{shot.duration_seconds}s</span>
                </article>
              ))}
            </div>
          )}
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
