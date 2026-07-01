import json
import os
import sys
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui import VideoScriptGUI


REFERENCE_VIDEO = os.getenv(
    "FULL_GUI_REFERENCE_VIDEO",
    "/Users/mac/Desktop/ai/@杜亚官方旗舰店 这才是品质生活该装的窗帘！Real6Pro 720P.mp4",
)
MATERIAL_VIDEO = os.getenv(
    "FULL_GUI_MATERIAL_VIDEO",
    "/Users/mac/Desktop/ai/微信视频2026-07-01_003409_809.mp4",
)


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def pump(root, seconds=0.2):
    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        time.sleep(0.02)


def wait_for(root, predicate, timeout, label):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        root.update()
        try:
            if predicate():
                return
        except Exception as exc:
            last_error = exc
        time.sleep(0.05)
    if last_error:
        raise TimeoutError(f"{label} timed out; last error: {last_error}")
    raise TimeoutError(f"{label} timed out")


def all_children(widget):
    for child in widget.winfo_children():
        yield child
        yield from all_children(child)


def find_button(root, text):
    matches = []
    for child in all_children(root):
        try:
            if child.winfo_class() == "TButton" and child.cget("text") == text:
                matches.append(child)
        except tk.TclError:
            continue
    if not matches:
        raise AssertionError(f"button not found: {text}")
    return matches[0]


def invoke_button(root, text):
    button = find_button(root, text)
    button.invoke()
    pump(root)
    return button


def set_text(text_widget, value):
    text_widget.delete("1.0", tk.END)
    text_widget.insert(tk.END, value)


def add_video_through_file_dialog(root, video_path, button_text):
    with patch("gui.filedialog.askopenfilename", return_value=(video_path,)):
        invoke_button(root, button_text)


def add_video_through_drop(app, root, video_path, target):
    event = type("DropEvent", (), {})()
    event.data = root.tk.call("list", video_path)
    app.handle_drop(event, target=target)
    pump(root)


def run():
    for path in (REFERENCE_VIDEO, MATERIAL_VIDEO):
        assert_true(Path(path).exists(), f"missing test video: {path}")

    output_dir = ROOT / "output" / f"full_gui_workflow_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "output_dir": str(output_dir),
        "reference_video": REFERENCE_VIDEO,
        "material_video": MATERIAL_VIDEO,
        "steps": [],
        "warnings": [],
    }

    root = tk.Tk()
    app = VideoScriptGUI(root)

    warnings = []
    errors = []

    def record(step, **data):
        payload = {"step": step, **data}
        report["steps"].append(payload)
        print(json.dumps(payload, ensure_ascii=False))

    def fake_showwarning(title, message):
        warnings.append({"title": title, "message": message})
        print(json.dumps({"warning": title, "message": message}, ensure_ascii=False))

    def fake_showerror(title, message):
        errors.append({"title": title, "message": message})
        print(json.dumps({"error": title, "message": message}, ensure_ascii=False))

    try:
        with (
            patch("gui.messagebox.showwarning", side_effect=fake_showwarning),
            patch("gui.messagebox.showerror", side_effect=fake_showerror),
            patch("gui.messagebox.askyesno", return_value=True),
        ):
            app.output_dir_var.set(str(output_dir))
            app.model_var.set("tiny")
            app.storyboard_count_var.set("1")
            app.storyboard_duration_var.set("8")
            app.product_entry.delete(0, tk.END)
            app.product_entry.insert(0, "杜亚智能窗帘")
            app.points_entry.delete(0, tk.END)
            app.points_entry.insert(0, "智能、安静、安装方便")
            set_text(app.requirements_text, "口语化，短视频口播，突出智能窗帘体验，不夸大效果")
            record("setup", model=app.model_var.get(), output_dir=str(output_dir))

            invoke_button(root, "显示设置")
            assert_true(app.settings_visible, "settings panel did not open")
            invoke_button(root, "隐藏设置")
            assert_true(not app.settings_visible, "settings panel did not hide")
            record("settings_toggle", ok=True)

            add_video_through_file_dialog(root, REFERENCE_VIDEO, "添加参考视频...")
            assert_true(
                app.reference_video_queue == [REFERENCE_VIDEO],
                f"reference video not added: {app.reference_video_queue}",
            )
            assert_true(not app.material_video_queue, "reference add polluted material queue")
            record("add_reference_video", reference_video_count=len(app.reference_video_queue))

            invoke_button(root, "转写参考")
            wait_for(
                root,
                lambda: (not app.is_running) and bool(app.workflow.reference_text.strip()),
                timeout=90,
                label="reference transcription",
            )
            reference_text = app.workflow.reference_text
            assert_true(len(reference_text) > 20, "reference transcription produced too little text")
            record("reference_transcription", chars=len(reference_text), status=app.status_var.get())

            invoke_button(root, "分析分镜")
            wait_for(
                root,
                lambda: app.workflow.storyboard_analysis is not None
                and len(app.workflow.storyboard_analysis.shots) > 0,
                timeout=120,
                label="storyboard analysis",
            )
            shot_count = len(app.workflow.storyboard_analysis.shots)
            record("storyboard_analysis", shots=shot_count)

            invoke_button(root, "清空素材")
            assert_true(not app.material_video_queue, "material queue did not clear before material tagging")
            add_video_through_drop(app, root, MATERIAL_VIDEO, target="material")
            assert_true(
                app.material_video_queue == [MATERIAL_VIDEO],
                f"drop did not add material video: {app.material_video_queue}",
            )
            assert_true(app.reference_video_queue == [REFERENCE_VIDEO], "material drop polluted reference queue")
            record(
                "drop_material_video",
                reference_video_count=len(app.reference_video_queue),
                material_video_count=len(app.material_video_queue),
            )

            invoke_button(root, "打标素材列表")
            wait_for(
                root,
                lambda: len(app.workflow.material_assets) > 0,
                timeout=150,
                label="material tagging",
            )
            first_asset = app.workflow.material_assets[0]
            record(
                "material_tagging",
                assets=len(app.workflow.material_assets),
                material_type=getattr(first_asset, "material_type", ""),
                tags=getattr(first_asset, "visual_tags", [])[:6],
            )

            invoke_button(root, "按分镜生成配音")
            wait_for(
                root,
                lambda: len(app.workflow.voiceover_segments) >= min(shot_count, 1)
                and all(segment.audio_path for segment in app.workflow.voiceover_segments),
                timeout=180,
                label="voiceover generation",
            )
            voiceover_paths = [segment.audio_path for segment in app.workflow.voiceover_segments]
            assert_true(all(Path(path).exists() for path in voiceover_paths), "missing generated voiceover file")
            record("voiceover_generation", segments=len(voiceover_paths), files=voiceover_paths)

            invoke_button(root, "生成合成计划")
            wait_for(
                root,
                lambda: app.workflow.assembly_plan is not None,
                timeout=60,
                label="assembly plan",
            )
            assembly_preview = app.assembly_command_preview()
            assert_true(bool(assembly_preview), "assembly command preview is empty")
            record(
                "assembly_plan",
                items=len(getattr(app.workflow.assembly_plan, "items", []) or []),
                has_command=bool(assembly_preview),
            )

            project_path = output_dir / "workflow_project.json"
            with patch("gui.filedialog.asksaveasfilename", return_value=str(project_path)):
                invoke_button(root, "保存项目")
            assert_true(project_path.exists(), "project file was not saved")
            set_text(app.reference_text, "")
            app.workflow.set_reference_text("")
            with patch("gui.filedialog.askopenfilename", return_value=str(project_path)):
                invoke_button(root, "加载项目")
            assert_true(len(app.workflow.reference_text) > 20, "project load did not restore reference text")
            record("save_load_project", project_path=str(project_path))

            app.agent_entry.insert(0, "状态")
            invoke_button(root, "执行")
            assert_true("参考视频数量" in app.get_text(app.agent_output), "agent status did not render reference count")
            assert_true("素材视频数量" in app.get_text(app.agent_output), "agent status did not render material count")
            app.agent_entry.insert(0, "记住风格 全流程测试: 口语化、短句、明确利益点")
            invoke_button(root, "执行")
            app.agent_entry.insert(0, "查看记忆")
            invoke_button(root, "执行")
            assert_true("全流程测试" in app.get_text(app.agent_output), "agent memory command failed")
            record("agent_console", status=True, memory=True)

            invoke_button(root, "清空参考")
            add_video_through_file_dialog(root, REFERENCE_VIDEO, "添加参考视频...")
            app.enable_titles_var.set(True)
            app.model_var.set("tiny")
            invoke_button(root, "旧流程生成文案")
            wait_for(
                root,
                lambda: (not app.is_running)
                and len(app.get_text(app.rewrite_output)) > 20
                and len(app.get_text(app.title_output)) > 10,
                timeout=240,
                label="legacy generation flow",
            )
            record(
                "legacy_generation",
                transcript_chars=len(app.get_text(app.transcript_output)),
                rewrite_chars=len(app.get_text(app.rewrite_output)),
                title_chars=len(app.get_text(app.title_output)),
                status=app.status_var.get(),
            )

        report["warnings"] = warnings
        report["errors"] = errors
        assert_true(not errors, f"GUI errors occurred: {errors}")
        report["ok"] = True
        report_path = output_dir / "full_gui_workflow_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"report_path": str(report_path), "ok": True}, ensure_ascii=False))
        return report
    finally:
        root.destroy()


if __name__ == "__main__":
    run()
