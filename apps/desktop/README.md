# MiMo Desktop Prototype

Experimental React frontend for phase 2 of the Tauri/React + Python sidecar plan.

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
