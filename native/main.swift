import AppKit
import WebKit

final class LabDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var webView: WKWebView!
    var backend: Process?
    var stdoutPipe: Pipe?
    var stderrPipe: Pipe?
    var ready = false
    var quitting = false
    var sessionPort: Int?
    var labels: [String: String] = [:]
    var glass: NSView?

    func label(_ key: String, _ fallback: String) -> String { labels[key] ?? fallback }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        setupMenu()
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(self, name: "saveFile")
        configuration.userContentController.add(self, name: "chooseFolder")
        configuration.userContentController.add(self, name: "appearance")
        let defaults = UserDefaults.standard
        let preferences = ["theme": defaults.string(forKey: "ssd-theme") ?? "light",
                           "language": defaults.string(forKey: "ssd-language") ?? "en"]
        if let data = try? JSONSerialization.data(withJSONObject: preferences), let json = String(data: data, encoding: .utf8) {
            configuration.userContentController.addUserScript(WKUserScript(source: "window.SSDNativePreferences = \(json);", injectionTime: .atDocumentStart, forMainFrameOnly: true))
        }
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 920),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        window.title = "SSD Lab"
        window.minSize = NSSize(width: 860, height: 680)
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.appearance = NSAppearance(named: .aqua)
        window.autorecalculatesKeyViewLoop = true
        webView.underPageBackgroundColor = .clear
        if #available(macOS 26.0, *) {
            let material = NSGlassEffectView()
            material.style = .regular
            material.cornerRadius = 24
            material.contentView = webView
            glass = material
            window.contentView = material
        } else {
            let material = NSVisualEffectView()
            material.material = .sidebar
            material.blendingMode = .behindWindow
            material.state = .active
            webView.frame = material.bounds
            webView.autoresizingMask = [.width, .height]
            material.addSubview(webView)
            window.contentView = material
        }
        window.delegate = self
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        webView.loadHTMLString("<html><body style='background:#f2f5fa;font:14px -apple-system;color:#536376;display:grid;place-items:center;height:95vh'><div>SSD Lab · Starting local engine…</div></body></html>", baseURL: nil)
        startBackend()
    }

    func setupMenu() {
        let mainMenu = NSMenu()
        let applicationItem = NSMenuItem()
        mainMenu.addItem(applicationItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: label("about", "About SSD Lab"), action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: label("quit", "Quit SSD Lab"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        applicationItem.submenu = appMenu
        let editItem = NSMenuItem(title: label("edit", "Edit"), action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: editItem.title)
        editMenu.addItem(withTitle: label("undo", "Undo"), action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: label("cut", "Cut"), action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: label("copy", "Copy"), action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: label("paste", "Paste"), action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: label("all", "Select All"), action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        mainMenu.addItem(editItem)
        NSApp.mainMenu = mainMenu
    }

    @objc func showAbout() {
        let alert = NSAlert()
        alert.messageText = "SSD Lab 1.3.1"
        alert.informativeText = label("aboutDescription", "Local SSD performance and health workbench") + "\n\nfio 3.43 · smartmontools 7.5\n\n" + label("affiliation", "Independent project, not affiliated with Crystal Dew World.")
        alert.runModal()
    }

    func startBackend() {
        guard let resources = Bundle.main.resourceURL else { fail("Unable to read application resources."); return }
        let bundledPython = resources.appendingPathComponent("python/bin/python3").path
        let candidates = [bundledPython, "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        guard let python = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
            fail("Python 3 not found. Use the complete app bundle with its runtime."); return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-B", resources.appendingPathComponent("SSD-Lab/app.py").path, "--no-browser"]
        process.currentDirectoryURL = resources.appendingPathComponent("SSD-Lab")
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        environment.removeValue(forKey: "PYTHONPATH")
        if python == bundledPython { environment["PYTHONHOME"] = resources.appendingPathComponent("python").path }
        process.environment = environment
        let output = Pipe(), errors = Pipe()
        stdoutPipe = output; stderrPipe = errors
        process.standardOutput = output; process.standardError = errors
        process.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let self = self, !self.quitting else { return }
                self.fail("Local engine exited (status \(process.terminationStatus)). Reopen the app.")
            }
        }
        do { try process.run(); backend = process }
        catch { fail("Unable to start the local engine: \(error.localizedDescription)"); return }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var buffer = Data()
            while buffer.count < 65536 {
                let chunk = output.fileHandleForReading.availableData
                if chunk.isEmpty { break }
                buffer.append(chunk)
                if let newline = buffer.firstIndex(of: 10) {
                    let firstLine = buffer.prefix(upTo: newline)
                    guard let json = try? JSONSerialization.jsonObject(with: firstLine) as? [String: Any],
                          let address = json["url"] as? String, let url = URL(string: address), url.host == "127.0.0.1" else { break }
                    DispatchQueue.main.async {
                        self?.ready = true
                        self?.sessionPort = url.port
                        self?.webView.load(URLRequest(url: url))
                    }
                    return
                }
            }
            DispatchQueue.main.async { if self?.ready == false { self?.fail("Startup failed. Check whether the app bundle is incomplete or damaged.") } }
        }
        // Drain stderr so a diagnostic burst can never block the backend.
        errors.fileHandleForReading.readabilityHandler = { handle in _ = handle.availableData }
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) { [weak self] in
            if self?.ready == false { self?.fail("Local engine startup timed out. Close and reopen the app.") }
        }
    }

    func fail(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "SSD Lab"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: label("close", "Close"))
        alert.runModal()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let reduced = NSWorkspace.shared.accessibilityDisplayShouldReduceTransparency
        webView.evaluateJavaScript("document.documentElement.classList.add('native-shell'); document.documentElement.classList.toggle('reduce-transparency', \(reduced));", completionHandler: nil)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "about" || (url.scheme == "http" && url.host == "127.0.0.1" && url.port == sessionPort) {
            decisionHandler(.allow)
        } else {
            if ["https", "http"].contains(url.scheme ?? ""), navigationAction.navigationType == .linkActivated {
                NSWorkspace.shared.open(url)
            }
            decisionHandler(.cancel)
        }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame,
              message.frameInfo.request.url?.host == "127.0.0.1", message.frameInfo.request.url?.port == sessionPort,
              ["saveFile", "chooseFolder", "appearance"].contains(message.name) else { return }
        if message.name == "appearance" {
            guard let body = message.body as? [String: String], let theme = body["theme"],
                  ["light", "dark", "miku"].contains(theme) else { return }
            window.appearance = NSAppearance(named: theme == "light" ? .aqua : .darkAqua)
            UserDefaults.standard.set(theme, forKey: "ssd-theme")
            if let language = body["language"], ["en", "zh-CN", "zh-TW", "ja", "ko", "de", "fr", "es"].contains(language) {
                UserDefaults.standard.set(language, forKey: "ssd-language")
            }
            if #available(macOS 26.0, *), let material = glass as? NSGlassEffectView {
                material.tintColor = theme == "miku" ? NSColor(calibratedRed: 0.22, green: 0.77, blue: 0.73, alpha: 0.15) : nil
            }
            for key in ["about", "quit", "edit", "undo", "cut", "copy", "paste", "all", "close", "choose", "export", "aboutDescription", "affiliation"] {
                if let value = body[key], value.count < 120 { labels[key] = value }
            }
            setupMenu()
            return
        }
        if message.name == "chooseFolder" {
            let panel = NSOpenPanel()
            panel.title = label("choose", "Choose test folder")
            panel.preventsApplicationTerminationWhenModal = false
            panel.canChooseDirectories = true
            panel.canChooseFiles = false
            panel.allowsMultipleSelection = false
            panel.beginSheetModal(for: window) { [weak self] response in
                if response == .OK, let url = panel.url,
                   let data = try? JSONSerialization.data(withJSONObject: [url.path]),
                   let json = String(data: data, encoding: .utf8) {
                    self?.webView.evaluateJavaScript("window.ssdFolderChosen.apply(null, \(json));", completionHandler: nil)
                }
            }
            return
        }
        guard let body = message.body as? [String: String], let filename = body["filename"],
              let encoded = body["data"], encoded.count < 64 * 1024 * 1024, let data = Data(base64Encoded: encoded) else { return }
        let panel = NSSavePanel()
        panel.title = label("export", "Export report")
        panel.preventsApplicationTerminationWhenModal = false
        panel.nameFieldStringValue = URL(fileURLWithPath: filename).lastPathComponent
        panel.canCreateDirectories = true
        panel.beginSheetModal(for: window) { [weak self] response in
            if response == .OK, let url = panel.url {
                do { try data.write(to: url, options: .atomic) }
                catch { self?.fail("Unable to save: \(error.localizedDescription)") }
            }
        }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { NSApp.terminate(nil); return false }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        quitting = true
        guard let process = backend, process.isRunning else { return .terminateNow }
        process.terminate()
        DispatchQueue.global().async {
            let deadline = Date().addingTimeInterval(10)
            while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
            DispatchQueue.main.async { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }
}

let application = NSApplication.shared
let delegate = LabDelegate()
application.delegate = delegate
application.run()
