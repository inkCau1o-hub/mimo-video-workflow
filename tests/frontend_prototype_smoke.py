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
    "src/api/jobMonitor.js",
    "src/api/workflowController.js",
    "src/useEditableValue.js",
    "src/components/EditableText.jsx",
    "src/components/JobProgress.jsx",
    "src/components/OpenProjectModal.jsx",
    "src/components/VideoPreview.jsx",
    "src/components/RenderedVideo.jsx",
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
    "src/editor/shotEditing.js",
    "src/editor/draftHistory.js",
    "src/editor/useShotDraft.js",
    "src/components/EditorButton.jsx",
    "src/components/EditorHistory.jsx",
    "src/components/ShotInspector.jsx",
    "src/components/ShotPreview.jsx",
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
    for script in ("dev", "build", "preview", "test"):
        assert script in scripts, f"missing npm script: {script}"
    for dependency in ("react", "react-dom", "vite", "@vitejs/plugin-react", "lucide-react"):
        assert dependency in dependencies, f"missing dependency: {dependency}"


def test_relative_import_graph_resolves():
    import_pattern = re.compile(r"(?:from\s+|import\s*)[\"'](\.[^\"']+)[\"']")
    missing = []
    checked = 0
    for source_file in list((APP_DIR / "src").rglob("*.js")) + list((APP_DIR / "src").rglob("*.jsx")):
        text = source_file.read_text(encoding="utf-8")
        for import_path in import_pattern.findall(text):
            checked += 1
            if resolve_import(source_file, import_path) is None:
                missing.append(f"{source_file.relative_to(APP_DIR)} -> {import_path}")
    assert not missing, "unresolved imports: " + ", ".join(missing)
    assert checked > 20, "import graph matcher did not inspect enough imports"


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
    assert "start_page" in app
    assert "voiceover" in app
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
    controller = (APP_DIR / "src/api/workflowController.js").read_text(encoding="utf-8")
    assert 'uploadVideos("reference", files)' in controller
    assert "/upload-videos" in controller


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


def test_editing_upload_render_and_external_project_contracts():
    controller = (APP_DIR / "src/api/workflowController.js").read_text(encoding="utf-8")
    for route in ("/reference/text", "/copy/text", "/storyboard/shots", "/projects/open"):
        assert route in controller, f"missing save/open contract: {route}"
    assert "activity_id: scope.activityId" in controller
    assert 'body.append("activity_id", scope.activityId)' in controller
    assert 'uploadVideos("materials", files)' in controller
    assert "recoverJobs" in controller
    assert "this.current(scope)" in controller
    assert "localStorage" in (APP_DIR / "src/App.jsx").read_text(encoding="utf-8")
    assert "/assembly/render" in (APP_DIR / "src/App.jsx").read_text(encoding="utf-8")
    storyboard = (APP_DIR / "src/pages/StoryboardPage.jsx").read_text(encoding="utf-8")
    storyboard += (APP_DIR / "src/components/ShotInspector.jsx").read_text(encoding="utf-8")
    for field in ("script_text", "tts_text", "duration_seconds", "material_asset_id", "onSaveShots"):
        assert field in storyboard
    media = (APP_DIR / "src/components/VideoPreview.jsx").read_text(encoding="utf-8")
    assert "projectMediaUrl" in media and "&download=1" in media and "download=" in media
    assert "createObjectURL" not in media
    monitor = (APP_DIR / "src/api/jobMonitor.js").read_text(encoding="utf-8")
    assert "while (!signal.aborted)" in monitor
    assert "< 120" not in monitor
    styles = (APP_DIR / "src/styles.css").read_text(encoding="utf-8")
    assert "max-width: 760px" in styles
    assert "min-width: 1080px" not in styles


def test_stage_one_editor_contracts():
    app = (APP_DIR / "src/App.jsx").read_text(encoding="utf-8")
    fieldset = app.split('<fieldset className="workspace-fields"', 1)[1].split("</fieldset>", 1)[0]
    assert "pending-status" not in fieldset, "Keep pending messages outside the fieldset to preserve container layout"
    helper = (APP_DIR / "src/editor/shotEditing.js").read_text(encoding="utf-8")
    for field in ("source_in", "source_out", "crop_x", "crop_y", "duration_mode", "shortage",
                  "duplicate_of", "font_size", "background", "randomUUID"):
        assert field in helper, f"missing editor contract: {field}"
    controller = (APP_DIR / "src/api/workflowController.js").read_text(encoding="utf-8")
    for field in ("/editor/history", "base_revision", "revision_id", "historyVersion", "matchesEditorScope", "edit_history"):
        assert field in controller, f"missing scoped history contract: {field}"
    preview = (APP_DIR / "src/components/ShotPreview.jsx").read_text(encoding="utf-8")
    for control in ("enforcePreviewRange", "objectFit", "objectPosition", "onLoadedMetadata", "requestAnimationFrame"):
        assert control in preview, f"missing preview behavior: {control}"
    page = (APP_DIR / "src/pages/StoryboardPage.jsx").read_text(encoding="utf-8")
    assert '<details className="editor-debug">' in page
    assert "useShotDraft" in page and "draft.conflict" in page
    for path in ("editor.test.mjs", "editor-controller.test.mjs", "editor-interactions.test.mjs"):
        assert (APP_DIR / "tests" / path).exists(), f"missing editor behavior tests: {path}"


def main():
    test_required_files_exist()
    test_package_scripts_and_dependencies()
    test_relative_import_graph_resolves()
    test_app_contains_required_product_regions()
    test_reference_page_contains_full_entry_controls()
    test_copywriting_page_has_rewrite_and_titles_without_cleaning()
    test_product_profile_creation_and_editing_surfaces_exist()
    test_voiceover_audio_uses_backend_media_endpoint()
    test_editing_upload_render_and_external_project_contracts()
    test_stage_one_editor_contracts()
    print("frontend_prototype_smoke: ok")


if __name__ == "__main__":
    main()
