# MiMo Workflow Dev macOS App

This folder contains a development macOS app wrapper for the existing React + Python workflow.

The app is intentionally a dev prototype:

- It opens a native macOS window with `WKWebView`.
- It starts the local Python backend on `127.0.0.1:8765` when needed.
- It starts the Vite frontend on `127.0.0.1:5173` when needed.
- It loads the existing desktop web UI with `auto_open=latest&start_page=voiceover`, so the latest project opens directly on the voiceover page for playback and per-segment regeneration checks.

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
