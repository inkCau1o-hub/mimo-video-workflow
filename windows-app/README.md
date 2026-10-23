# MiMo Workflow Windows 开发版

与 `macos-app/` 并列。所有入口均从自身位置定位项目，可从其他工作目录运行，支持中文、空格和常见特殊字符路径。macOS 入口与虚拟环境不变。

## 安装与检查

需要 Python 3.10+、Node.js（含 npm）、ffmpeg 和 ffprobe。脚本会合并当前与系统/用户 PATH，实际运行工具验证版本和退出码，不会自行安装或重复安装系统工具。

从项目根目录运行，或双击外层 `安装环境.bat`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows-app\安装环境.ps1
```

- 优先使用 `.venv-windows`，兼容已有的 Windows `.venv`，不使用 macOS `.venv`。
- 不仅检查 `python.exe` 是否存在，还验证解释器可运行、Python 版本、环境位置、基础解释器和 pip。移动后失效的环境会先改名为 `.invalid-时间-随机值` 备份，再创建新环境；不会删除旧环境。
- Python requirements 已满足时不重装；额外执行 `pip check` 检查依赖冲突。
- npm 版本依赖及 esbuild/Rollup 原生模块均通过时不重装。失败时优先使用锁文件执行 `npm ci --include=optional`，无锁文件才使用 `npm install`。
- pip、npm、构建、编译及 smoke 任一步失败都会返回非零退出码，不会继续报告成功。
- `-BuildFrontend` 构建前端；`-RunSmoke` 执行项目 smoke 测试。请求的测试文件缺失时明确失败。
- `-SkipPythonDeps`、`-SkipNpmInstall` 是显式跳过对应检查/安装，不代表该部分依赖已验证。
- `-VenvDirName` 可指定项目内的单个目录名，启动时也须传入相同名称。不接受上级路径、绝对路径或对联接点执行修复移动。

## 启动

双击外层 `启动开发版App.bat`，或运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows-app\启动开发版.ps1
```

默认后端 `127.0.0.1:8765`、前端 `127.0.0.1:5173`。默认打开：

```text
http://127.0.0.1:5173/?auto_open=latest
```

换端口或只检查启动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows-app\启动开发版.ps1 -BackendPort 8766 -FrontendPort 5174 -NoBrowser -SmokeOnly
```

- 端口必须在 1 到 65535 之间且互不相同。占用时明确失败，不自动换号，不复用或停止任何已有服务，包括其他目录启动的 MiMo。
- 前端使用本地 `node_modules/vite/bin/vite.js`，启用 `--strictPort`，仅监听回环地址；`VITE_BACKEND_URL` 只写入该前端子进程的环境。
- 后端传入 `--frontend-port <实际前端端口>`。后端合同：`/health` 返回 JSON `{ "ok": true, "service": "mimo-workflow-backend" }`；允许指定端口的 `http://127.0.0.1` 和 `http://localhost` 来源。启动器实测身份与 CORS 响应，不修改服务器代码。
- 前端检查 MiMo HTML 标识和 Vite 客户端；前后端监听者都必须属于本次启动的 Windows Job Object。
- Ctrl+C、关闭窗口、启动失败、服务提前退出或强制结束启动器时，Windows Job Object 清理本次启动的整个进程树，不按端口或进程名批量停止服务。
- `-SmokeOnly` 不打开浏览器，检查成功后退出并清理；`-NoBrowser` 只禁止打开浏览器。
- `start-frontend.bat` 使用同一套逻辑进入 `-FrontendOnly -NoBrowser` 模式，不启动后端。可传 `-BackendPort`、`-FrontendPort`；后端可单独启动，因此仅前端模式不验证后端健康和 CORS。

## 密钥与日志

已有 `MIMO_API_KEY` 环境变量优先。未设置时，启动器先在项目目录、再在外层目录查找 `mimo*.txt`：第一条非空、非注释、非 URL 行作为密钥，第一条 HTTP(S) URL 行作为 `MIMO_BASE_URL`（已有 URL 环境变量优先）。同一目录存在多个候选文件时明确报错，不猜测使用哪个。

文件只在准备启动后端时读取，内容只放入后端独立的环境映射，不写回父进程、前端或浏览器环境。文件中的密钥也参与写盘前脱敏。`-SkipCredentialFile` 完全禁用密钥文件读取，供无凭据 smoke 测试使用；`-FrontendOnly` 从不读取密钥文件。

只有后端继承启动环境中的凭据。前端、浏览器打开助手、工具检查和安装子进程移除名称包含 key、token、secret、password、credential 的环境变量，以及 `MIMO_*`、继承的 `VITE_*`。启动器不会把密钥加入命令行或写回父环境。第三方工具如需私有源凭据，应另行配置受控安装流程。

每次启动日志独立放在 `windows-app/logs/时间-PID-随机值/`。stdout/stderr 写盘前替换已知环境凭据及常见凭据字段，避免并发覆盖日志。脱敏不是任意应用内容的保密保证：不要在日志或业务文本中主动输出未知密钥，不要公开日志、`.env`、配置、模型、项目输出或虚拟环境。

## 回归测试

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows-app\tests\Test-Reliability.ps1 -Integration
```

测试只使用合成密钥，不读取用户密钥文件。覆盖命令退出码、参数引用、编码、虚拟环境修复、端口/身份/CORS、固定端口、日志脱敏、凭据隔离、批处理移动路径、子孙进程清理、强制退出和真实前后端 smoke。测试占用的端口由系统动态分配，清理仅针对自己创建的进程及 `tests/.runtime-*` 临时目录。

PowerShell 文件使用 UTF-8 BOM + CRLF，供 Windows PowerShell 5.1 识别中文；批处理使用无 BOM UTF-8 + CRLF，并启用代码页 65001。外层批处理支持 `MIMO_NO_PAUSE=1`，保留真实退出码。
