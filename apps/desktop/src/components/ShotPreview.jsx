import { useEffect, useRef, useState } from "react";
import { Pause, Play, RotateCcw } from "lucide-react";
import { projectMediaUrl } from "../workflowData.js";
import { enforcePreviewRange, previewRange, isImageAsset } from "../editor/shotEditing.js";
import { EditorButton } from "./EditorButton.jsx";
import { subtitleAtTime } from "../editor/subtitleEditing.js";

export function ShotPreview({ backendUrl, projectId, asset, edit, aspectRatio, onDuration, timing }) {
  const videoRef = useRef(null);
  const frameRef = useRef(null);
  const [duration, setDuration] = useState(null);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState("");
  const [width, setWidth] = useState(240);
  const source = projectMediaUrl(backendUrl, projectId, asset?.file_path);
  const range = previewRange(edit, duration || asset?.duration_seconds);
  const rangeRef = useRef(range);
  rangeRef.current = range;

  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    if (frameRef.current) observer.observe(frameRef.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const video = videoRef.current;
    video?.pause();
    setPlaying(false);
    if (video?.readyState >= 1) video.currentTime = range.start;
    setTime(range.start);
  }, [source, range.start, range.end]);
  useEffect(() => {
    if (!playing) return undefined;
    let frame;
    const tick = () => {
      enforcePreviewRange(videoRef.current, rangeRef.current);
      if (videoRef.current && !videoRef.current.paused) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing]);

  const togglePlayback = async () => {
    const video = videoRef.current;
    if (!video || !range.end) return;
    if (!video.paused) { video.pause(); return; }
    if (video.currentTime < range.start || video.currentTime >= range.end - 0.04) video.currentTime = range.start;
    try { await video.play(); setError(""); } catch { setError("预览暂时无法播放，请检查素材格式或路径。"); }
  };
  const [ratioWidth, ratioHeight] = aspectRatio.split(":").map(Number);
  const subtitleSize = Number(edit.subtitle.font_size) * Math.min(width, width * ratioHeight / ratioWidth) / 720;
  const caption = subtitleAtTime(edit, time - range.start, timing);

  return <div className="shot-preview">
    <div className="shot-preview-frame" ref={frameRef} style={{ aspectRatio: `${ratioWidth} / ${ratioHeight}` }}>
      {source && isImageAsset(asset) ? <img src={source} alt="图片素材预览"
        style={{ objectFit: edit.fit, objectPosition: edit.fit === "contain" ? "50% 50%" : `${Number(edit.crop_x) * 100}% ${Number(edit.crop_y) * 100}%` }}
        onError={() => setError("图片预览加载失败，请替换素材或检查路径。")} />
      : source ? <video ref={videoRef} src={source} muted playsInline preload="metadata" aria-label="镜头选段预览"
        style={{ objectFit: edit.fit, objectPosition: edit.fit === "contain" ? "50% 50%" : `${Number(edit.crop_x) * 100}% ${Number(edit.crop_y) * 100}%` }}
        onLoadedMetadata={(event) => {
          const video = event.currentTarget;
          if (Number.isFinite(video.duration)) { setDuration(video.duration); onDuration?.(video.duration); }
          video.currentTime = previewRange(edit, video.duration).start;
        }}
        onPlay={(event) => { enforcePreviewRange(event.currentTarget, rangeRef.current); setPlaying(!event.currentTarget.paused); }}
        onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
        onSeeking={(event) => enforcePreviewRange(event.currentTarget, rangeRef.current)}
        onTimeUpdate={(event) => { enforcePreviewRange(event.currentTarget, rangeRef.current); setTime(event.currentTarget.currentTime); }}
        onError={() => { setError("素材预览加载失败，请替换素材或检查路径。"); setPlaying(false); }}
      /> : <span className="preview-placeholder">{asset ? "素材路径缺失" : "自动匹配素材"}</span>}
      {caption ? <div className={`shot-subtitle subtitle-${edit.subtitle.position}`} style={{ fontSize: `${subtitleSize}px`, color: /^#[0-9a-f]{6}$/i.test(edit.subtitle.color) ? edit.subtitle.color : "#FFFFFF" }}>
        <span className={edit.subtitle.background ? "with-background" : ""}>{caption}</span>
      </div> : null}
    </div>
    <div className="preview-transport">
      <EditorButton icon={playing ? Pause : Play} label={playing ? "暂停选段" : "播放选段"} disabled={!source || !range.end || Boolean(error)} onClick={togglePlayback} />
      <EditorButton icon={RotateCcw} label="返回选段起点" disabled={!source || !range.end} onClick={() => {
        videoRef.current.pause(); videoRef.current.currentTime = range.start; setTime(range.start);
      }} />
      <output>{time.toFixed(2)} / {range.end?.toFixed(2) || "--"} s</output>
      <input aria-label="选段播放位置" type="range" min={range.start} max={range.end || range.start + 1} step="0.01" value={Math.max(range.start, Math.min(time, range.end || range.start))} disabled={!source || !range.end}
        onChange={(event) => { videoRef.current.currentTime = Number(event.target.value); setTime(Number(event.target.value)); }} />
    </div>
    {error ? <p className="error-text" role="alert">{error}</p> : null}
  </div>;
}
