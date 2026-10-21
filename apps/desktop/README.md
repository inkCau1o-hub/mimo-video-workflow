# MiMo Desktop 开发版

MiMo 工作流的 React 开发版界面。

This app does not replace the existing Tkinter GUI. It talks to the local Python
workflow backend over HTTP:

```bash
python -m backend.server --host 127.0.0.1 --port 8765
cd apps/desktop
npm install
npm run dev
```

Default backend URL:

```text
http://127.0.0.1:8765
```

The future Tauri shell can launch the Python backend as a sidecar and load this
React UI as the desktop window.

## 前端验证

在 `apps/desktop` 运行 `npm test` 和 `npm run build`；在项目根目录运行
`python tests/frontend_prototype_smoke.py`。测试不调用付费模型。

行为测试覆盖编辑保存、活动隔离、任务恢复、长任务轮询、旧后端兼容和渲染结果，
页面测试验证实际 React 输出中的输入控件、媒体地址和错误状态。
桌面与移动浏览器的实际交互由整合测试统一验证。

## 接口约定

活动操作均发送 `activity_id`，上传采用 multipart 的 `videos` 和 `activity_id` 字段。
新接口未部署时显示真实错误；旧后端缺少项目任务列表时仅恢复本机记录的任务。
轮询连接失败会重试，不将等待时长当作后端任务失败。

视频素材上传不自动打标。导出只在后端返回实际渲染结果后显示成片；
内容变更后失效的输出保留在历史版本中。输出路径限项目目录内的 `.mp4`，
留空使用后端自动命名，不覆盖已有文件。
