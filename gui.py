import os
import json
import shlex
import sys
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
from audio_extractor import AudioExtractor
from multimodal_interfaces import MaterialAnalyzer
from speech_to_text import SpeechToText
from text_cleaner import TextCleaner
from title_generator import TitleGenerator


FONT_FAMILY = getattr(config, "UI_FONT_FAMILY", "Arial")
VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm")


class VideoScriptGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("短视频文案生成工具")
        self.root.geometry("1420x940")
        self.root.resizable(True, True)
        self.main_thread = threading.current_thread()

        self.video_queue = []
        self.is_running = False
        self.output_dir = config.OUTPUT_DIR
        self.vision_model = config.VISION_API_MODEL
        self.pending_agent_actions = None

        self.setup_styles()
        self.create_widgets()
        self.load_config()
        self.setup_drag_and_drop()

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Title.TLabel", font=(FONT_FAMILY, 16, "bold"), foreground="#2c3e50")
        style.configure("Info.TLabel", font=(FONT_FAMILY, 10), foreground="#34495e")
        style.configure("Primary.TButton", font=(FONT_FAMILY, 10), padding=8)
        style.configure("Secondary.TButton", font=(FONT_FAMILY, 9))

    def create_widgets(self):
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        main_frame = ttk.Frame(paned, padding="14")
        agent_frame = ttk.Frame(paned, padding=(0, 14, 14, 14))
        paned.add(main_frame, weight=4)
        paned.add(agent_frame, weight=1)

        ttk.Label(main_frame, text="视频生成文案", style="Title.TLabel").pack(anchor=tk.W, pady=(0, 10))

        self.create_common_settings(main_frame)
        self.create_task_settings(main_frame)
        self.create_result_area(main_frame)
        self.create_agent_panel(agent_frame)

    def create_agent_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="AI 控制台", padding="10")
        frame.pack(fill=tk.BOTH, expand=True)

        self.agent_output = scrolledtext.ScrolledText(frame, height=28, wrap=tk.WORD, font=(FONT_FAMILY, 10))
        self.agent_output.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        input_row = ttk.Frame(frame)
        input_row.pack(fill=tk.X)
        self.agent_entry = ttk.Entry(input_row, font=(FONT_FAMILY, 10))
        self.agent_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.agent_entry.bind("<Return>", lambda _event: self.handle_agent_command())
        ttk.Button(input_row, text="执行", style="Secondary.TButton", command=self.handle_agent_command).pack(side=tk.LEFT, padx=(6, 0))

        quick_row = ttk.Frame(frame)
        quick_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(quick_row, text="帮助", style="Secondary.TButton", command=lambda: self.execute_agent_command("帮助")).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(quick_row, text="状态", style="Secondary.TButton", command=lambda: self.execute_agent_command("状态")).pack(side=tk.LEFT, padx=5)
        ttk.Button(quick_row, text="开始", style="Secondary.TButton", command=lambda: self.execute_agent_command("开始生成")).pack(side=tk.LEFT, padx=5)
        ttk.Button(quick_row, text="确认", style="Secondary.TButton", command=lambda: self.execute_agent_command("确认")).pack(side=tk.LEFT, padx=5)

        self.log_agent("控制台已就绪。输入“帮助”查看可用命令。")

    def create_common_settings(self, parent):
        frame = ttk.LabelFrame(parent, text="通用设置", padding="10")
        frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(frame, text="输出文件夹:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.output_dir_var = tk.StringVar(value=self.output_dir)
        ttk.Entry(frame, textvariable=self.output_dir_var, width=58, font=(FONT_FAMILY, 10)).grid(row=0, column=1, sticky=tk.W, padx=8, pady=4)
        ttk.Button(frame, text="浏览...", style="Secondary.TButton", command=self.browse_output_dir).grid(row=0, column=2, sticky=tk.W, pady=4)

        ttk.Label(frame, text="AI API Key:").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.api_key_entry = ttk.Entry(frame, width=58, font=(FONT_FAMILY, 10), show="*")
        self.api_key_entry.grid(row=1, column=1, sticky=tk.W, padx=8, pady=4)
        ttk.Button(frame, text="保存配置", style="Secondary.TButton", command=self.save_config).grid(row=1, column=2, sticky=tk.W, pady=4)

        ttk.Label(frame, text="AI Base URL:").grid(row=2, column=0, sticky=tk.W, pady=4)
        self.api_base_url_entry = ttk.Entry(frame, width=58, font=(FONT_FAMILY, 10))
        self.api_base_url_entry.grid(row=2, column=1, sticky=tk.W, padx=8, pady=4)

        ttk.Label(frame, text="AI模型:").grid(row=3, column=0, sticky=tk.W, pady=4)
        self.api_model_entry = ttk.Entry(frame, width=36, font=(FONT_FAMILY, 10))
        self.api_model_entry.grid(row=3, column=1, sticky=tk.W, padx=8, pady=4)

        frame.columnconfigure(1, weight=1)

    def create_task_settings(self, parent):
        frame = ttk.LabelFrame(parent, text="生成任务", padding="10")
        frame.pack(fill=tk.X, pady=(0, 10))

        left = ttk.Frame(frame)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = ttk.Frame(frame)
        right.grid(row=0, column=1, sticky="nsew")
        frame.columnconfigure(0, weight=2)
        frame.columnconfigure(1, weight=3)

        self.drop_frame = tk.Frame(left, bg="#e8f4fc", height=96, highlightthickness=1, highlightbackground="#8ab4d6")
        self.drop_frame.pack(fill=tk.X, pady=(0, 8))
        self.drop_frame.pack_propagate(False)
        drop_text = "拖拽视频文件到这里"
        if not DND_FILES:
            drop_text = "拖拽依赖未安装，可先使用浏览按钮添加视频"
        self.drop_label = tk.Label(
            self.drop_frame,
            text=drop_text,
            bg="#e8f4fc",
            fg="#2563eb",
            font=(FONT_FAMILY, 12, "bold"),
        )
        self.drop_label.pack(expand=True)

        self.video_listbox = tk.Listbox(left, height=7, font=(FONT_FAMILY, 10))
        self.video_listbox.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        video_buttons = ttk.Frame(left)
        video_buttons.pack(fill=tk.X)
        ttk.Button(video_buttons, text="添加文件...", style="Secondary.TButton", command=self.add_videos).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(video_buttons, text="添加文件夹...", style="Secondary.TButton", command=self.add_folder).pack(side=tk.LEFT, padx=5)
        ttk.Button(video_buttons, text="删除选中", style="Secondary.TButton", command=self.remove_selected_video).pack(side=tk.LEFT, padx=5)
        ttk.Button(video_buttons, text="清空", style="Secondary.TButton", command=self.clear_videos).pack(side=tk.LEFT, padx=5)

        ttk.Label(right, text="产品名称:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.product_entry = ttk.Entry(right, width=34, font=(FONT_FAMILY, 10))
        self.product_entry.grid(row=0, column=1, sticky="ew", padx=8, pady=4)

        ttk.Label(right, text="产品卖点:").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.points_entry = ttk.Entry(right, width=48, font=(FONT_FAMILY, 10))
        self.points_entry.grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        self.points_entry.insert(0, "保湿、抗衰老、美白")

        ttk.Label(right, text="改写要求:").grid(row=2, column=0, sticky=tk.NW, pady=4)
        self.requirements_text = scrolledtext.ScrolledText(right, height=4, wrap=tk.WORD, font=(FONT_FAMILY, 10))
        self.requirements_text.grid(row=2, column=1, sticky="ew", padx=8, pady=4)
        self.requirements_text.insert(tk.END, "保留核心信息；更口语化；适合短视频口播；控制在300字以内")

        ttk.Label(right, text="Whisper模型:").grid(row=3, column=0, sticky=tk.W, pady=4)
        model_frame = ttk.Frame(right)
        model_frame.grid(row=3, column=1, sticky=tk.W, padx=8, pady=4)
        self.model_var = tk.StringVar(value="medium")
        for name in ["tiny", "base", "small", "medium", "large-v3"]:
            ttk.Radiobutton(model_frame, text=name, variable=self.model_var, value=name).pack(side=tk.LEFT, padx=5)

        options = ttk.Frame(right)
        options.grid(row=4, column=1, sticky=tk.W, padx=8, pady=(8, 4))
        self.enable_cleaning_var = tk.BooleanVar(value=True)
        self.enable_titles_var = tk.BooleanVar(value=True)
        self.save_transcript_var = tk.BooleanVar(value=True)
        self.save_clean_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text="文本清洗", variable=self.enable_cleaning_var).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(options, text="生成标题", variable=self.enable_titles_var).pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(options, text="保存原文", variable=self.save_transcript_var).pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(options, text="保存清洗文本", variable=self.save_clean_var).pack(side=tk.LEFT, padx=10)

        action_row = ttk.Frame(right)
        action_row.grid(row=5, column=1, sticky=tk.EW, padx=8, pady=(10, 0))
        self.start_button = ttk.Button(action_row, text="开始生成", style="Primary.TButton", command=self.start_generation)
        self.start_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(action_row, text="停止", style="Secondary.TButton", command=self.stop_generation, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=8)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(action_row, textvariable=self.status_var, style="Info.TLabel").pack(side=tk.LEFT, padx=12)

        right.columnconfigure(1, weight=1)

    def create_result_area(self, parent):
        self.progress = ttk.Progressbar(parent, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(0, 10))

        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True)

        log_tab = ttk.Frame(notebook, padding="8")
        transcript_tab = ttk.Frame(notebook, padding="8")
        rewrite_tab = ttk.Frame(notebook, padding="8")
        title_tab = ttk.Frame(notebook, padding="8")

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
        for widget in (self.drop_frame, self.drop_label, self.video_listbox):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self.handle_drop)

    def handle_drop(self, event):
        try:
            paths = self.root.tk.splitlist(event.data)
        except Exception:
            paths = event.data.split()
        self.add_paths(paths)

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
        return api_key, api_base_url, api_model

    def refresh_output_dir(self):
        self.output_dir = self.output_dir_var.get().strip() or self.output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        return self.output_dir

    def add_videos(self):
        filenames = filedialog.askopenfilename(
            title="选择视频文件",
            filetypes=[("视频文件", "*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm"), ("所有文件", "*.*")],
            multiple=True,
        )
        self.add_paths(filenames)

    def add_folder(self):
        dirname = filedialog.askdirectory(title="选择文件夹")
        if dirname:
            self.add_paths([dirname])

    def add_paths(self, paths):
        added = 0
        for raw_path in paths:
            path = os.path.abspath(str(raw_path))
            if os.path.isdir(path):
                for root, _, filenames in os.walk(path):
                    for filename in filenames:
                        if filename.lower().endswith(VIDEO_EXTENSIONS):
                            if self.add_video_path(os.path.join(root, filename)):
                                added += 1
            elif path.lower().endswith(VIDEO_EXTENSIONS):
                if self.add_video_path(path):
                    added += 1
        if added:
            self.log(f"已添加 {added} 个视频")
        elif paths:
            self.log("未找到可用视频文件")

    def add_video_path(self, path):
        if path in self.video_queue:
            return False
        self.video_queue.append(path)
        self.video_listbox.insert(tk.END, os.path.basename(path))
        return True

    def remove_selected_video(self):
        selection = self.video_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        self.video_listbox.delete(index)
        self.video_queue.pop(index)

    def clear_videos(self):
        self.video_listbox.delete(0, tk.END)
        self.video_queue.clear()

    def make_text_area(self, parent, height):
        text = scrolledtext.ScrolledText(parent, height=height, wrap=tk.WORD, font=(FONT_FAMILY, 10))
        text.pack(fill=tk.BOTH, expand=True)
        return text

    def get_text(self, text_widget):
        return text_widget.get("1.0", tk.END).strip()

    def set_text(self, text_widget, content):
        if threading.current_thread() is not self.main_thread:
            self.root.after(0, self.set_text, text_widget, content)
            return
        text_widget.delete("1.0", tk.END)
        text_widget.insert(tk.END, content)

    def append_text(self, text_widget, content):
        if threading.current_thread() is not self.main_thread:
            self.root.after(0, self.append_text, text_widget, content)
            return
        text_widget.insert(tk.END, content)
        text_widget.see(tk.END)

    def log(self, message):
        self.append_text(self.log_text, message + "\n")

    def set_status(self, message):
        if threading.current_thread() is not self.main_thread:
            self.root.after(0, self.set_status, message)
            return
        self.status_var.set(message)

    def set_running_state(self, running):
        if threading.current_thread() is not self.main_thread:
            self.root.after(0, self.set_running_state, running)
            return
        self.start_button.config(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_button.config(state=tk.NORMAL if running else tk.DISABLED)

    def set_progress(self, value):
        if threading.current_thread() is not self.main_thread:
            self.root.after(0, self.set_progress, value)
            return
        self.progress.config(value=value)

    def show_error(self, message):
        if threading.current_thread() is not self.main_thread:
            self.root.after(0, self.show_error, message)
            return
        messagebox.showerror("错误", message)

    def log_agent(self, message):
        if threading.current_thread() is not self.main_thread:
            self.root.after(0, self.log_agent, message)
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
                    "将开始处理当前视频列表。",
                    require_confirmation=True,
                )
            elif normalized in {"停止", "stop"}:
                self.stop_generation()
                self.log_agent("已请求停止任务。")
            elif normalized in {"清空", "清空视频", "clear"}:
                self.queue_agent_actions(
                    [{"action": "clear_videos"}],
                    "将清空当前视频列表。",
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
                    "  开启清洗 / 关闭清洗",
                    "  开启标题 / 关闭标题",
                    "  保存原文 / 不保存原文",
                    "  保存清洗文本 / 不保存清洗文本",
                    "  添加 /path/to/video.mp4",
                    "  添加文件夹 /path/to/folder",
                    "  清空视频",
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
            self.root.after(0, self.queue_agent_actions, actions, summary, require_confirmation)
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
            self.root.after(0, self.apply_agent_actions, actions)
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
            elif name == "toggle_cleaning":
                self.enable_cleaning_var.set(bool(value))
                self.log_agent(f"文本清洗: {'开' if value else '关'}")
            elif name == "toggle_titles":
                self.enable_titles_var.set(bool(value))
                self.log_agent(f"生成标题: {'开' if value else '关'}")
            elif name == "toggle_save_transcript":
                self.save_transcript_var.set(bool(value))
                self.log_agent(f"保存原文: {'开' if value else '关'}")
            elif name == "toggle_save_clean":
                self.save_clean_var.set(bool(value))
                self.log_agent(f"保存清洗文本: {'开' if value else '关'}")
            elif name == "add_path":
                self.add_paths([value])
            elif name == "clear_videos":
                self.clear_videos()
                self.log_agent("已清空视频列表。")
            elif name == "clear_memory":
                app_memory.clear_facts()
                self.log_agent("已清空长期记忆。")
            elif name == "start_generation":
                self.start_generation()
                self.log_agent("已请求开始生成。")
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
            f"视频数量: {len(self.video_queue)}",
            f"产品名称: {self.product_entry.get().strip() or '未设置'}",
            f"产品卖点: {self.points_entry.get().strip() or '未设置'}",
            f"Whisper模型: {self.model_var.get()}",
            f"AI模型: {self.api_model_entry.get().strip() or config.DEFAULT_API_MODEL}",
            f"视觉模型: {self.vision_model or config.VISION_API_MODEL}",
            f"输出目录: {self.output_dir_var.get().strip() or self.output_dir}",
            f"文本清洗: {'开' if self.enable_cleaning_var.get() else '关'}",
            f"生成标题: {'开' if self.enable_titles_var.get() else '关'}",
            f"保存原文: {'开' if self.save_transcript_var.get() else '关'}",
            f"保存清洗文本: {'开' if self.save_clean_var.get() else '关'}",
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
        if "enable_cleaning" in last_task:
            self.enable_cleaning_var.set(bool(last_task["enable_cleaning"]))
        if "enable_titles" in last_task:
            self.enable_titles_var.set(bool(last_task["enable_titles"]))
        if "save_transcript" in last_task:
            self.save_transcript_var.set(bool(last_task["save_transcript"]))
        if "save_clean" in last_task:
            self.save_clean_var.set(bool(last_task["save_clean"]))
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
            ("清洗文本", self.save_clean_var, "保存清洗文本"),
            ("原文", self.save_transcript_var, "保存原文"),
            ("标题", self.enable_titles_var, "生成标题"),
            ("清洗", self.enable_cleaning_var, "文本清洗"),
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
        prefixes = ["添加文件夹", "添加文件", "添加", "add folder", "add file", "add"]
        for prefix in prefixes:
            if not lowered.startswith(prefix.lower()):
                continue
            value = command[len(prefix):].strip()
            if value.startswith(("=", "：", ":")):
                value = value[1:].strip()
            if not value:
                self.log_agent("请在添加命令后输入文件或文件夹路径。")
                return True
            paths = self.parse_cli_paths(value)
            self.add_paths(paths)
            self.log_agent(f"已尝试添加 {len(paths)} 个路径。")
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
            "video_count": len(self.video_queue),
            "product_name": self.product_entry.get().strip(),
            "selling_points": self.points_entry.get().strip(),
            "requirements": self.get_text(self.requirements_text),
            "whisper_model": self.model_var.get(),
            "ai_model": self.api_model_entry.get().strip(),
            "vision_model": self.vision_model,
            "enable_cleaning": self.enable_cleaning_var.get(),
            "enable_titles": self.enable_titles_var.get(),
            "save_transcript": self.save_transcript_var.get(),
            "save_clean": self.save_clean_var.get(),
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
- toggle_cleaning value:boolean
- toggle_titles value:boolean
- toggle_save_transcript value:boolean
- toggle_save_clean value:boolean
- add_path value:string
- remember value:string
- extract_frames value:string
- analyze_material value:string
- start_generation
- clear_videos
- clear_memory

输出格式:
{{
  "summary": "一句话说明计划",
  "requires_confirmation": true,
  "actions": [
    {{"action": "set_product_name", "value": "示例"}}
  ]
}}

需要确认的动作: start_generation, clear_videos, clear_memory, analyze_material。
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

            risky = {"start_generation", "clear_videos", "clear_memory", "analyze_material"}
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
        if not self.video_queue:
            messagebox.showwarning("提示", "请先添加视频文件")
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
            "videos": list(self.video_queue),
            "output_dir": self.refresh_output_dir(),
            "model_size": self.model_var.get(),
            "product_name": self.product_entry.get().strip(),
            "selling_points": self.points_entry.get().strip(),
            "requirements": self.get_text(self.requirements_text),
            "enable_cleaning": self.enable_cleaning_var.get(),
            "enable_titles": self.enable_titles_var.get(),
            "save_transcript": self.save_transcript_var.get(),
            "save_clean": self.save_clean_var.get(),
        }

        app_memory.update_last_task(
            {
                "video_count": len(task["videos"]),
                "output_dir": task["output_dir"],
                "model_size": task["model_size"],
                "product_name": task["product_name"],
                "selling_points": task["selling_points"],
                "requirements": task["requirements"],
                "enable_cleaning": task["enable_cleaning"],
                "enable_titles": task["enable_titles"],
                "save_transcript": task["save_transcript"],
                "save_clean": task["save_clean"],
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
                audio_path = AudioExtractor(output_dir=task["output_dir"]).extract_audio(
                    video_path,
                    f"audio_{video_name}.wav",
                )
                transcript_text = SpeechToText(
                    model_size=task["model_size"],
                    output_dir=task["output_dir"],
                ).transcribe(audio_path, language="zh")

                self.append_text(self.transcript_output, f"\n===== {video_name} =====\n{transcript_text}\n")
                if task["save_transcript"]:
                    transcript_path = os.path.join(task["output_dir"], f"原文稿_{video_name}.txt")
                    with open(transcript_path, "w", encoding="utf-8") as f:
                        f.write(transcript_text)
                    self.log(f"原文已保存: {transcript_path}")

                work_text = transcript_text
                if task["enable_cleaning"]:
                    work_text = TextCleaner().clean_text(transcript_text)
                    if task["save_clean"]:
                        clean_path = os.path.join(task["output_dir"], f"clean_text_{video_name}.txt")
                        with open(clean_path, "w", encoding="utf-8") as f:
                            f.write(work_text)
                        self.log(f"清洗文本已保存: {clean_path}")

                prompt = self.build_rewrite_prompt(
                    work_text,
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
                    titles = TitleGenerator().generate_titles_from_transcript(work_text, count=5)
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
