# MiMo Workflow Dev macOS App

This folder contains a development macOS app wrapper for the existing React + Python workflow.

The app is intentionally a dev prototype:

- It opens a native macOS window with `WKWebView`.
- It starts the local Python backend on `127.0.0.1:8765` when needed.
- It starts the Vite frontend on `127.0.0.1:5173` when needed.
- It loads the desktop web UI with `auto_open=latest`. The home page offers two features: 信息流剪辑 (the MiMo workflow) and AI 动画. The backend starts the AI 动画 service only when that feature is opened, and stops its whole process tree when the backend exits.
- It discovers the project root at runtime by walking upward from the `.app` location, so the whole project folder can be moved without rebuilding the app.

## Build

```bash
./macos-app/build_dev_app.sh
```

Output:

```text
macos-app/build/MiMo Workflow Dev.app
```

## Open

```bash
open -n "macos-app/build/MiMo Workflow Dev.app"
```

You can also double-click the app in Finder.

Keep the app inside this project folder, for example:

```text
macos-app/build/MiMo Workflow Dev.app
```

Moving the whole project folder is supported. Moving only the `.app` out of the project folder is not supported by this dev prototype, because the backend and frontend source files are still loaded from the project tree.

## Smoke Test

```bash
./macos-app/smoke_dev_app.sh
```

The smoke test launches the app and verifies that both local services become reachable.

## Logs

```text
macos-app/build/logs/backend.log
macos-app/build/logs/frontend.log
```

## Notes

This is not the final distributable installer. A production build should later remove the Vite dev server, bundle the frontend as static assets, package the Python backend as a sidecar executable, and add Apple signing/notarization plus a `.dmg` installer.
