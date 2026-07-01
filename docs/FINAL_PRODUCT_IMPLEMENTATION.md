# 最终产品落实方案

目标形态不是一个“文案工具合集”，而是一条短视频生产流水线：

1. 参考文案转写
2. 文案分镜分析
3. 视频素材打标分类
4. 文案转音频
5. 视频音频按标签合成

## 1. 产品工作流

### 参考文案转写

输入可以来自：

- 拖入视频后提取音频并转写
- 粘贴已有参考文案
- 导入 `.txt` 文本

当前项目已经具备本地 faster-whisper 转写能力，短期继续保留本地 ASR，避免每次转写都消耗云端额度。后续如果要接 MiMo ASR，可在 `speech_to_text.py` 外新增一个 `asr_interfaces.py`，让 GUI 在“本地 Whisper / MiMo ASR”之间切换。

### 文案分镜分析

分镜分析把一整段参考文案拆成镜头级结构，每个镜头至少包含：

- `shot_id`
- `title`
- `script_text`
- `tts_text`
- `duration_seconds`
- `visual_tags`
- `material_requirements`
- `transition`
- `notes`

建议使用 `mimo-v2.5-pro` 完成文案理解、结构化拆分和改写。模型返回需要经过 fenced JSON 容错解析，因为真实测试中视觉模型会返回 ```json 代码块。

### 视频素材打标分类

素材打标不是只生成一句描述，而是把每个视频资产变成可检索素材卡：

- 文件路径、时长、分辨率
- 抽帧路径
- 素材类型：产品展示、人物口播、场景空镜、操作演示、包装物流、对比测评等
- 视觉标签：颜色、主体、动作、场景、情绪、镜头类型
- 可匹配的分镜需求

当前项目已经有 `multimodal_interfaces.py`，短期策略是：先抽帧，再用 `mimo-v2.5` 分析图片帧。真实 API 测试表明 `mimo-v2.5` 可处理图片输入，而 `mimo-v2.5-pro` 不适合当前图像输入。

### 文案转音频

TTS 独立成一个接口层，不和文案改写、分镜分析混在一起。每个分镜使用自己的 `tts_text` 合成一段音频，保存为：

```text
project_root/voiceovers/shot_001.mp3
project_root/voiceovers/shot_002.mp3
```

当前默认模型：

- `mimo-v2.5-tts`
- voice: `mimo_default`
- format: `mp3`

真实 API 测试已经确认该组合可以返回 mp3 音频。

### 视频音频按标签合成

合成阶段不要一开始就做复杂自动剪辑，先拆成两层：

1. `AssemblyPlanner` 只负责生成合成计划，决定哪个分镜用哪个素材、音频和时间段。
2. `FFmpegAssemblyRenderer` 负责把计划渲染成视频。

这样 agent 可以先解释、调整、确认合成计划，再真正执行 ffmpeg。

## 2. 模块拆分

建议新增和保留的核心模块：

```text
production_schema.py       # 统一数据结构
storyboard_analyzer.py     # 文案分镜分析
material_catalog.py        # 视频素材打标分类
tts_interfaces.py          # MiMo TTS 合成
media_assembler.py         # 标签匹配和合成计划
multimodal_interfaces.py   # 抽帧和视觉分析底层能力
ai_client.py               # OpenAI-compatible 文本/视觉客户端
app_memory.py              # agent 记忆
gui.py                     # 操作界面和 AI 控制台
```

## 3. GUI 形态

每个功能应该独立成一个操作界面，而不是堆在同一个页面里：

- `参考转写`：拖入视频/文本，生成参考文案
- `分镜分析`：展示镜头列表，可编辑每个镜头
- `素材库`：拖入素材文件夹，自动抽帧、打标、筛选
- `配音`：按分镜生成音频，可试听、重生成
- `合成`：展示素材匹配结果和最终合成计划
- `AI 控制台`：跨页面调度任务，读取记忆，解释计划，等待确认后执行高风险操作

AI 控制台的目标不是聊天装饰，而是一个能控制软件状态的 agent：

- 能读当前项目状态
- 能记住产品、风格、禁用表达、常用素材偏好
- 能发起抽帧、分镜、配音、合成计划
- 执行删除、覆盖、批量生成前必须要求确认

## 4. 多工作树协作规则

主线程负责：

- 拆任务
- 分配互不冲突的写入范围
- 实际调用 API 验证
- 集成和最终验收

第 1 层 worker 负责大板块，例如：

- 数据模型和分镜分析
- 素材目录和打标
- 合成计划和渲染接口
- GUI 页面拆分

第 2 层 worker 只能由第 1 层在必要时派生，用于更小的子任务，例如：

- 单独补 parser
- 单独补测试
- 单独验证 ffmpeg 命令

第 3 层是最大深度，只用于非常局部的验证任务。任何 worker 都不能继续派生第 4 层。

合并规则：

- 每个 worker 必须声明自己改了哪些文件
- 写入范围要尽量互斥
- 主线程只集成，不盲信
- 所有新增模块至少跑 `compileall`

## 5. 分阶段落地

### Phase 1：把流水线骨架跑通

- 完成统一 schema
- 完成参考文案到分镜 JSON
- 完成素材抽帧和视觉打标
- 完成 TTS 合成接口
- 完成标签匹配生成合成计划

这一阶段重点是“数据能从头流到尾”，不追求 GUI 完美。

### Phase 2：拆 GUI 操作界面

- 把当前单页改成 tabs 或左侧导航
- 每个功能页面有独立输入、执行按钮、结果区
- AI 控制台常驻右侧
- 所有页面共享同一个 `ProductionProject`

这一阶段重点是可操作、可查看、可手动调整。

### Phase 3：自动合成和质检

- 根据合成计划调用 ffmpeg
- 支持试听配音、预览分镜
- 支持重新匹配素材
- 支持导出项目 JSON 和最终视频

这一阶段重点是从半自动变成自动生产工具。

## 6. 当前模型配置

已验证的配置：

- 文本/分镜/agent：`mimo-v2.5-pro`
- 视觉/抽帧分析：`mimo-v2.5`
- TTS：`mimo-v2.5-tts`
- TTS voice：`mimo_default`

后续预留：

- 图像识别接口继续走 `OpenAICompatibleClient.analyze_images`
- 抽帧分析继续走 `MaterialAnalyzer`
- MiMo ASR 可新增 `asr_interfaces.py`
- 视频理解模型如果开放，优先替换“抽帧 + 图片理解”的实现，不改上层素材目录结构
