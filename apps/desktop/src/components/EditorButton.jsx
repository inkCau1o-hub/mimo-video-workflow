export function EditorButton({ icon: Icon, label, children, className = "", ...props }) {
  return <button type="button" title={label} aria-label={label} className={`editor-button ${children ? "" : "icon-button"} ${className}`} {...props}>
    <Icon size={16} aria-hidden="true" />{children}
  </button>;
}
