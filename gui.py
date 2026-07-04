import os
import json
import queue
import shlex
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:
    DND_FILES = None
    TkinterDnD = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import app_memory
from ai_client import OpenAICompatibleClient
from ai_rewriter import AIRewriter
import gui_pages
from media_assembler import FFmpegAssemblyRenderer
from multimodal_interfaces import MaterialAnalyzer
from workflow_state import WorkflowState
from title_generator import TitleGenerator


FONT_FAMILY = getattr(config, "UI_FONT_FAMILY", "SF Pro Text" if sys.platform == "darwin" else "Arial")
MONO_FONT_FAMILY = "SF Mono" if sys.platform == "darwin" else "Menlo"
VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm")

WINDOW_BG = "#f5f5f7"
PANEL_BG = "#ffffff"
PANEL_ALT_BG = "#fbfbfd"
TEXT_BG = "#ffffff"
TEXT_FG = "#1d1d1f"
MUTED_FG = "#6e6e73"
ACCENT = "#007aff"
BORDER = "#d2d2d7"
DROP_BG = "#f2f7ff"
DROP_BORDER = "#a9c7ff"
BUTTON_GAP = 8
BUTTON_ROW_GAP = 8


class FlowButtonBar(ttk.Frame):
    def __init__(self, parent, gap=BUTTON_GAP, row_gap=BUTTON_ROW_GAP):
        super().__init__(parent)
        self.gap = gap
        self.row_gap = row_gap
        self.buttons = []
        self._pending_reflow = False
        self._last_width = 0
        self.bind("<Configure>", self._schedule_reflow)

    def add_button(self, text, command, style="Secondary.TButton", width=12, **kwargs):
        button = ttk.Button(self, text=text, command=command, style=style, width=width, **kwargs)
        self.buttons.append(button)
        button.grid(row=0, column=len(self.buttons) - 1, sticky=tk.W, padx=(0, self.gap), pady=(0, 0))
        self._schedule_reflow()
        return button

    def _schedule_reflow(self, event=None):
        if event is not None and getattr(event, "width", 0):
            self._last_width = event.width
        if self._pending_reflow:
            return
        self._pending_reflow = True
        self.after_idle(self._reflow)

    def _reflow(self):
        self._pending_reflow = False
        if not self.buttons:
            return
        available_width = self._last_width or self.winfo_width()
        if available_width <= 1 and self.master is not None:
            available_width = self.master.winfo_width()
        available_width = max(available_width, 1)
        row = 0
        column = 0
        used_width = 0

        for button in self.buttons:
            button_width = max(button.winfo_reqwidth(), 96)
            next_width = button_width if column == 0 else used_width + self.gap + button_width
            if column > 0 and next_width > available_width:
                row += 1
                column = 0
                used_width = 0

            button.grid_configure(
                row=row,
                column=column,
                padx=(0, self.gap),
                pady=(0, self.row_gap if row < len(self.buttons) - 1 else 0),
            )
            used_width = button_width if column == 0 else used_width + self.gap + button_width
            column += 1


class VideoScriptGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("MiMo 短视频工作流")
        self.root.geometry("1480x940")
        self.root.minsize(1180, 760)
        self.root.resizable(True, True)
        self.root.configure(bg=WINDOW_BG)
        self.main_thread = threading.current_thread()

        self.reference_video_queue = []
        self.material_video_queue = []
        self.video_queue = self.reference_video_queue
        self.is_running = False
        self.output_dir = config.OUTPUT_DIR
        self.vision_model = config.VISION_API_MODEL
        self.pending_agent_actions = None
        self.workflow = WorkflowState(output_dir=self.output_dir)
        self.settings_visible = False
        self.ui_queue = queue.Queue()
        self.drop_targets = []

        self.setup_styles()
        self.create_widgets()
        self.load_config()
        self.setup_drag_and_drop()
        self.root.after(50, self.process_ui_queue)

    def setup_styles(self):
        style = ttk.Style()
        theme_names = set(style.theme_names())
        if "clam" in theme_names:
            style.theme_use("clam")

        style.configure(".", font=(FONT_FAMILY, 10))
        style.configure("TFrame", background=WINDOW_BG)
        style.configure("Panel.TFrame", background=PANEL_BG)
        style.configure("TLabel", background=WINDOW_BG, foreground=TEXT_FG)
        style.configure("Title.TLabel", font=(FONT_FAMILY, 20, "bold"), foreground=TEXT_FG, background=WINDOW_BG)
        style.configure("Info.TLabel", font=(FONT_FAMILY, 10), foreground=MUTED_FG, background=WINDOW_BG)
        style.configure("Field.TLabel", font=(FONT_FAMILY, 10), foreground=MUTED_FG, background=WINDOW_BG)
        style.configure("TLabelframe", background=WINDOW_BG, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, relief="solid")
        style.configure("TLabelframe.Label", background=WINDOW_BG, foreground=MUTED_FG, font=(FONT_FAMILY, 10, "bold"))
        style.configure("TNotebook", background=WINDOW_BG, borderwidth=0)
        style.configure("TNotebook.Tab", background="#ececf1", foreground=TEXT_FG, font=(FONT_FAMILY, 10), padding=(16, 9))
        style.map(
            "TNotebook.Tab",
            background=[("selected", PANEL_BG), ("active", "#f2f2f7")],
            foreground=[("selected", TEXT_FG), ("active", TEXT_FG)],
        )
        style.configure("TEntry", padding=(8, 5), foreground=TEXT_FG, fieldbackground=PANEL_BG)
        style.configure("TCheckbutton", background=WINDOW_BG, foreground=TEXT_FG)
        style.configure("TRadiobutton", background=WINDOW_BG, foreground=TEXT_FG)
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#e5e5ea", borderwidth=0)
        style.configure("Primary.TButton", background="#e8f2ff", foreground=ACCENT, font=(FONT_FAMILY, 10, "bold"), padding=(14, 8), borderwidth=1)
        style.configure("Secondary.TButton", background="#f2f2f7", foreground=TEXT_FG, font=(FONT_FAMILY, 10), padding=(12, 7), borderwidth=1)
        style.map(
            "Primary.TButton",
            background=[("active", "#d9ebff"), ("disabled", "#f2f2f7")],
            foreground=[("disabled", MUTED_FG), ("!disabled", ACCENT)],
        )
        style.map(
            "Secondary.TButton",
            background=[("active", "#e5e5ea"), ("disabled", "#f2f2f7")],
            foreground=[("disabled", MUTED_FG), ("!disabled", TEXT_FG)],
        )

    def make_button(self, parent, text, command, style="Secondary.TButton", width=12, **kwargs):
        return ttk.Button(parent, text=text, command=command, style=style, width=width, **kwargs)

    def make_button_bar(self, parent):
        return FlowButtonBar(parent)

    def create_widgets(self):
        self.main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self.main_paned.pack(fill=tk.BOTH, expand=True)

        main_frame = ttk.Frame(self.main_paned, padding="18")
        agent_frame = ttk.Frame(self.main_paned, padding=(0, 18, 18, 18), width=340)
        self.main_paned.add(main_frame, weight=5)
        self.main_paned.add(agent_frame, weight=2)

        header = ttk.Frame(main_frame)
        header.pack(fill=tk.X, pady=(0, 14))
        ttk.Label(header, text="短视频工作流", style="Title.TLabel").pack(side=tk.LEFT)
        self.model_status_label = ttk.Label(header, text="", style="Info.TLabel")
        self.model_status_label.pack(side=tk.RIGHT, padx=(12, 0))
        self.settings_button = self.make_button(header, text="显示设置", style="Secondary.TButton", width=10, command=self.toggle_common_settings)
        self.settings_button.pack(side=tk.RIGHT)

        self.create_common_settings(main_frame)
        self.common_settings_frame.pack_forget()
        self.create_workflow_tabs(main_frame)
        self.create_agent_panel(agent_frame)
        self.root.after(100, self.set_initial_pane_sizes)

    def create_workflow_tabs(self, parent):
        self.workflow_notebook = ttk.Notebook(parent)
        self.workflow_notebook.pack(fill=tk.BOTH, expand=True)

        reference_tab = ttk.Frame(self.workflow_notebook, padding="14")
        storyboard_tab = ttk.Frame(self.workflow_notebook, padding="14")
        material_tab = ttk.Frame(self.workflow_notebook, padding="14")
        voiceover_tab = ttk.Frame(self.workflow_notebook, padding="14")
        assembly_tab = ttk.Frame(self.workflow_notebook, padding="14")
        results_tab = ttk.Frame(self.workflow_notebook, padding="14")

        self.workflow_notebook.add(reference_tab, text="参考转写")
        self.workflow_notebook.add(storyboard_tab, text="分镜分析")
        self.workflow_notebook.add(material_tab, text="素材库")
        self.workflow_notebook.add(voiceover_tab, text="配音")
        self.workflow_notebook.add(assembly_tab, text="合成")
        self.workflow_notebook.add(results_tab, text="日志结果")

        self.create_reference_page(reference_tab)
        self.create_storyboard_page(storyboard_tab)
        self.create_material_page(material_tab)
        self.create_voiceover_page(voiceover_tab)
        self.create_assembly_page(assembly_tab)
        self.create_result_area(results_tab)

    def create_reference_page(self, parent):
        parent.columnconfigure(0, weight=2)
        parent.columnconfigure(1, weight=3)
        parent.rowconfigure(0, weight=1)

        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)
        right.rowconfigure(7, weight=1)

        drop_text = "拖拽参考视频到这里"
        if not DND_FILES:
            drop_text = "拖拽依赖未安装，可使用添加按钮"
        self.create_drop_zone(left, drop_text, height=110, target="reference")

        self.reference_video_listbox = tk.Listbox(left, height=10, font=(FONT_FAMILY, 10))
        self.video_listbox = self.reference_video_listbox
        self.style_listbox(self.reference_video_listbox)
        self.reference_video_listbox.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.drop_targets.append((self.reference_video_listbox, "reference"))

        video_buttons = self.make_button_bar(left)
        video_buttons.pack(fill=tk.X)
        video_buttons.add_button("添加参考视频...", self.add_reference_videos, width=14)
        video_buttons.add_button("添加参考文件夹...", self.add_reference_folder, width=16)
        video_buttons.add_button("删除参考", self.remove_selected_reference_video, width=10)
        video_buttons.add_button("清空参考", self.clear_reference_videos, width=10)

        ttk.Label(right, text="产品名称:", style="Field.TLabel").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.product_entry = ttk.Entry(right, width=34, font=(FONT_FAMILY, 10))
        self.product_entry.grid(row=0, column=1, sticky="ew", padx=10, pady=5)

        ttk.Label(right, text="产品卖点:", style="Field.TLabel").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.points_entry = ttk.Entry(right, width=48, font=(FONT_FAMILY, 10))
        self.points_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=5)
        self.points_entry.insert(0, "保湿、抗衰老、美白")

        ttk.Label(right, text="改写要求:", style="Field.TLabel").grid(row=2, column=0, sticky=tk.NW, pady=5)
        self.requirements_text = scrolledtext.ScrolledText(right, height=4, wrap=tk.WORD, font=(FONT_FAMILY, 10))
        self.style_text_widget(self.requirements_text)
        self.requirements_text.grid(row=2, column=1, sticky="ew", padx=10, pady=5)
        self.requirements_text.insert(tk.END, "保留核心信息；更口语化；适合短视频口播；控制在300字以内")

        ttk.Label(right, text="Whisper模型:", style="Field.TLabel").grid(row=3, column=0, sticky=tk.W, pady=5)
        model_frame = ttk.Frame(right)
        model_frame.grid(row=3, column=1, sticky=tk.W, padx=10, pady=5)
        self.model_var = tk.StringVar(value="medium")
        for name in ["tiny", "base", "small", "medium", "large-v3"]:
            ttk.Radiobutton(model_frame, text=name, variable=self.model_var, value=name).pack(side=tk.LEFT, padx=(0, 10))

        options = ttk.Frame(right)
        options.grid(row=4, column=1, sticky=tk.W, padx=10, pady=(8, 5))
        self.enable_titles_var = tk.BooleanVar(value=True)
        self.save_transcript_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text="生成标题", variable=self.enable_titles_var).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(options, text="保存原文", variable=self.save_transcript_var).pack(side=tk.LEFT, padx=10)

        action_area = ttk.Frame(right)
        action_area.grid(row=5, column=1, sticky=tk.EW, padx=10, pady=(8, 8))
        action_area.columnconfigure(0, weight=1)
        action_row = self.make_button_bar(action_area)
        action_row.grid(row=0, column=0, sticky=tk.EW)
        self.reference_transcribe_button = action_row.add_button(
            "转写参考",
            self.start_reference_transcription,
            style="Primary.TButton",
            width=12,
        )
        action_row.add_button("使用当前文本", self.sync_reference_text, width=13)
        action_row.add_button("导入文本", self.import_reference_text, width=10)
        self.start_button = action_row.add_button("旧流程生成文案", self.start_generation, width=15)
        self.stop_button = action_row.add_button("停止", self.stop_generation, width=8, state=tk.DISABLED)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(action_area, textvariable=self.status_var, style="Info.TLabel").grid(row=1, column=0, sticky=tk.W, pady=(4, 0))

        project_row = self.make_button_bar(right)
        project_row.grid(row=6, column=1, sticky="ew", padx=10, pady=(0, 8))
        project_row.add_button("保存项目", self.save_project_dialog, width=10)
        project_row.add_button("加载项目", self.load_project_dialog, width=10)

        ttk.Label(right, text="参考文案:", style="Field.TLabel").grid(row=7, column=0, sticky=tk.NW, pady=5)
        self.reference_text = scrolledtext.ScrolledText(right, height=11, wrap=tk.WORD, font=(FONT_FAMILY, 10))
        self.style_text_widget(self.reference_text)
        self.reference_text.grid(row=7, column=1, sticky="nsew", padx=10, pady=5)

    def create_storyboard_page(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        controls = ttk.Frame(parent)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(controls, text="目标时长(s):", style="Field.TLabel").pack(side=tk.LEFT)
        self.storyboard_duration_var = tk.StringVar(value="")
        ttk.Entry(controls, textvariable=self.storyboard_duration_var, width=8).pack(side=tk.LEFT, padx=(6, 14))
        ttk.Label(controls, text="分镜数:", style="Field.TLabel").pack(side=tk.LEFT)
        self.storyboard_count_var = tk.StringVar(value="")
        ttk.Entry(controls, textvariable=self.storyboard_count_var, width=8).pack(side=tk.LEFT, padx=(6, 14))
        self.make_button(controls, text="分析分镜", style="Primary.TButton", width=12, command=self.start_storyboard_analysis).pack(side=tk.LEFT)

        split = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        split.grid(row=1, column=0, sticky="nsew")
        summary_frame = ttk.LabelFrame(split, text="分镜列表", padding="10")
        json_frame = ttk.LabelFrame(split, text="分镜 JSON", padding="10")
        split.add(summary_frame, weight=1)
        split.add(json_frame, weight=1)
        self.storyboard_summary_output = self.make_text_area(summary_frame, height=18)
        self.storyboard_json_output = self.make_text_area(json_frame, height=18)

    def create_material_page(self, parent):
        parent.columnconfigure(0, weight=2)
        parent.columnconfigure(1, weight=3)
        parent.rowconfigure(0, weight=1)

        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        drop_text = "拖拽素材视频到这里"
        if not DND_FILES:
            drop_text = "拖拽依赖未安装，可使用添加按钮"
        self.create_drop_zone(left, drop_text, height=110, target="material")

        self.material_video_listbox = tk.Listbox(left, height=10, font=(FONT_FAMILY, 10))
        self.style_listbox(self.material_video_listbox)
        self.material_video_listbox.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.drop_targets.append((self.material_video_listbox, "material"))

        video_buttons = self.make_button_bar(left)
        video_buttons.pack(fill=tk.X)
        video_buttons.add_button("添加素材视频...", self.add_material_videos, width=14)
        video_buttons.add_button("添加素材文件夹...", self.add_material_folder, width=16)
        video_buttons.add_button("删除素材", self.remove_selected_material_video, width=10)
        video_buttons.add_button("清空素材", self.clear_material_videos, width=10)

        controls = self.make_button_bar(right)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        controls.add_button("打标素材列表", self.confirm_material_tagging, style="Primary.TButton", width=14)
        controls.add_button("刷新摘要", self.refresh_material_output, width=10)
        output_frame = ttk.Frame(right)
        output_frame.grid(row=1, column=0, sticky="nsew")
        self.material_output = self.make_text_area(output_frame, height=22)

    def create_voiceover_page(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        controls = self.make_button_bar(parent)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        controls.add_button("按分镜生成配音", self.confirm_voiceover_generation, style="Primary.TButton", width=16)
        controls.add_button("刷新摘要", self.refresh_voiceover_output, width=10)
        output_frame = ttk.Frame(parent)
        output_frame.grid(row=1, column=0, sticky="nsew")
        self.voiceover_output = self.make_text_area(output_frame, height=22)

    def create_assembly_page(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        controls = self.make_button_bar(parent)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        controls.add_button("生成合成计划", self.confirm_assembly_plan, style="Primary.TButton", width=14)
        controls.add_button("刷新摘要", self.refresh_assembly_output, width=10)
        output_frame = ttk.Frame(parent)
        output_frame.grid(row=1, column=0, sticky="nsew")
        self.assembly_output = self.make_text_area(output_frame, height=22)

    def create_agent_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="AI 控制台", padding="12")
        frame.pack(fill=tk.BOTH, expand=True)

        self.agent_output = scrolledtext.ScrolledText(frame, height=28, wrap=tk.WORD, font=(MONO_FONT_FAMILY, 10))
        self.style_text_widget(self.agent_output)
        self.agent_output.configure(bg=PANEL_ALT_BG)
        self.agent_output.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        input_row = ttk.Frame(frame)
        input_row.pack(fill=tk.X)
        self.agent_entry = ttk.Entry(input_row, font=(FONT_FAMILY, 10))
        self.agent_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.agent_entry.bind("<Return>", lambda _event: self.handle_agent_command())
        self.make_button(input_row, text="执行", style="Secondary.TButton", width=8, command=self.handle_agent_command).pack(side=tk.LEFT, padx=(8, 0))

        quick_row = self.make_button_bar(frame)
        quick_row.pack(fill=tk.X, pady=(10, 0))
        quick_row.add_button("帮助", lambda: self.execute_agent_command("帮助"), width=8)
        quick_row.add_button("状态", lambda: self.execute_agent_command("状态"), width=8)
        quick_row.add_button("开始", lambda: self.execute_agent_command("开始生成"), width=8)
        quick_row.add_button("确认", lambda: self.execute_agent_command("确认"), width=8)

        self.log_agent("控制台已就绪。输入“帮助”查看可用命令。")

    def create_common_settings(self, parent):
        frame = ttk.LabelFrame(parent, text="通用设置", padding="12")
        self.common_settings_frame = frame
        frame.pack(fill=tk.X, pady=(0, 12))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="输出文件夹:", style="Field.TLabel").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.output_dir_var = tk.StringVar(value=self.output_dir)
        ttk.Entry(frame, textvariable=self.output_dir_var, width=58, font=(FONT_FAMILY, 10)).grid(row=0, column=1, sticky="ew", padx=10, pady=5)
        self.make_button(frame, text="浏览...", width=10, command=self.browse_output_dir).grid(row=0, column=2, sticky=tk.W, pady=5)

        ttk.Label(frame, text="AI API Key:", style="Field.TLabel").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.api_key_entry = ttk.Entry(frame, width=58, font=(FONT_FAMILY, 10), show="*")
        self.api_key_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=5)
        self.make_button(frame, text="保存配置", width=10, command=self.save_config).grid(row=1, column=2, sticky=tk.W, pady=5)

        ttk.Label(frame, text="AI Base URL:", style="Field.TLabel").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.api_base_url_entry = ttk.Entry(frame, width=58, font=(FONT_FAMILY, 10))
        self.api_base_url_entry.grid(row=2, column=1, sticky="ew", padx=10, pady=5)

        ttk.Label(frame, text="AI模型:", style="Field.TLabel").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.api_model_entry = ttk.Entry(frame, width=36, font=(FONT_FAMILY, 10))
        self.api_model_entry.grid(row=3, column=1, sticky=tk.W, padx=10, pady=5)

    def toggle_common_settings(self):
        self.settings_visible = not self.settings_visible
        if self.settings_visible:
            self.common_settings_frame.pack(fill=tk.X, pady=(0, 12), before=self.workflow_notebook)
            self.settings_button.configure(text="隐藏设置")
        else:
            self.common_settings_frame.pack_forget()
            self.settings_button.configure(text="显示设置")

    def set_initial_pane_sizes(self):
        try:
            width = self.root.winfo_width()
            if width > 1:
                self.main_paned.sashpos(0, max(780, width - 360))
        except tk.TclError:
            pass

    def post_ui(self, func, *args, **kwargs):
        self.ui_queue.put((func, args, kwargs))

    def process_ui_queue(self):
        try:
            while True:
                func, args, kwargs = self.ui_queue.get_nowait()
                func(*args, **kwargs)
        except queue.Empty:
            pass
        try:
            self.root.after(50, self.process_ui_queue)
        except tk.TclError:
            pass

    def create_task_settings(self, parent):
        frame = ttk.LabelFrame(parent, text="生成任务", padding="10")
        frame.pack(fill=tk.X, pady=(0, 10))

        left = ttk.Frame(frame)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = ttk.Frame(frame)
        right.grid(row=0, column=1, sticky="nsew")
        frame.columnconfigure(0, weight=2)
        frame.columnconfigure(1, weight=3)

        drop_text = "拖拽参考视频到这里"
        if not DND_FILES:
            drop_text = "拖拽依赖未安装，可先使用浏览按钮添加视频"
        self.create_drop_zone(left, drop_text, height=96, target="reference")

        self.reference_video_listbox = tk.Listbox(left, height=7, font=(FONT_FAMILY, 10))
        self.video_listbox = self.reference_video_listbox
        self.style_listbox(self.reference_video_listbox)
        self.reference_video_listbox.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.drop_targets.append((self.reference_video_listbox, "reference"))

        video_buttons = self.make_button_bar(left)
        video_buttons.pack(fill=tk.X)
        video_buttons.add_button("添加参考视频...", self.add_reference_videos, width=14)
        video_buttons.add_button("添加参考文件夹...", self.add_reference_folder, width=16)
        video_buttons.add_button("删除选中", self.remove_selected_video, width=10)
        video_buttons.add_button("清空参考", self.clear_reference_videos, width=10)

        ttk.Label(right, text="产品名称:", style="Field.TLabel").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.product_entry = ttk.Entry(right, width=34, font=(FONT_FAMILY, 10))
        self.product_entry.grid(row=0, column=1, sticky="ew", padx=10, pady=5)

        ttk.Label(right, text="产品卖点:", style="Field.TLabel").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.points_entry = ttk.Entry(right, width=48, font=(FONT_FAMILY, 10))
        self.points_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=5)
        self.points_entry.insert(0, "保湿、抗衰老、美白")

        ttk.Label(right, text="改写要求:", style="Field.TLabel").grid(row=2, column=0, sticky=tk.NW, pady=5)
        self.requirements_text = scrolledtext.ScrolledText(right, height=4, wrap=tk.WORD, font=(FONT_FAMILY, 10))
        self.style_text_widget(self.requirements_text)
        self.requirements_text.grid(row=2, column=1, sticky="ew", padx=10, pady=5)
        self.requirements_text.insert(tk.END, "保留核心信息；更口语化；适合短视频口播；控制在300字以内")

        ttk.Label(right, text="Whisper模型:", style="Field.TLabel").grid(row=3, column=0, sticky=tk.W, pady=5)
        model_frame = ttk.Frame(right)
        model_frame.grid(row=3, column=1, sticky=tk.W, padx=10, pady=5)
        self.model_var = tk.StringVar(value="medium")
        for name in ["tiny", "base", "small", "medium", "large-v3"]:
            ttk.Radiobutton(model_frame, text=name, variable=self.model_var, value=name).pack(side=tk.LEFT, padx=(0, 10))

        options = ttk.Frame(right)
        options.grid(row=4, column=1, sticky=tk.W, padx=10, pady=(8, 5))
        self.enable_titles_var = tk.BooleanVar(value=True)
        self.save_transcript_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text="生成标题", variable=self.enable_titles_var).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(options, text="保存原文", variable=self.save_transcript_var).pack(side=tk.LEFT, padx=10)

        action_row = self.make_button_bar(right)
        action_row.grid(row=5, column=1, sticky=tk.EW, padx=8, pady=(10, 0))
        self.start_button = action_row.add_button("开始生成", self.start_generation, style="Primary.TButton", width=12)
        self.stop_button = action_row.add_button("停止", self.stop_generation, width=8, state=tk.DISABLED)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(right, textvariable=self.status_var, style="Info.TLabel").grid(row=6, column=1, sticky=tk.W, padx=8, pady=(4, 0))

        right.columnconfigure(1, weight=1)

    def create_result_area(self, parent):
        self.progress = ttk.Progressbar(parent, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(0, 10))

        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True)

        log_tab = ttk.Frame(notebook, padding="10")
        transcript_tab = ttk.Frame(notebook, padding="10")
        rewrite_tab = ttk.Frame(notebook, padding="10")
        title_tab = ttk.Frame(notebook, padding="10")

        notebook.add(log_tab, text="日志")
        notebook.add(transcript_tab, text="转写文本")
        notebook.add(rewrite_tab, text="改写文案")
        notebook.add(title_tab, text="标题")

        self.log_text = self.make_text_area(log_tab, height=18)
        self.transcript_output = self.make_text_area(transcript_tab, height=18)
        self.rewrite_output = self.make_text_area(rewrite_tab, height=18)
        self.title_output = self.make_text_area(title_tab, height=18)

    def setup_drag_and_drop(self):
        if not DND_FILES:
            return
        try:
            for widget, target in self.drop_targets:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", lambda event, queue_target=target: self.handle_drop(event, queue_target))
        except tk.TclError:
            self.log("拖拽组件未就绪，可使用添加按钮导入视频。")

    def handle_drop(self, event, target="reference"):
        try:
            paths = self.root.tk.splitlist(event.data)
        except Exception:
            paths = event.data.split()
        self.add_paths(paths, target=target)

    def browse_output_dir(self):
        dirname = filedialog.askdirectory(title="选择输出文件夹")
        if dirname:
            self.output_dir = dirname
            self.output_dir_var.set(dirname)

    def load_config(self):
        if config.API_KEY:
            self.api_key_entry.insert(0, config.API_KEY)
        self.api_base_url_entry.insert(0, config.API_BASE_URL)
        self.api_model_entry.insert(0, config.API_MODEL)
        self.output_dir = config.OUTPUT_DIR
        self.output_dir_var.set(config.OUTPUT_DIR)
        self.workflow.output_dir = config.OUTPUT_DIR
        self.update_header_status()

    def save_config(self):
        try:
            api_key, api_base_url, api_model = self.apply_api_settings(require_key=False)
            self.output_dir = self.output_dir_var.get().strip() or self.output_dir
            config.save_user_config(
                api_key=api_key,
                api_base_url=api_base_url,
                api_model=api_model,
                vision_api_model=self.vision_model,
                output_dir=self.output_dir,
            )
            os.makedirs(self.output_dir, exist_ok=True)
            self.update_header_status()
            messagebox.showinfo("成功", "配置已保存")
        except Exception as e:
            messagebox.showerror("错误", f"保存配置失败: {e}")

    def apply_api_settings(self, require_key=True):
        api_key = self.api_key_entry.get().strip()
        if require_key and not api_key:
            raise ValueError("请输入AI API Key")
        api_base_url = self.api_base_url_entry.get().strip() or config.DEFAULT_API_BASE_URL
        api_model = self.api_model_entry.get().strip() or config.DEFAULT_API_MODEL
        config.API_KEY = api_key
        config.API_BASE_URL = api_base_url
        config.API_URL = config._chat_completions_url(api_base_url)
        config.API_MODEL = api_model
        self.update_header_status(api_model)
        return api_key, api_base_url, api_model

    def update_header_status(self, model=None):
        if not hasattr(self, "model_status_label"):
            return
        model_name = model or self.api_model_entry.get().strip() or config.API_MODEL
        self.model_status_label.configure(text=f"模型 {model_name}")

    def refresh_output_dir(self):
        self.output_dir = self.output_dir_var.get().strip() or self.output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self.workflow.output_dir = self.output_dir
        return self.output_dir

    def sync_reference_text(self):
        text = self.get_text(self.reference_text)
        if not text:
            text = self.get_text(self.transcript_output)
        self.workflow.set_reference_text(
            text,
            title=self.product_entry.get().strip() or "参考文案",
            product_name=self.product_entry.get().strip(),
            keywords=self.keywords_from_points(),
        )
        self.log(f"参考文案已同步，长度 {len(text)} 字符")
        return text

    def import_reference_text(self):
        filename = filedialog.askopenfilename(
            title="导入参考文案",
            filetypes=[("文本文件", "*.txt *.md"), ("所有文件", "*.*")],
        )
        if not filename:
            return
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read()
        self.set_text(self.reference_text, content)
        self.sync_reference_text()

    def save_project_dialog(self):
        output_dir = self.refresh_output_dir()
        default_path = os.path.join(output_dir, "workflow_project.json")
        filename = filedialog.asksaveasfilename(
            title="保存项目",
            initialfile=os.path.basename(default_path),
            initialdir=output_dir,
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if filename:
            self.save_project(filename)

    def load_project_dialog(self):
        filename = filedialog.askopenfilename(
            title="加载项目",
            initialdir=self.refresh_output_dir(),
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if filename:
            self.load_project(filename)

    def save_project(self, path=None):
        if path is None:
            path = os.path.join(self.refresh_output_dir(), "workflow_project.json")
        self.sync_reference_text()
        result = self.workflow.save_project(path)
        saved_path = result.get("project_path", result) if isinstance(result, dict) else result
        self.log(f"项目已保存: {saved_path}")
        self.log_agent(f"项目已保存: {saved_path}")
        return saved_path

    def load_project(self, path):
        self.workflow = WorkflowState.load_project(path)
        self.output_dir = self.workflow.output_dir or self.output_dir
        self.output_dir_var.set(self.output_dir)
        self.set_text(self.reference_text, self.workflow.reference_text or "")
        self.refresh_storyboard_output()
        self.refresh_material_output()
        self.refresh_voiceover_output()
        self.refresh_assembly_output()
        self.log(f"项目已加载: {path}")
        self.log_agent(f"项目已加载: {path}")

    def start_reference_transcription(self):
        if not self.reference_video_queue:
            self.set_status("请先添加视频文件")
            self.log("转写参考未启动：请先添加参考视频。")
            messagebox.showwarning("提示", "请先添加参考视频")
            return
        missing_videos = [path for path in self.reference_video_queue if not os.path.exists(path)]
        if missing_videos:
            message = f"视频文件不存在: {missing_videos[0]}"
            self.set_status("视频文件不存在")
            self.log(f"转写参考未启动：{message}")
            messagebox.showwarning("提示", message)
            return
        task = {
            "videos": list(self.reference_video_queue),
            "output_dir": self.refresh_output_dir(),
            "model_size": self.model_var.get(),
        }
        self.is_running = True
        self.set_running_state(True)
        self.set_progress(0)
        self.set_status(f"准备转写 {len(task['videos'])} 个视频")
        self.log(f"转写参考任务已启动，共 {len(task['videos'])} 个视频，模型: {task['model_size']}")
        self.log_agent(f"已启动参考转写，共 {len(task['videos'])} 个视频。")
        threading.Thread(target=self.reference_transcription_worker, args=(task,), daemon=True).start()

    def reference_transcription_worker(self, task):
        transcripts = []
        videos = task["videos"]
        total = len(videos)
        try:
            for index, video_path in enumerate(videos, 1):
                if not self.is_running:
                    break
                video_name = self.safe_name(video_path)
                self.set_status(f"转写第 {index}/{total} 个视频")
                self.log(f"参考转写: {os.path.basename(video_path)}")
                self.set_status(f"正在转写第 {index}/{total} 个视频")
                transcript_text = self.transcribe_reference_video(
                    video_path,
                    output_dir=task["output_dir"],
                    model_size=task["model_size"],
                )
                transcripts.append(f"===== {video_name} =====\n{transcript_text}")
                self.append_text(self.transcript_output, f"\n===== {video_name} =====\n{transcript_text}\n")
                self.set_progress(index / max(total, 1) * 100)

            merged = "\n\n".join(transcripts).strip()
            if merged:
                self.set_text(self.reference_text, merged)
                self.workflow.set_reference_text(
                    merged,
                    title=self.product_entry.get().strip() or "参考文案",
                    product_name=self.product_entry.get().strip(),
                    keywords=self.keywords_from_points(),
                )
                self.log("参考转写完成，已写入参考文案页。")
        except Exception as e:
            self.show_error(str(e))
            self.log(f"[参考转写错误] {e}")
        finally:
            self.is_running = False
            self.set_running_state(False)
            self.set_status("就绪")

    def transcribe_reference_video(self, video_path, output_dir, model_size):
        return self.transcribe_video_with_subprocess(video_path, output_dir, model_size)

    def transcribe_video_with_subprocess(self, video_path, output_dir, model_size):
        os.makedirs(output_dir, exist_ok=True)
        result_file = tempfile.NamedTemporaryFile(
            prefix="reference_transcription_",
            suffix=".json",
            dir=output_dir,
            delete=False,
        )
        result_path = result_file.name
        result_file.close()

        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference_transcription_cli.py")
        command = [
            sys.executable,
            script_path,
            "--video",
            video_path,
            "--output-dir",
            output_dir,
            "--model-size",
            model_size,
            "--language",
            "zh",
            "--result-json",
            result_path,
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                self.log(line)
        if result.stderr.strip():
            for line in result.stderr.strip().splitlines()[-12:]:
                self.log(line)

        payload = {}
        if os.path.exists(result_path):
            with open(result_path, "r", encoding="utf-8") as f:
                payload = json.load(f)

        try:
            os.remove(result_path)
        except OSError:
            pass

        if result.returncode != 0 or not payload.get("ok"):
            error = payload.get("error") or f"转写子进程失败，退出码 {result.returncode}"
            raise RuntimeError(error)
        return payload.get("transcript", "")

    def start_storyboard_analysis(self):
        try:
            self.apply_api_settings(require_key=True)
            reference_text = self.sync_reference_text()
            if not reference_text.strip():
                messagebox.showwarning("提示", "请先输入或转写参考文案")
                return
            output_dir = self.refresh_output_dir()
            target_duration = self._float_or_none(self.storyboard_duration_var.get())
            shot_count = self._int_or_none(self.storyboard_count_var.get())
            threading.Thread(
                target=self.storyboard_worker,
                args=(output_dir, target_duration, shot_count),
                daemon=True,
            ).start()
        except Exception as e:
            self.show_error(str(e))

    def storyboard_worker(self, output_dir, target_duration, shot_count):
        try:
            self.set_status("正在分析分镜")
            self.log("正在调用 AI 分析分镜...")
            analysis = self.workflow.analyze_storyboard(
                target_duration_seconds=target_duration,
                shot_count=shot_count,
                model=config.API_MODEL,
            )
            self.refresh_storyboard_output()
            self.log(f"分镜分析完成，共 {len(analysis.shots)} 个分镜")
        except Exception as e:
            self.log(f"[分镜分析错误] {e}")
            self.show_error(str(e))
        finally:
            self.set_status("就绪")

    def confirm_material_tagging(self):
        if not self.material_video_queue:
            messagebox.showwarning("提示", "请先添加素材视频")
            return
        if not messagebox.askyesno("确认", "将调用视觉模型分析素材视频列表，是否继续？"):
            return
        self.start_material_tagging()

    def start_material_tagging(self):
        try:
            if not self.material_video_queue:
                messagebox.showwarning("提示", "请先添加素材视频")
                return
            self.apply_api_settings(require_key=True)
            output_dir = self.refresh_output_dir()
            videos = list(self.material_video_queue)
            vision_model = self.vision_model or config.VISION_API_MODEL
            threading.Thread(
                target=self.material_tagging_worker,
                args=(videos, output_dir, vision_model),
                daemon=True,
            ).start()
        except Exception as e:
            self.show_error(str(e))

    def material_tagging_worker(self, videos, output_dir, vision_model):
        try:
            self.set_status("正在打标素材")
            self.log(f"正在分析 {len(videos)} 个素材视频...")
            assets = self.workflow.analyze_materials(
                videos,
                output_dir=output_dir,
                vision_model=vision_model,
                max_frames=4,
                every_seconds=3,
            )
            self.refresh_material_output()
            self.log(f"素材打标完成，共 {len(assets)} 个素材")
        except Exception as e:
            self.log(f"[素材打标错误] {e}")
            self.show_error(str(e))
        finally:
            self.set_status("就绪")

    def confirm_voiceover_generation(self):
        if not self.workflow.storyboard_analysis or not self.workflow.storyboard_analysis.shots:
            messagebox.showwarning("提示", "请先完成分镜分析")
            return
        if not messagebox.askyesno("确认", "将调用 TTS 模型按分镜生成配音，是否继续？"):
            return
        self.start_voiceover_generation()

    def start_voiceover_generation(self):
        try:
            self.apply_api_settings(require_key=True)
            output_dir = self.refresh_output_dir()
            threading.Thread(
                target=self.voiceover_worker,
                args=(output_dir,),
                daemon=True,
            ).start()
        except Exception as e:
            self.show_error(str(e))

    def voiceover_worker(self, output_dir):
        try:
            self.set_status("正在生成配音")
            self.log("正在调用 TTS 生成分镜配音...")
            segments = self.workflow.generate_voiceovers(output_dir=output_dir)
            self.refresh_voiceover_output()
            self.log(f"配音生成完成，共 {len(segments)} 段")
        except Exception as e:
            self.log(f"[配音错误] {e}")
            self.show_error(str(e))
        finally:
            self.set_status("就绪")

    def confirm_assembly_plan(self):
        if not messagebox.askyesno("确认", "将根据分镜、素材和配音生成合成计划，不会直接渲染视频。是否继续？"):
            return
        self.start_assembly_plan()

    def start_assembly_plan(self):
        output_dir = self.refresh_output_dir()
        threading.Thread(target=self.assembly_worker, args=(output_dir,), daemon=True).start()

    def assembly_worker(self, output_dir):
        try:
            self.set_status("正在生成合成计划")
            self.log("正在生成合成计划...")
            plan = self.workflow.build_assembly_plan(output_dir=output_dir)
            self.refresh_assembly_output()
            self.log(f"合成计划完成，共 {len(plan.get('segments', [])) if isinstance(plan, dict) else len(getattr(plan, 'items', []))} 段")
        except Exception as e:
            self.log(f"[合成计划错误] {e}")
            self.show_error(str(e))
        finally:
            self.set_status("就绪")

    def refresh_storyboard_output(self):
        analysis = self.workflow.storyboard_analysis
        self.set_text(self.storyboard_summary_output, gui_pages.render_storyboard_summary(analysis))
        payload = analysis.to_dict() if hasattr(analysis, "to_dict") else analysis
        self.set_text(self.storyboard_json_output, json.dumps(payload or {}, ensure_ascii=False, indent=2))

    def refresh_material_output(self):
        self.set_text(self.material_output, gui_pages.render_material_summary(self.workflow.material_assets))

    def refresh_voiceover_output(self):
        self.set_text(self.voiceover_output, gui_pages.render_voiceover_summary(self.workflow.voiceover_segments))

    def refresh_assembly_output(self):
        summary = gui_pages.render_assembly_summary(self.workflow.assembly_plan)
        command = self.assembly_command_preview()
        if command:
            summary = f"{summary}\n\nFFmpeg 命令预览:\n{command}"
        self.set_text(self.assembly_output, summary)

    def _float_or_none(self, value):
        value = str(value or "").strip()
        if not value:
            return None
        return float(value)

    def _int_or_none(self, value):
        value = str(value or "").strip()
        if not value:
            return None
        return int(value)

    def keywords_from_points(self):
        raw = self.points_entry.get().strip()
        if not raw:
            return []
        normalized = raw.replace("，", ",").replace("、", ",").replace("；", ",").replace(";", ",")
        return [item.strip() for item in normalized.split(",") if item.strip()]

    def assembly_command_preview(self):
        plan = self.workflow.assembly_plan
        if not plan:
            return ""
        metadata = getattr(plan, "metadata", {}) or {}
        renderer = metadata.get("renderer", {}) if isinstance(metadata, dict) else {}
        command = renderer.get("command") if isinstance(renderer, dict) else None
        if isinstance(command, list):
            return " ".join(str(part) for part in command)
        return str(command or "")

    def add_reference_videos(self):
        self.add_videos(target="reference")

    def add_material_videos(self):
        self.add_videos(target="material")

    def add_reference_folder(self):
        self.add_folder(target="reference")

    def add_material_folder(self):
        self.add_folder(target="material")

    def add_videos(self, target="reference"):
        filenames = filedialog.askopenfilename(
            title="选择视频文件",
            filetypes=[("视频文件", "*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm"), ("所有文件", "*.*")],
            multiple=True,
        )
        self.add_paths(filenames, target=target)

    def add_folder(self, target="reference"):
        dirname = filedialog.askdirectory(title="选择文件夹")
        if dirname:
            self.add_paths([dirname], target=target)

    def add_paths(self, paths, target="reference"):
        added = 0
        for raw_path in paths:
            path = os.path.abspath(str(raw_path))
            if os.path.isdir(path):
                for root, _, filenames in os.walk(path):
                    for filename in filenames:
                        if filename.lower().endswith(VIDEO_EXTENSIONS):
                            if self.add_video_path(os.path.join(root, filename), target=target):
                                added += 1
            elif path.lower().endswith(VIDEO_EXTENSIONS):
                if self.add_video_path(path, target=target):
                    added += 1
        label = self.video_target_label(target)
        if added:
            self.log(f"已添加 {added} 个{label}视频")
        elif paths:
            self.log("未找到可用视频文件")

    def video_target_label(self, target):
        return "素材" if target == "material" else "参考"

    def queue_for_target(self, target):
        return self.material_video_queue if target == "material" else self.reference_video_queue

    def listbox_for_target(self, target):
        return self.material_video_listbox if target == "material" else self.reference_video_listbox

    def add_video_path(self, path, target="reference"):
        queue_for_target = self.queue_for_target(target)
        if path in queue_for_target:
            return False
        queue_for_target.append(path)
        self.listbox_for_target(target).insert(tk.END, os.path.basename(path))
        return True

    def remove_selected_reference_video(self):
        self.remove_selected_video(target="reference")

    def remove_selected_material_video(self):
        self.remove_selected_video(target="material")

    def remove_selected_video(self, target="reference"):
        listbox = self.listbox_for_target(target)
        selection = listbox.curselection()
        if not selection:
            return
        index = selection[0]
        listbox.delete(index)
        self.queue_for_target(target).pop(index)

    def clear_reference_videos(self):
        self.clear_videos(target="reference")

    def clear_material_videos(self):
        self.clear_videos(target="material")

    def clear_all_videos(self):
        self.clear_videos(target="reference")
        self.clear_videos(target="material")

    def clear_videos(self, target="reference"):
        self.listbox_for_target(target).delete(0, tk.END)
        self.queue_for_target(target).clear()

    def create_drop_zone(self, parent, text, height, target="reference"):
        drop_frame = tk.Frame(
            parent,
            bg=DROP_BG,
            height=height,
            highlightthickness=1,
            highlightbackground=DROP_BORDER,
            highlightcolor=ACCENT,
            bd=0,
        )
        drop_frame.pack(fill=tk.X, pady=(0, 10))
        drop_frame.pack_propagate(False)
        drop_label = tk.Label(
            drop_frame,
            text=text,
            bg=DROP_BG,
            fg=ACCENT,
            font=(FONT_FAMILY, 13, "bold"),
        )
        drop_label.pack(expand=True)
        self.drop_targets.extend([(drop_frame, target), (drop_label, target)])
        return drop_frame, drop_label

    def style_listbox(self, listbox):
        listbox.configure(
            bg=PANEL_BG,
            fg=TEXT_FG,
            selectbackground=ACCENT,
            selectforeground="#ffffff",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            relief=tk.FLAT,
            bd=0,
            activestyle="none",
            exportselection=False,
        )

    def style_text_widget(self, text):
        text.configure(
            bg=TEXT_BG,
            fg=TEXT_FG,
            insertbackground=TEXT_FG,
            selectbackground=ACCENT,
            selectforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            padx=10,
            pady=8,
            spacing1=2,
            spacing3=4,
        )

    def make_text_area(self, parent, height):
        text = scrolledtext.ScrolledText(parent, height=height, wrap=tk.WORD, font=(FONT_FAMILY, 10))
        self.style_text_widget(text)
        text.pack(fill=tk.BOTH, expand=True)
        return text

    def get_text(self, text_widget):
        return text_widget.get("1.0", tk.END).strip()

    def set_text(self, text_widget, content):
        if threading.current_thread() is not self.main_thread:
            self.post_ui(self.set_text, text_widget, content)
            return
        text_widget.delete("1.0", tk.END)
        text_widget.insert(tk.END, content)

    def append_text(self, text_widget, content):
        if threading.current_thread() is not self.main_thread:
            self.post_ui(self.append_text, text_widget, content)
            return
        text_widget.insert(tk.END, content)
        text_widget.see(tk.END)

    def log(self, message):
        self.append_text(self.log_text, message + "\n")

    def set_status(self, message):
        if threading.current_thread() is not self.main_thread:
            self.post_ui(self.set_status, message)
            return
        self.status_var.set(message)

    def set_running_state(self, running):
        if threading.current_thread() is not self.main_thread:
            self.post_ui(self.set_running_state, running)
            return
        for button_name in ("reference_transcribe_button", "start_button"):
            button = getattr(self, button_name, None)
            if button is not None:
                button.config(state=tk.DISABLED if running else tk.NORMAL)
        if getattr(self, "stop_button", None) is not None:
            self.stop_button.config(state=tk.NORMAL if running else tk.DISABLED)

    def set_progress(self, value):
        if threading.current_thread() is not self.main_thread:
            self.post_ui(self.set_progress, value)
            return
        self.progress.config(value=value)

    def show_error(self, message):
        if threading.current_thread() is not self.main_thread:
            self.post_ui(self.show_error, message)
            return
        messagebox.showerror("错误", message)

    def log_agent(self, message):
        if threading.current_thread() is not self.main_thread:
            self.post_ui(self.log_agent, message)
            return
        self.agent_output.insert(tk.END, message + "\n")
        self.agent_output.see(tk.END)

    def handle_agent_command(self):
        command = self.agent_entry.get().strip()
        if not command:
            return
        self.agent_entry.delete(0, tk.END)
        display = command
        if "key" in command.lower() or "密钥" in command:
            display = "[已隐藏密钥命令]"
        self.log_agent(f"> {display}")
        self.execute_agent_command(command)

    def execute_agent_command(self, command):
        command = command.strip()
        normalized = command.lower()

        try:
            if normalized in {"帮助", "help", "?"}:
                self.show_agent_help()
            elif normalized in {"状态", "status"}:
                self.show_agent_status()
            elif normalized in {"确认", "confirm", "yes", "执行"}:
                self.confirm_agent_actions()
            elif normalized in {"取消", "cancel", "no"}:
                self.pending_agent_actions = None
                self.log_agent("已取消待确认操作。")
            elif normalized in {"开始", "开始生成", "run", "start"}:
                self.queue_agent_actions(
                    [{"action": "start_generation"}],
                    "将开始处理参考视频列表中的视频。",
                    require_confirmation=True,
                )
            elif normalized in {"转写参考", "参考转写", "transcribe reference"}:
                self.queue_agent_actions(
                    [{"action": "transcribe_reference"}],
                    "将把参考视频列表转写为参考文案。",
                    require_confirmation=False,
                )
            elif normalized in {"分析分镜", "分镜分析", "analyze storyboard"}:
                self.queue_agent_actions(
                    [{"action": "analyze_storyboard"}],
                    "将根据参考文案生成分镜。",
                    require_confirmation=False,
                )
            elif normalized in {"打标素材", "素材打标", "tag materials"}:
                self.queue_agent_actions(
                    [{"action": "tag_materials"}],
                    "将调用视觉模型批量分析当前视频素材。",
                    require_confirmation=True,
                )
            elif normalized in {"生成配音", "配音", "generate voiceovers"}:
                self.queue_agent_actions(
                    [{"action": "generate_voiceovers"}],
                    "将调用 TTS 模型为分镜生成配音。",
                    require_confirmation=True,
                )
            elif normalized in {"生成合成计划", "合成计划", "build assembly plan"}:
                self.queue_agent_actions(
                    [{"action": "build_assembly_plan"}],
                    "将生成视频音频匹配合成计划，不会直接渲染视频。",
                    require_confirmation=True,
                )
            elif normalized in {"保存项目", "save project"}:
                self.save_project()
            elif command.startswith("加载项目 ") or command.startswith("load project "):
                value = command.split(" ", 1)[1].strip()
                self.load_project(str(Path(value).expanduser()))
            elif normalized in {"停止", "stop"}:
                self.stop_generation()
                self.log_agent("已请求停止任务。")
            elif normalized in {"清空", "清空视频", "clear"}:
                self.queue_agent_actions(
                    [{"action": "clear_videos"}],
                    "将清空参考视频和素材视频列表。",
                    require_confirmation=True,
                )
            elif normalized in {"清空参考", "清空参考视频", "clear reference"}:
                self.queue_agent_actions(
                    [{"action": "clear_reference_videos"}],
                    "将清空参考视频列表。",
                    require_confirmation=True,
                )
            elif normalized in {"清空素材", "清空素材视频", "clear materials"}:
                self.queue_agent_actions(
                    [{"action": "clear_material_videos"}],
                    "将清空素材视频列表。",
                    require_confirmation=True,
                )
            elif normalized in {"保存配置", "save config"}:
                self.save_config()
                self.log_agent("已请求保存配置。")
            elif normalized in {"查看记忆", "记忆", "memory", "mem"}:
                self.show_memory()
            elif normalized in {"应用最近任务", "恢复最近任务", "根据记忆填充", "restore last task"}:
                self.restore_last_task()
            elif normalized in {"清空记忆", "clear memory"}:
                self.queue_agent_actions(
                    [{"action": "clear_memory"}],
                    "将清空长期记忆。",
                    require_confirmation=True,
                )
            elif command.startswith("记住风格 "):
                self.remember_with_category(command, "记住风格 ", "style")
            elif command.startswith("记住禁用表达 "):
                self.remember_with_category(command, "记住禁用表达 ", "blocked_expression")
            elif command.startswith("记住产品 "):
                self.remember_with_category(command, "记住产品 ", "product")
            elif command.startswith("记住预设 "):
                self.remember_with_category(command, "记住预设 ", "preset")
            elif command.startswith("使用预设 "):
                self.apply_prompt_preset(command[len("使用预设 "):].strip())
            elif command.startswith("记住 ") or command.startswith("remember "):
                text = command.split(" ", 1)[1].strip()
                if not text:
                    self.log_agent("请在“记住”后面输入要保存的内容。")
                else:
                    item = app_memory.remember(text)
                    self.log_agent(f"已记住 [{item['id']}]: {item['text']}")
            elif command.startswith("忘记 ") or command.startswith("forget "):
                raw_id = command.split(" ", 1)[1].strip()
                try:
                    memory_id = int(raw_id)
                except ValueError:
                    self.log_agent("请使用记忆编号，例如：忘记 2")
                else:
                    if app_memory.forget(memory_id):
                        self.log_agent(f"已忘记 [{memory_id}]。")
                    else:
                        self.log_agent(f"没有找到记忆 [{memory_id}]。")
            elif command.startswith("问 ") or command.startswith("ask "):
                question = command.split(" ", 1)[1].strip()
                if not question:
                    self.log_agent("请在“问”后面输入问题。")
                else:
                    self.apply_api_settings(require_key=True)
                    threading.Thread(target=self.ask_agent_worker, args=(question,), daemon=True).start()
            elif command.startswith("抽帧 ") or command.startswith("extract frames "):
                value = command.split(" ", 1)[1].strip()
                output_dir = self.refresh_output_dir()
                threading.Thread(target=self.extract_frames_worker, args=(value, output_dir), daemon=True).start()
            elif command.startswith("分析素材 ") or command.startswith("analyze material "):
                value = command.split(" ", 1)[1].strip()
                self.apply_api_settings(require_key=True)
                output_dir = self.refresh_output_dir()
                vision_model = self.vision_model or config.VISION_API_MODEL
                threading.Thread(target=self.analyze_material_worker, args=(value, output_dir, vision_model), daemon=True).start()
            elif self.try_set_command(command):
                return
            elif self.try_toggle_command(command):
                return
            elif self.try_add_command(command):
                return
            else:
                self.interpret_natural_language(command)
        except Exception as e:
            self.log_agent(f"[错误] {e}")

    def show_agent_help(self):
        self.log_agent(
            "\n".join(
                [
                    "可用命令示例:",
                    "  产品名称 玻尿酸面膜",
                    "  卖点 补水、修护、熬夜急救",
                    "  要求 更口语化，适合小红书口播，200字以内",
                    "  模型 small",
                    "  AI模型 mimo-v2.5-pro",
                    "  视觉模型 mimo-v2.5",
                    "  BaseURL https://token-plan-cn.xiaomimimo.com/v1",
                    "  输出目录 ~/Documents/短视频文案输出",
                    "  开启标题 / 关闭标题",
                    "  保存原文 / 不保存原文",
                    "  添加参考 /path/to/video.mp4",
                    "  添加素材 /path/to/material.mp4",
                    "  添加参考文件夹 /path/to/folder",
                    "  添加素材文件夹 /path/to/folder",
                    "  清空参考视频 / 清空素材视频 / 清空视频",
                    "  转写参考",
                    "  分析分镜",
                    "  打标素材",
                    "  生成配音",
                    "  生成合成计划",
                    "  保存项目",
                    "  加载项目 /path/to/workflow_project.json",
                    "  抽帧 /path/to/video.mp4",
                    "  分析素材 /path/to/video.mp4",
                    "  记住 我的默认风格是短句、强钩子、不要夸大功效",
                    "  记住风格 小红书: 短句、强钩子、强情绪",
                    "  记住禁用表达 不要说100%有效、根治、最安全",
                    "  记住产品 玻尿酸面膜: 补水、修护、熬夜急救",
                    "  记住预设 小红书 = 短句、强情绪、不要夸大功效",
                    "  使用预设 小红书",
                    "  查看记忆",
                    "  应用最近任务",
                    "  忘记 1",
                    "  清空记忆",
                    "  开始生成 / 停止",
                    "  确认 / 取消",
                    "  状态",
                    "  问 帮我把这个产品卖点写得更像口播",
                ]
            )
        )

    def queue_agent_actions(self, actions, summary, require_confirmation=False):
        if threading.current_thread() is not self.main_thread:
            self.post_ui(self.queue_agent_actions, actions, summary, require_confirmation)
            return

        self.log_agent(f"计划: {summary}")
        for index, action in enumerate(actions, 1):
            self.log_agent(f"  {index}. {action.get('action')}")

        if require_confirmation:
            self.pending_agent_actions = actions
            self.log_agent("输入“确认”执行，或输入“取消”放弃。")
            return

        self.apply_agent_actions(actions)

    def confirm_agent_actions(self):
        if not self.pending_agent_actions:
            self.log_agent("没有待确认操作。")
            return
        actions = self.pending_agent_actions
        self.pending_agent_actions = None
        self.apply_agent_actions(actions)

    def apply_agent_actions(self, actions):
        if threading.current_thread() is not self.main_thread:
            self.post_ui(self.apply_agent_actions, actions)
            return

        for action in actions:
            name = action.get("action")
            value = action.get("value")

            if name == "set_product_name":
                self.set_field_value(self.product_entry, value or "")
                self.log_agent(f"已设置产品名称: {value}")
            elif name == "set_selling_points":
                self.set_field_value(self.points_entry, value or "")
                self.log_agent(f"已设置产品卖点: {value}")
            elif name == "set_requirements":
                self.requirements_text.delete("1.0", tk.END)
                self.requirements_text.insert(tk.END, value or "")
                self.log_agent("已更新改写要求。")
            elif name == "set_whisper_model":
                if value in {"tiny", "base", "small", "medium", "large-v3"}:
                    self.model_var.set(value)
                    self.log_agent(f"已设置Whisper模型: {value}")
                else:
                    self.log_agent("忽略无效Whisper模型。")
            elif name == "set_ai_model":
                self.set_field_value(self.api_model_entry, value or config.DEFAULT_API_MODEL)
                self.log_agent(f"已设置AI模型: {value}")
            elif name == "set_vision_model":
                self.vision_model = value or config.DEFAULT_VISION_API_MODEL
                config.VISION_API_MODEL = self.vision_model
                self.log_agent(f"已设置视觉模型: {self.vision_model}")
            elif name == "set_output_dir":
                output_dir = str(Path(value or "").expanduser())
                self.output_dir_var.set(output_dir)
                self.output_dir = output_dir
                self.log_agent(f"已设置输出目录: {output_dir}")
            elif name == "toggle_titles":
                self.enable_titles_var.set(bool(value))
                self.log_agent(f"生成标题: {'开' if value else '关'}")
            elif name == "toggle_save_transcript":
                self.save_transcript_var.set(bool(value))
                self.log_agent(f"保存原文: {'开' if value else '关'}")
            elif name == "add_path":
                self.add_paths([value], target="reference")
            elif name == "add_reference_path":
                self.add_paths([value], target="reference")
            elif name == "add_material_path":
                self.add_paths([value], target="material")
            elif name == "clear_videos":
                self.clear_all_videos()
                self.log_agent("已清空参考视频和素材视频列表。")
            elif name == "clear_reference_videos":
                self.clear_reference_videos()
                self.log_agent("已清空参考视频列表。")
            elif name == "clear_material_videos":
                self.clear_material_videos()
                self.log_agent("已清空素材视频列表。")
            elif name == "clear_memory":
                app_memory.clear_facts()
                self.log_agent("已清空长期记忆。")
            elif name == "start_generation":
                self.start_generation()
                self.log_agent("已请求开始生成。")
            elif name == "transcribe_reference":
                self.start_reference_transcription()
                self.log_agent("已请求转写参考。")
            elif name == "analyze_storyboard":
                self.start_storyboard_analysis()
                self.log_agent("已请求分析分镜。")
            elif name == "tag_materials":
                self.start_material_tagging()
                self.log_agent("已请求素材打标。")
            elif name == "generate_voiceovers":
                self.start_voiceover_generation()
                self.log_agent("已请求生成配音。")
            elif name == "build_assembly_plan":
                self.start_assembly_plan()
                self.log_agent("已请求生成合成计划。")
            elif name == "save_project":
                self.save_project(value)
            elif name == "load_project":
                if value:
                    self.load_project(str(Path(value).expanduser()))
                else:
                    self.log_agent("加载项目需要提供路径。")
            elif name == "remember":
                if value:
                    item = app_memory.remember(value)
                    self.log_agent(f"已记住 [{item['id']}]: {item['text']}")
            elif name == "extract_frames":
                output_dir = self.refresh_output_dir()
                threading.Thread(target=self.extract_frames_worker, args=(value, output_dir), daemon=True).start()
            elif name == "analyze_material":
                output_dir = self.refresh_output_dir()
                vision_model = self.vision_model or config.VISION_API_MODEL
                threading.Thread(target=self.analyze_material_worker, args=(value, output_dir, vision_model), daemon=True).start()
            else:
                self.log_agent(f"未知动作已跳过: {name}")

    def show_agent_status(self):
        status = [
            f"参考视频数量: {len(self.reference_video_queue)}",
            f"素材视频数量: {len(self.material_video_queue)}",
            f"产品名称: {self.product_entry.get().strip() or '未设置'}",
            f"产品卖点: {self.points_entry.get().strip() or '未设置'}",
            f"Whisper模型: {self.model_var.get()}",
            f"AI模型: {self.api_model_entry.get().strip() or config.DEFAULT_API_MODEL}",
            f"视觉模型: {self.vision_model or config.VISION_API_MODEL}",
            f"输出目录: {self.output_dir_var.get().strip() or self.output_dir}",
            f"生成标题: {'开' if self.enable_titles_var.get() else '关'}",
            f"保存原文: {'开' if self.save_transcript_var.get() else '关'}",
            f"运行状态: {'运行中' if self.is_running else '空闲'}",
        ]
        self.log_agent("\n".join(status))

    def show_memory(self):
        facts = app_memory.list_facts()
        last_task = app_memory.get_last_task()

        if not facts and not last_task:
            self.log_agent("当前没有记忆。")
            return

        if facts:
            self.log_agent("长期记忆:")
            for item in facts:
                self.log_agent(f"  [{item.get('id')}] ({item.get('category', 'note')}) {item.get('text', '')}")
        else:
            self.log_agent("长期记忆: 无")

        if last_task:
            self.log_agent("最近任务:")
            for key, value in last_task.items():
                self.log_agent(f"  {key}: {value}")

    def restore_last_task(self):
        last_task = app_memory.get_last_task()
        if not last_task:
            self.log_agent("没有最近任务可恢复。")
            return

        if last_task.get("product_name"):
            self.set_field_value(self.product_entry, last_task["product_name"])
        if last_task.get("selling_points"):
            self.set_field_value(self.points_entry, last_task["selling_points"])
        if last_task.get("requirements"):
            self.requirements_text.delete("1.0", tk.END)
            self.requirements_text.insert(tk.END, last_task["requirements"])
        if last_task.get("model_size"):
            self.model_var.set(last_task["model_size"])
        if "enable_titles" in last_task:
            self.enable_titles_var.set(bool(last_task["enable_titles"]))
        if "save_transcript" in last_task:
            self.save_transcript_var.set(bool(last_task["save_transcript"]))
        if last_task.get("output_dir"):
            self.output_dir_var.set(last_task["output_dir"])
            self.output_dir = last_task["output_dir"]

        self.log_agent("已恢复最近任务设置。")

    def try_set_command(self, command):
        mappings = [
            ("产品名称", self.product_entry, "产品名称"),
            ("产品卖点", self.points_entry, "产品卖点"),
            ("卖点", self.points_entry, "产品卖点"),
            ("产品", self.product_entry, "产品名称"),
            ("AI模型", self.api_model_entry, "AI模型"),
            ("api模型", self.api_model_entry, "AI模型"),
            ("BaseURL", self.api_base_url_entry, "AI Base URL"),
            ("baseurl", self.api_base_url_entry, "AI Base URL"),
            ("Base URL", self.api_base_url_entry, "AI Base URL"),
            ("输出目录", self.output_dir_var, "输出目录"),
        ]

        for prefix, target, label in mappings:
            value = self.value_after_prefix(command, prefix)
            if value is None:
                continue
            if not value:
                self.log_agent(f"{label}不能为空。")
                return True
            self.set_field_value(target, value)
            if target is self.output_dir_var:
                self.output_dir = str(Path(value).expanduser())
            self.log_agent(f"已设置{label}: {value}")
            return True

        vision_model = self.value_after_prefix(command, "视觉模型")
        if vision_model is not None:
            if not vision_model:
                self.log_agent("视觉模型不能为空。")
                return True
            self.vision_model = vision_model
            config.VISION_API_MODEL = vision_model
            self.log_agent(f"已设置视觉模型: {vision_model}")
            return True

        model_value = self.value_after_prefix(command, "模型")
        if model_value is not None:
            if model_value not in {"tiny", "base", "small", "medium", "large-v3"}:
                self.log_agent("Whisper模型可选: tiny、base、small、medium、large-v3")
                return True
            self.model_var.set(model_value)
            self.log_agent(f"已设置Whisper模型: {model_value}")
            return True

        requirements = self.value_after_prefix(command, "要求")
        if requirements is None:
            requirements = self.value_after_prefix(command, "改写要求")
        if requirements is not None:
            if not requirements:
                self.log_agent("改写要求不能为空。")
                return True
            self.requirements_text.delete("1.0", tk.END)
            self.requirements_text.insert(tk.END, requirements)
            self.log_agent("已更新改写要求。")
            return True

        return False

    def remember_with_category(self, command, prefix, category):
        text = command[len(prefix):].strip()
        if not text:
            self.log_agent("记忆内容不能为空。")
            return
        item = app_memory.remember(text, category=category)
        self.log_agent(f"已记住 [{item['id']}] ({category}): {item['text']}")

    def apply_prompt_preset(self, keyword):
        if not keyword:
            self.log_agent("请指定预设名称或关键词。")
            return

        presets = [
            item for item in app_memory.list_facts()
            if item.get("category") == "preset" and keyword in item.get("text", "")
        ]
        if not presets:
            self.log_agent(f"没有找到包含“{keyword}”的预设。")
            return

        preset_text = presets[-1]["text"]
        if "=" in preset_text:
            preset_text = preset_text.split("=", 1)[1].strip()
        elif "：" in preset_text:
            preset_text = preset_text.split("：", 1)[1].strip()
        elif ":" in preset_text:
            preset_text = preset_text.split(":", 1)[1].strip()

        self.requirements_text.delete("1.0", tk.END)
        self.requirements_text.insert(tk.END, preset_text)
        self.log_agent(f"已应用预设: {keyword}")

    def value_after_prefix(self, command, prefix):
        if not command.lower().startswith(prefix.lower()):
            return None
        value = command[len(prefix):].strip()
        if value.startswith(("=", "：", ":")):
            value = value[1:].strip()
        return value

    def set_field_value(self, target, value):
        if isinstance(target, tk.StringVar):
            target.set(value)
            return
        target.delete(0, tk.END)
        target.insert(0, value)

    def try_toggle_command(self, command):
        normalized = command.replace(" ", "").lower()
        toggles = [
            ("原文", self.save_transcript_var, "保存原文"),
            ("标题", self.enable_titles_var, "生成标题"),
        ]
        for keyword, var, label in toggles:
            if keyword not in normalized:
                continue
            if normalized.startswith(("开启", "打开", "启用", "保存", "on", "enable")):
                var.set(True)
                self.log_agent(f"已开启{label}。")
                return True
            if normalized.startswith(("关闭", "关掉", "禁用", "不保存", "off", "disable")):
                var.set(False)
                self.log_agent(f"已关闭{label}。")
                return True
        return False

    def try_add_command(self, command):
        lowered = command.lower()
        prefixes = [
            ("添加素材文件夹", "material"),
            ("添加素材视频", "material"),
            ("添加素材文件", "material"),
            ("添加素材", "material"),
            ("添加参考文件夹", "reference"),
            ("添加参考视频", "reference"),
            ("添加参考文件", "reference"),
            ("添加参考", "reference"),
            ("add material folder", "material"),
            ("add material file", "material"),
            ("add material", "material"),
            ("add reference folder", "reference"),
            ("add reference file", "reference"),
            ("add reference", "reference"),
            ("添加文件夹", "reference"),
            ("添加文件", "reference"),
            ("添加", "reference"),
            ("add folder", "reference"),
            ("add file", "reference"),
            ("add", "reference"),
        ]
        for prefix, target in prefixes:
            if not lowered.startswith(prefix.lower()):
                continue
            value = command[len(prefix):].strip()
            if value.startswith(("=", "：", ":")):
                value = value[1:].strip()
            if not value:
                self.log_agent("请在添加命令后输入文件或文件夹路径。")
                return True
            paths = self.parse_cli_paths(value)
            self.add_paths(paths, target=target)
            self.log_agent(f"已尝试添加 {len(paths)} 个{self.video_target_label(target)}路径。")
            return True
        return False

    def parse_cli_paths(self, raw_value):
        try:
            parts = shlex.split(raw_value)
        except ValueError:
            parts = [raw_value]
        if not parts:
            return []
        return [str(Path(part).expanduser()) for part in parts]

    def interpret_natural_language(self, command):
        try:
            self.apply_api_settings(require_key=True)
        except Exception:
            self.log_agent("未识别命令。输入“帮助”查看示例；配置 API Key 后可使用自然语言控制。")
            return

        state = {
            "reference_video_count": len(self.reference_video_queue),
            "material_video_count": len(self.material_video_queue),
            "product_name": self.product_entry.get().strip(),
            "selling_points": self.points_entry.get().strip(),
            "requirements": self.get_text(self.requirements_text),
            "whisper_model": self.model_var.get(),
            "ai_model": self.api_model_entry.get().strip(),
            "vision_model": self.vision_model,
            "enable_titles": self.enable_titles_var.get(),
            "save_transcript": self.save_transcript_var.get(),
        }
        threading.Thread(target=self.interpret_natural_language_worker, args=(command, state), daemon=True).start()

    def interpret_natural_language_worker(self, command, state):
        try:
            self.log_agent("正在解析自然语言指令...")
            memory = app_memory.memory_context()
            prompt = f"""你是一个软件控制 agent。请把用户自然语言转换成 JSON 动作计划。

只能输出 JSON，不要输出解释。

可用 action:
- set_product_name value:string
- set_selling_points value:string
- set_requirements value:string
- set_whisper_model value: tiny|base|small|medium|large-v3
- set_ai_model value:string
- set_vision_model value:string
- set_output_dir value:string
- toggle_titles value:boolean
- toggle_save_transcript value:boolean
- add_path value:string 默认添加到参考视频列表，兼容旧命令
- add_reference_path value:string
- add_material_path value:string
- remember value:string
- extract_frames value:string
- analyze_material value:string
- transcribe_reference
- analyze_storyboard
- tag_materials
- generate_voiceovers
- build_assembly_plan
- save_project value:string 可选
- load_project value:string
- start_generation
- clear_videos 清空参考视频和素材视频列表
- clear_reference_videos
- clear_material_videos
- clear_memory

输出格式:
{{
  "summary": "一句话说明计划",
  "requires_confirmation": true,
  "actions": [
    {{"action": "set_product_name", "value": "示例"}}
  ]
}}

需要确认的动作: start_generation, clear_videos, clear_reference_videos, clear_material_videos, clear_memory, analyze_material, tag_materials, generate_voiceovers, build_assembly_plan。
如果无法转换，输出 actions 为空。

当前软件状态:
{json.dumps(state, ensure_ascii=False)}

本地记忆:
{memory or "无"}

用户指令:
{command}
"""
            raw = OpenAICompatibleClient().chat(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1200,
            )
            plan = self.parse_json_plan(raw)
            actions = plan.get("actions", [])
            if not actions:
                self.log_agent("没有解析出可执行动作。")
                return

            risky = {
                "start_generation",
                "clear_videos",
                "clear_reference_videos",
                "clear_material_videos",
                "clear_memory",
                "analyze_material",
                "tag_materials",
                "generate_voiceovers",
                "build_assembly_plan",
            }
            requires_confirmation = bool(plan.get("requires_confirmation")) or any(
                action.get("action") in risky for action in actions
            )
            self.queue_agent_actions(
                actions,
                plan.get("summary") or "自然语言指令计划",
                require_confirmation=requires_confirmation,
            )
        except Exception as e:
            self.log_agent(f"[解析失败] {e}")

    def parse_json_plan(self, raw):
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= start:
            text = text[start:end + 1]
        return json.loads(text)

    def ask_agent_worker(self, question):
        try:
            self.log_agent("AI 正在思考...")
            memory = app_memory.memory_context()
            prompt = f"""你是短视频文案工具里的控制台助手。请用简洁中文回答用户问题。

你可以参考以下本地记忆。如果记忆为空，可以忽略。
{memory or "无"}

用户问题:
{question}"""
            answer = AIRewriter().rewrite_with_prompt(prompt)
            self.log_agent(f"AI: {answer.strip()}")
        except Exception as e:
            self.log_agent(f"[AI错误] {e}")

    def extract_frames_worker(self, raw_path, output_dir):
        try:
            paths = self.parse_cli_paths(raw_path)
            if not paths:
                self.log_agent("请提供视频路径。")
                return
            analyzer = MaterialAnalyzer()
            for path in paths:
                self.log_agent(f"正在抽帧: {path}")
                frames = analyzer.extract_only(path, output_dir)
                self.log_agent(f"已抽取 {len(frames)} 帧:")
                for frame in frames:
                    self.log_agent(f"  {frame}")
        except Exception as e:
            self.log_agent(f"[抽帧错误] {e}")

    def analyze_material_worker(self, raw_path, output_dir, vision_model):
        try:
            paths = self.parse_cli_paths(raw_path)
            if not paths:
                self.log_agent("请提供视频路径。")
                return
            analyzer = MaterialAnalyzer()
            for path in paths:
                self.log_agent(f"正在抽帧并分析素材: {path}")
                result = analyzer.analyze_video_material(
                    path,
                    output_dir,
                    vision_model=vision_model,
                )
                analysis_path = os.path.join(output_dir, f"material_analysis_{self.safe_name(path)}.json")
                analyzer.save_analysis(result, analysis_path)
                self.log_agent(f"素材分析结果已保存: {analysis_path}")
                self.log_agent(result["analysis"])
        except Exception as e:
            self.log_agent(f"[素材分析错误] {e}")

    def safe_name(self, path):
        name = Path(path).stem
        return "".join(ch if ch not in '/\\:*?"<>|' else "_" for ch in name)

    def start_generation(self):
        if not self.reference_video_queue:
            messagebox.showwarning("提示", "请先添加参考视频")
            return
        if not self.product_entry.get().strip():
            messagebox.showwarning("提示", "请输入产品名称")
            return
        try:
            self.apply_api_settings(require_key=True)
        except Exception as e:
            messagebox.showwarning("提示", str(e))
            return

        task = {
            "videos": list(self.reference_video_queue),
            "output_dir": self.refresh_output_dir(),
            "model_size": self.model_var.get(),
            "product_name": self.product_entry.get().strip(),
            "selling_points": self.points_entry.get().strip(),
            "requirements": self.get_text(self.requirements_text),
            "enable_titles": self.enable_titles_var.get(),
            "save_transcript": self.save_transcript_var.get(),
        }

        app_memory.update_last_task(
            {
                "reference_video_count": len(self.reference_video_queue),
                "material_video_count": len(self.material_video_queue),
                "output_dir": task["output_dir"],
                "model_size": task["model_size"],
                "product_name": task["product_name"],
                "selling_points": task["selling_points"],
                "requirements": task["requirements"],
                "enable_titles": task["enable_titles"],
                "save_transcript": task["save_transcript"],
            }
        )

        self.is_running = True
        self.set_running_state(True)
        self.set_progress(0)
        self.set_text(self.transcript_output, "")
        self.set_text(self.rewrite_output, "")
        self.set_text(self.title_output, "")
        threading.Thread(target=self.generation_worker, args=(task,), daemon=True).start()

    def stop_generation(self):
        self.is_running = False
        self.log("正在停止任务...")

    def generation_worker(self, task):
        videos = task["videos"]
        total = len(videos)
        self.log(f"开始处理，共 {total} 个视频")

        for index, video_path in enumerate(videos, 1):
            if not self.is_running:
                break

            video_name = self.safe_name(video_path)
            self.set_status(f"处理第 {index}/{total} 个视频")
            self.log(f"\n[{index}/{total}] {os.path.basename(video_path)}")

            try:
                transcript_text = self.transcribe_video_with_subprocess(
                    video_path,
                    output_dir=task["output_dir"],
                    model_size=task["model_size"],
                )

                self.append_text(self.transcript_output, f"\n===== {video_name} =====\n{transcript_text}\n")
                if task["save_transcript"]:
                    transcript_path = os.path.join(task["output_dir"], f"原文稿_{video_name}.txt")
                    with open(transcript_path, "w", encoding="utf-8") as f:
                        f.write(transcript_text)
                    self.log(f"原文已保存: {transcript_path}")

                prompt = self.build_rewrite_prompt(
                    transcript_text,
                    task["product_name"],
                    task["selling_points"],
                    task["requirements"],
                )
                rewrite_text = AIRewriter().rewrite_with_prompt(prompt)
                self.append_text(self.rewrite_output, f"\n===== {video_name} =====\n{rewrite_text}\n")

                rewrite_path = os.path.join(task["output_dir"], f"修改文稿_{video_name}.txt")
                with open(rewrite_path, "w", encoding="utf-8") as f:
                    f.write(rewrite_text)
                self.log(f"改写文案已保存: {rewrite_path}")

                if task["enable_titles"]:
                    titles = TitleGenerator().generate_titles_from_transcript(transcript_text, count=5)
                    title_text = "\n".join(titles)
                    self.append_text(self.title_output, f"\n===== {video_name} =====\n{title_text}\n")
                    titles_path = os.path.join(task["output_dir"], f"titles_{video_name}.txt")
                    with open(titles_path, "w", encoding="utf-8") as f:
                        f.write(title_text)
                    self.log(f"标题已保存: {titles_path}")

                self.log(f"完成: {video_name}")
            except Exception as e:
                self.log(f"[错误] {e}")

            self.set_progress(index / total * 100)

        self.is_running = False
        self.set_running_state(False)
        self.set_status("完成")
        self.log("\n任务结束")

    def build_rewrite_prompt(self, transcript_text, product_name, selling_points, requirements):
        return f"""以下是一个视频内容转写文本：
{transcript_text}

请围绕以下产品信息改写为短视频口播文案：
产品名称: {product_name}
产品卖点: {selling_points}

要求：
{requirements}

请直接输出改写后的文案，不需要其他说明。"""


def main():
    global DND_FILES, TkinterDnD
    if TkinterDnD:
        try:
            root = TkinterDnD.Tk()
        except Exception:
            DND_FILES = None
            TkinterDnD = None
            root = tk.Tk()
    else:
        root = tk.Tk()
    VideoScriptGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
