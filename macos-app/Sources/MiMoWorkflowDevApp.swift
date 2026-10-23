import Cocoa
import Foundation
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var backendProcess: Process?
    private var frontendProcess: Process?
    private var logHandles: [FileHandle] = []
    private var readinessTimer: Timer?
    private var isLoadingFrontend = false
    private var startedAt = Date()

    private let repositoryRoot: URL?
    private let backendPort: Int
    private let frontendPort: Int

    private var backendHealthURL: URL {
        URL(string: "http://127.0.0.1:\(backendPort)/health")!
    }

    private var frontendURL: URL {
        URL(string: "http://127.0.0.1:\(frontendPort)/?auto_open=latest")!
    }

    override init() {
        let info = Bundle.main.infoDictionary ?? [:]
        repositoryRoot = Self.resolveRepositoryRoot()
        backendPort = Int((info["BackendPort"] as? String) ?? "8765") ?? 8765
        frontendPort = Int((info["FrontendPort"] as? String) ?? "5173") ?? 5173
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        configureMenu()
        createWindow()
        NSApp.activate(ignoringOtherApps: true)

        showStatus("正在启动本地服务...", details: "后端 \(backendPort)，前端 \(frontendPort)")
        startServicesIfNeeded()
        waitForServices()
    }

    func applicationWillTerminate(_ notification: Notification) {
        readinessTimer?.invalidate()
        terminateProcessTree(frontendProcess)
        terminateProcessTree(backendProcess)
        for handle in logHandles {
            try? handle.close()
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    private static func resolveRepositoryRoot() -> URL? {
        let bundleURL = Bundle.main.bundleURL.standardizedFileURL
        var candidate = bundleURL.deletingLastPathComponent()

        for _ in 0..<10 {
            if isRepositoryRoot(candidate) {
                return candidate.standardizedFileURL
            }
            candidate.deleteLastPathComponent()
        }

        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath).standardizedFileURL
        if isRepositoryRoot(currentDirectory) {
            return currentDirectory
        }
        return nil
    }

    private static func isRepositoryRoot(_ url: URL) -> Bool {
        let backendServer = url.appendingPathComponent("backend/server.py").path
        let desktopPackage = url.appendingPathComponent("apps/desktop/package.json").path
        return FileManager.default.fileExists(atPath: backendServer)
            && FileManager.default.fileExists(atPath: desktopPackage)
    }

    private func configureMenu() {
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(
            NSMenuItem(
                title: "退出 MiMo Workflow Dev",
                action: #selector(NSApplication.terminate(_:)),
                keyEquivalent: "q"
            )
        )
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)
        NSApp.mainMenu = mainMenu
    }

    private func createWindow() {
        let configuration = WKWebViewConfiguration()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = true
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "MiMo Workflow Dev"
        window.center()
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
    }

    private func startServicesIfNeeded() {
        guard let repositoryRoot else {
            showStatus(
                "无法找到项目目录",
                details: "请移动整个项目文件夹，并从项目内的 macos-app/build 打开此 App；不要只单独移动 .app。"
            )
            return
        }

        let desktopDir = repositoryRoot.appendingPathComponent("apps/desktop").path
        let logsDir = repositoryRoot.appendingPathComponent("macos-app/build/logs").path
        try? FileManager.default.createDirectory(atPath: logsDir, withIntermediateDirectories: true)

        if !httpAvailable(backendHealthURL, timeout: 0.8) {
            backendProcess = launchProcess(
                name: "backend",
                executable: "/usr/bin/env",
                arguments: ["python3", "-m", "backend.server", "--host", "127.0.0.1", "--port", "\(backendPort)", "--quiet"],
                workingDirectory: repositoryRoot.path,
                logPath: "\(logsDir)/backend.log"
            )
        }

        if !httpAvailable(frontendURL, timeout: 0.8) {
            frontendProcess = launchProcess(
                name: "frontend",
                executable: "/usr/bin/env",
                arguments: ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "\(frontendPort)"],
                workingDirectory: desktopDir,
                logPath: "\(logsDir)/frontend.log"
            )
        }
    }

    private func waitForServices() {
        readinessTimer?.invalidate()
        startedAt = Date()
        readinessTimer = Timer.scheduledTimer(withTimeInterval: 0.8, repeats: true) { [weak self] timer in
            guard let self else { return }
            DispatchQueue.global(qos: .userInitiated).async {
                let backendReady = self.httpAvailable(self.backendHealthURL, timeout: 1.0)
                let frontendReady = self.httpAvailable(self.frontendURL, timeout: 1.0)
                DispatchQueue.main.async {
                    if backendReady && frontendReady {
                        timer.invalidate()
                        self.loadFrontend()
                        return
                    }

                    let elapsed = Int(Date().timeIntervalSince(self.startedAt))
                    self.showStatus(
                        "正在等待本地服务...",
                        details: "后端: \(backendReady ? "已就绪" : "启动中") · 前端: \(frontendReady ? "已就绪" : "启动中") · \(elapsed)s"
                    )
                }
            }
        }
    }

    private func loadFrontend() {
        if isLoadingFrontend {
            return
        }
        isLoadingFrontend = true
        window.title = "MiMo Workflow Dev - \(frontendURL.absoluteString)"
        webView.load(URLRequest(url: frontendURL, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 15))
    }

    private func showStatus(_ title: String, details: String) {
        let html = """
        <!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="utf-8">
          <style>
            body {
              margin: 0;
              min-height: 100vh;
              display: grid;
              place-items: center;
              background: #f5f5f7;
              color: #1d1d1f;
              font: 15px -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
            }
            main {
              width: min(520px, calc(100vw - 48px));
              padding: 28px;
              border: 1px solid #e5e5ea;
              border-radius: 18px;
              background: rgba(255,255,255,0.86);
              box-shadow: 0 18px 60px rgba(0,0,0,0.10);
            }
            h1 { margin: 0 0 10px; font-size: 24px; letter-spacing: 0; }
            p { margin: 0; color: #6e6e73; line-height: 1.6; }
          </style>
        </head>
        <body>
          <main>
            <h1>\(htmlEscape(title))</h1>
            <p>\(htmlEscape(details))</p>
          </main>
        </body>
        </html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    private func launchProcess(
        name: String,
        executable: String,
        arguments: [String],
        workingDirectory: String,
        logPath: String
    ) -> Process? {
        guard FileManager.default.isExecutableFile(atPath: executable) else {
            showStatus("无法启动 \(name)", details: "找不到可执行文件: \(executable)")
            return nil
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.currentDirectoryURL = URL(fileURLWithPath: workingDirectory)
        process.environment = processEnvironment()

        FileManager.default.createFile(atPath: logPath, contents: nil)
        if let handle = try? FileHandle(forWritingTo: URL(fileURLWithPath: logPath)) {
            logHandles.append(handle)
            process.standardOutput = handle
            process.standardError = handle
        }

        do {
            try process.run()
            return process
        } catch {
            showStatus("无法启动 \(name)", details: error.localizedDescription)
            return nil
        }
    }

    private func processEnvironment() -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let fallbackPath = [
            "\(home)/.local/bin",
            "\(home)/.npm-global/bin",
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ].joined(separator: ":")
        if let currentPath = environment["PATH"], !currentPath.isEmpty {
            environment["PATH"] = "\(fallbackPath):\(currentPath)"
        } else {
            environment["PATH"] = fallbackPath
        }
        environment["PYTHONUNBUFFERED"] = "1"
        return environment
    }

    private func httpAvailable(_ url: URL, timeout: TimeInterval) -> Bool {
        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = timeout

        let semaphore = DispatchSemaphore(value: 0)
        var available = false
        URLSession.shared.dataTask(with: request) { _, response, _ in
            if let httpResponse = response as? HTTPURLResponse {
                available = (200..<500).contains(httpResponse.statusCode)
            }
            semaphore.signal()
        }.resume()

        _ = semaphore.wait(timeout: .now() + timeout + 0.2)
        return available
    }

    private func terminateProcessTree(_ process: Process?) {
        guard let process, process.isRunning else {
            return
        }

        let childKiller = Process()
        childKiller.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        childKiller.arguments = ["-TERM", "-P", "\(process.processIdentifier)"]
        try? childKiller.run()
        childKiller.waitUntilExit()

        process.terminate()
    }

    private func htmlEscape(_ value: String) -> String {
        value
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
            .replacingOccurrences(of: "\"", with: "&quot;")
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
