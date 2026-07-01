import os
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui import FlowButtonBar, VideoScriptGUI


TEST_VIDEO = "/Users/mac/Desktop/ai/微信视频2026-07-01_003409_809.mp4"


def build_app():
    root = tk.Tk()
    root.withdraw()
    app = VideoScriptGUI(root)
    return root, app


def test_reference_button_dispatches_with_visible_feedback():
    if not os.path.exists(TEST_VIDEO):
        raise AssertionError(f"missing test video: {TEST_VIDEO}")

    root, app = build_app()
    calls = []

    def fake_thread(*args, **kwargs):
        calls.append((args, kwargs))

        class FakeThread:
            def start(self):
                calls.append("start")

        return FakeThread()

    try:
        app.add_video_path(TEST_VIDEO)
        with patch("gui.threading.Thread", side_effect=fake_thread):
            app.reference_transcribe_button.invoke()
            root.update_idletasks()

        assert calls and calls[-1] == "start", "reference button did not start worker thread"
        assert app.is_running is True, "app did not enter running state"
        assert "准备转写" in app.status_var.get(), app.status_var.get()
        assert app.reference_transcribe_button.instate(["disabled"])
        assert app.stop_button.instate(["!disabled"])
        assert "转写参考任务已启动" in app.get_text(app.log_text)
    finally:
        root.destroy()


def test_reference_worker_writes_backend_result_to_project_state():
    root, app = build_app()

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            app.is_running = True
            task = {
                "videos": [TEST_VIDEO],
                "output_dir": tmpdir,
                "model_size": "base",
            }
            with patch.object(app, "transcribe_reference_video", return_value="这是一次 GUI 转写 smoke 测试文本"):
                app.reference_transcription_worker(task)
                root.update_idletasks()

        reference_text = app.get_text(app.reference_text)
        assert "GUI 转写 smoke 测试文本" in reference_text
        assert "GUI 转写 smoke 测试文本" in app.workflow.reference_text
        assert app.is_running is False
        assert app.status_var.get() == "就绪"
    finally:
        root.destroy()


def test_flow_button_bar_wraps_without_shrinking_buttons():
    root = tk.Tk()
    root.withdraw()
    bar = FlowButtonBar(root)
    bar.pack(fill=tk.X)
    for label in ["转写参考", "使用当前文本", "导入文本", "旧流程生成文案", "停止"]:
        bar.add_button(label, lambda: None, width=14)

    try:
        root.update_idletasks()
        bar._last_width = 220
        bar._reflow()
        rows = {button.grid_info().get("row") for button in bar.buttons}
        widths = {button.cget("width") for button in bar.buttons}
        assert len(rows) > 1, "buttons did not wrap in a narrow container"
        assert widths == {14}, widths
    finally:
        root.destroy()


def main():
    test_reference_button_dispatches_with_visible_feedback()
    test_reference_worker_writes_backend_result_to_project_state()
    test_flow_button_bar_wraps_without_shrinking_buttons()
    print("smoke_gui_reference: ok")


if __name__ == "__main__":
    main()
