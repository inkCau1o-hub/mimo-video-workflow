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
    "src/components/CreateProjectModal.jsx",
    "src/components/JobLogPanel.jsx",
    "src/components/ProductProfileForm.jsx",
    "src/components/ProjectStatusPanel.jsx",
    "src/components/Sidebar.jsx",
    "src/components/TopBar.jsx",
    "src/pages/CopywritingPage.jsx",
    "src/pages/ReferencePage.jsx",
    "src/pages/StoryboardPage.jsx",
    "src/pages/MaterialLibraryPage.jsx",
    "src/pages/VoiceoverPage.jsx",
    "src/pages/AssemblyPage.jsx",
    "src/pages/ProductProfilePage.jsx",
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
        "CopywritingPage",
        "ProductProfilePage",
        "StoryboardPage",
        "MaterialLibraryPage",
        "VoiceoverPage",
        "AssemblyPage",
        "ResultsPage",
        "AgentConsole",
        "CreateProjectModal",
        "ProjectStatusPanel",
        "JobLogPanel",
    ):
        assert component in app, f"App.jsx does not wire {component}"
    assert "auto_open" in app
    assert "latest" in app


def test_reference_page_contains_full_entry_controls():
    reference_page = (APP_DIR / "src" / "pages" / "ReferencePage.jsx").read_text(encoding="utf-8")
    for text in (
        "拖拽视频文件到这里",
        "选择视频",
        "选择文件夹",
        "Whisper模型",
        "开始转写",
        "停止任务",
        "发送到文案处理",
        "下一步：文案处理",
        "已保存",
        "正在导入",
        "浏览器导入会先复制到当前项目",
        "路径有效",
        "路径缺失",
    ):
        assert text in reference_page, f"ReferencePage missing control text: {text}"
    assert "转写完成后会显示在这里" not in reference_page

    app = (APP_DIR / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "uploadReferenceVideos" in app
    assert "/reference/upload-videos" in app


def test_copywriting_page_has_rewrite_and_titles_without_cleaning():
    copywriting_page = (APP_DIR / "src" / "pages" / "CopywritingPage.jsx").read_text(encoding="utf-8")
    sidebar = (APP_DIR / "src" / "components" / "Sidebar.jsx").read_text(encoding="utf-8")
    app = (APP_DIR / "src" / "App.jsx").read_text(encoding="utf-8")
    for text in ("文案处理", "文案改写", "标题生成", "发送到分镜", "成稿与标题"):
        assert text in copywriting_page, f"CopywritingPage missing text: {text}"
    assert "文本" + "清洗" not in copywriting_page
    assert "clean" not in copywriting_page.lower()
    assert "文案处理" in sidebar
    assert "/copy/rewrite" in app
    assert "/copy/titles" in app


def test_product_profile_creation_and_editing_surfaces_exist():
    modal = (APP_DIR / "src" / "components" / "CreateProjectModal.jsx").read_text(encoding="utf-8")
    profile_form = (APP_DIR / "src" / "components" / "ProductProfileForm.jsx").read_text(encoding="utf-8")
    profile_page = (APP_DIR / "src" / "pages" / "ProductProfilePage.jsx").read_text(encoding="utf-8")
    sidebar = (APP_DIR / "src" / "components" / "Sidebar.jsx").read_text(encoding="utf-8")
    for text in (
        "产品名称",
        "产品特点",
        "核心卖点",
        "转写/改写要求",
        "必填",
    ):
        assert text in profile_form, f"ProductProfileForm missing field text: {text}"
    assert "创建项目" in modal
    assert "missingRequiredProfileFields" in modal
    assert "保存资料" in profile_page
    assert "项目资料" in sidebar


def test_voiceover_audio_uses_backend_media_endpoint():
    voiceover_page = (APP_DIR / "src" / "pages" / "VoiceoverPage.jsx").read_text(encoding="utf-8")
    app = (APP_DIR / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "/projects/" in voiceover_page
    assert "/media?path=" in voiceover_page
    assert "encodeURIComponent" in voiceover_page
    assert "重新生成" in voiceover_page
    assert "onRegenerateVoiceover" in voiceover_page
    assert "/voiceovers/regenerate" in app
    assert "file://" not in voiceover_page
    assert "backendUrl={backendUrl}" in app


def main():
    test_required_files_exist()
    test_package_scripts_and_dependencies()
    test_relative_import_graph_resolves()
    test_app_contains_required_product_regions()
    test_reference_page_contains_full_entry_controls()
    test_copywriting_page_has_rewrite_and_titles_without_cleaning()
    test_product_profile_creation_and_editing_surfaces_exist()
    test_voiceover_audio_uses_backend_media_endpoint()
    print("frontend_prototype_smoke: ok")


if __name__ == "__main__":
    main()
