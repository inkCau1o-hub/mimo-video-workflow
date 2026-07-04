import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "apps" / "desktop"


REQUIRED_FILES = [
    "package.json",
    "index.html",
    "vite.config.js",
    "src/main.jsx",
    "src/App.jsx",
    "src/api/client.js",
    "src/workflowData.js",
    "src/components/AgentConsole.jsx",
    "src/components/JobLogPanel.jsx",
    "src/components/ProjectStatusPanel.jsx",
    "src/components/Sidebar.jsx",
    "src/components/TopBar.jsx",
    "src/pages/ReferencePage.jsx",
    "src/pages/StoryboardPage.jsx",
    "src/pages/MaterialLibraryPage.jsx",
    "src/pages/VoiceoverPage.jsx",
    "src/pages/AssemblyPage.jsx",
    "src/pages/ResultsPage.jsx",
    "src/styles.css",
]


def resolve_import(source_file, import_path):
    base = source_file.parent / import_path
    candidates = [
        base,
        base.with_suffix(".js"),
        base.with_suffix(".jsx"),
        base / "index.js",
        base / "index.jsx",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def test_required_files_exist():
    missing = [path for path in REQUIRED_FILES if not (APP_DIR / path).exists()]
    assert not missing, f"missing frontend files: {missing}"


def test_package_scripts_and_dependencies():
    package = json.loads((APP_DIR / "package.json").read_text(encoding="utf-8"))
    scripts = package.get("scripts", {})
    dependencies = package.get("dependencies", {})
    for script in ("dev", "build", "preview"):
        assert script in scripts, f"missing npm script: {script}"
    for dependency in ("react", "react-dom", "vite", "@vitejs/plugin-react"):
        assert dependency in dependencies, f"missing dependency: {dependency}"


def test_relative_import_graph_resolves():
    import_pattern = re.compile(r"from\\s+[\"'](\\.[^\"']+)[\"']")
    missing = []
    for source_file in list((APP_DIR / "src").rglob("*.js")) + list((APP_DIR / "src").rglob("*.jsx")):
        text = source_file.read_text(encoding="utf-8")
        for import_path in import_pattern.findall(text):
            if resolve_import(source_file, import_path) is None:
                missing.append(f"{source_file.relative_to(APP_DIR)} -> {import_path}")
    assert not missing, "unresolved imports: " + ", ".join(missing)


def test_app_contains_required_product_regions():
    app = (APP_DIR / "src" / "App.jsx").read_text(encoding="utf-8")
    for component in (
        "ReferencePage",
        "StoryboardPage",
        "MaterialLibraryPage",
        "VoiceoverPage",
        "AssemblyPage",
        "ResultsPage",
        "AgentConsole",
        "ProjectStatusPanel",
        "JobLogPanel",
    ):
        assert component in app, f"App.jsx does not wire {component}"


def test_reference_page_contains_full_entry_controls():
    reference_page = (APP_DIR / "src" / "pages" / "ReferencePage.jsx").read_text(encoding="utf-8")
    for text in (
        "拖拽视频文件到这里",
        "选择视频",
        "选择文件夹",
        "Whisper模型",
        "开始转写",
        "停止任务",
        "发送到分镜",
        "下一步：生成分镜",
    ):
        assert text in reference_page, f"ReferencePage missing control text: {text}"


def main():
    test_required_files_exist()
    test_package_scripts_and_dependencies()
    test_relative_import_graph_resolves()
    test_app_contains_required_product_regions()
    test_reference_page_contains_full_entry_controls()
    print("frontend_prototype_smoke: ok")


if __name__ == "__main__":
    main()
