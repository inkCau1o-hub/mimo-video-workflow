import { useState } from "react";

export function AgentConsole({ onCommand, messages }) {
  const [command, setCommand] = useState("");

  const submit = (event) => {
    event.preventDefault();
    const value = command.trim();
    if (!value) return;
    onCommand(value);
    setCommand("");
  };

  return (
    <section className="agent-console">
      <div className="panel-title">AI 控制台</div>
      <div className="agent-stream">
        {messages.length === 0 ? (
          <div className="muted">输入“帮助”查看项目和活动命令。</div>
        ) : (
          messages.map((message, index) => (
            <div className={`agent-message ${message.role}`} key={`${message.role}-${index}`}>
              {message.text}
            </div>
          ))
        )}
      </div>
      <form className="agent-input-row" onSubmit={submit}>
        <input
          value={command}
          onChange={(event) => setCommand(event.target.value)}
          placeholder="状态 / 创建活动 / 生成分镜 / 生成配音"
        />
        <button type="submit">执行</button>
      </form>
    </section>
  );
}
