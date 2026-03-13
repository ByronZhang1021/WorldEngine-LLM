# WorldEngine-LLM 项目架构

> 面向 AI 助手的项目全貌文档。
> 最后更新：2026-03-04（临时角色系统、世界设定系统）

## 项目是什么

多角色世界模拟系统。通过 Telegram 与一个虚拟世界互动，世界中有多个角色（所有角色地位平等），有虚拟时间线，用户是世界中的一个角色（可通过 `/play` 命令切换扮演的角色）。

完全独立的 Python 服务（World Engine）驱动，在 Windows 本机运行。

## 核心理念

- **所有角色平等**：无主角，所有角色使用同一套系统
- **活着的世界**：用户每次发消息时，整个世界同步推进（触发式，非持续运行）
- **场景驱动**：同一地点 = 在一起 = 可面对面交互
- **完全自主**：一个 Python 包搞定一切

## 技术栈

| 层面 | 技术 | 说明 |
|------|------|------|
| 语言 | **Python 3.12+** | 全部代码使用 Python，原生 asyncio 驱动异步逻辑 |
| Bot 框架 | **python-telegram-bot ≥21.0** | Telegram Bot API 的 Python 封装，支持异步 polling、命令菜单注册、消息分段发送。本项目采用双 Bot 架构（聊天 + 管理），各自独立 polling |
| LLM | **302.ai API**（OpenAI 兼容接口） | 统一的大模型网关，支持流式 SSE 和非流式 JSON 两种调用方式。当前使用 `grok-4-1-fast-reasoning`（对话生成）和 `grok-4-1-fast-non-reasoning`（系统分析），配置可切换 |
| Embedding | **云端 API / 本地模型** | 支持 302.ai Embedding API（Qwen3-Embedding-8B）和本地 sentence-transformers 模型（Qwen3-Embedding-0.6B），可在 Dashboard 切换 |
| Rerank | **云端 API / 本地模型 / 关闭** | 支持 302.ai Rerank API、本地 CrossEncoder 模型和关闭三种模式，可在 Dashboard 切换 |
| Web 后端 | **FastAPI ≥0.100.0** | 高性能 Python Web 框架，用于 Dashboard REST API，支持自动文档生成、请求验证 |
| Web 服务器 | **Uvicorn ≥0.20.0** | ASGI 服务器，运行 FastAPI 应用，在 daemon 线程中后台启动 |
| Web 前端 | **纯 HTML/CSS/JS** | Dashboard 前端，无框架依赖。模块化 JS（app/characters/map/sessions/events/archives/settings）|
| HTTP 客户端 | **requests ≥2.28.0** + **urllib** | `requests` 用于 Dashboard 代理 API 请求（如模型列表），`urllib` 用于 LLM/Embedding 直连调用 |
| 数据存储 | **ChromaDB** + **JSON 文件系统** | 角色记忆存储在 ChromaDB 向量数据库中，世界状态/地点/事件等结构化数据以 JSON 文件存储 |
| 图片生成 | **302.ai z-image-turbo** | 文生图 API，LLM 先将中文描述转英文 prompt + 宽高比，再调用生图 |

| 联网搜索 | **302.ai 搜索 API** | 实时联网搜索，为对话提供外部信息 |
| 中文分词 | **jieba** | 中文关键词提取，用于记忆检索的关键词匹配分支（hybrid search 中的关键词权重部分） |
| 费用追踪 | **TurnLogger 内建** | 基于 `config.json` 中 `model_pricing` 的费用单价，每轮自动计算 LLM/Embedding/Rerank/图片/语音费用 |

## 依赖

```
# requirements.txt
python-telegram-bot>=21.0
fastapi>=0.100.0
uvicorn>=0.20.0
requests>=2.28.0
jieba>=0.42.0
chromadb>=0.5.0
sentence-transformers>=2.2.0
```

安装：`pip install -r requirements.txt`

## 启动方式

```bash
# 方式一：直接启动（聊天Bot + 管理Bot + Dashboard）
cd WorldEngine-LLM
python -m world_engine

# 方式二：Windows 双击启动
start.bat

# 方式三：单独启动 Dashboard
python -m world_engine.dashboard
# 默认端口 8080
```

`__main__.py` → 后台 daemon 线程启动 Dashboard → 延迟 2 秒自动打开浏览器 → 创建双 Bot → 并行 polling → 注册命令菜单。

## 代码结构

```
WorldEngine-LLM/                     ← 项目根目录
├── requirements.txt                 ← Python 依赖
├── start.bat                        ← Windows 快捷启动脚本
├── docs/
│   ├── ARCHITECTURE.md              ← 本文档
│   └── 302ai/                       ← 302.ai 平台 API 参考文档
│       ├── README.md
│       ├── llms-cn.txt              ← 中文版 API 文档
│       └── llms-en.txt              ← 英文版 API 文档
├── data/                            ← 见「数据目录」章节（纯游戏数据：配置 + 状态 + 存档）
├── media/                           ← 生成的媒体文件（按世界名分目录，见下方）
├── logs/                            ← 运行日志（见「日志系统」章节）
└── world_engine/                    ← Python 包（18 个源文件）
├── __init__.py
├── __main__.py                  ← 入口（同时启动双Bot + Dashboard + 自动开浏览器）
├── bot.py                       ← Telegram 双Bot（聊天 + 管理），消息分段发送 + 打字延迟
├── world.py                     ← 世界管理（虚拟时间、活动链生成、世界模拟、群组移动）
├── scene.py                     ← 场景管理（DM 系统、消息路由、多角色调度、电话、动作可见性、临时角色、NPC session 管理）
├── character.py                 ← 角色引擎（system prompt 构建、智能记忆检索注入、回复生成）
├── session.py                   ← 对话管理（Session/SessionManager、消息级可见性过滤、压缩、归档）
├── memory.py                    ← 记忆存储（CRUD、TTL 过期清理、LLM 驱动合并，基于 ChromaDB）
├── memory_pipeline.py           ← 记忆分析管道（对话后异步分析 → 记忆操作 + 情绪更新 + 活动变更）
├── events.py                    ← 预定事件系统（CRUD + 活动链注入 + 爽约检测 + 过期清理）
├── memory_retrieval.py          ← 记忆智能检索（hybrid search: ChromaDB向量检索 + 关键词 + rerank，分层检索）
├── embedding.py                 ← 嵌入向量（云端API/本地模型双引擎 + 余弦相似度 + 费用记录）
├── chroma_store.py              ← ChromaDB 存储层（CRUD、语义检索、存档管理、迁移工具）
├── llm.py                       ← LLM 调用（流式/非流式/JSON，含 SSE 解析 + 推理链清洗 + 自动重试）
├── location.py                  ← 地点管理（Location/SubLocation、坐标距离、已知地点、子地点发现）
├── tools.py                     ← 外部工具（图片生成、联网搜索，全部异步包装）
├── dashboard.py                 ← Dashboard 后端（FastAPI REST API + 存档管理）
├── dashboard.html               ← Dashboard 前端主页面
├── utils.py                     ← 工具函数（路径常量、日志、文件读写、状态管理、角色数据解析、临时角色 CRUD、世界设定加载、TurnLogger）
├── static/
│   ├── dashboard.css            ← Dashboard 样式
│   └── js/
│       ├── app.js               ← 通用工具（API 封装、Tab 切换、通知）
│       ├── characters.js        ← 角色编辑（四个 section、子地点下拉联动）
│       ├── map.js               ← 2D 地图画布（拖拽图钉、子地点管理）
│       ├── sessions.js          ← 会话列表
│       ├── events.js            ← 事件查看 + 预定事件管理（CRUD 表单、状态分组）
│       ├── archives.js          ← 存档管理（保存/恢复/重命名/删除/浏览快照）
│       └── settings.js          ← 设置编辑（模型选择、参数调整、费用配置）
└── prompts/                     ← LLM prompt 模板（12 个）
    ├── rules.md                 ← 全局规则（角色行为准则、语言风格、禁止事项、DM 裁定规则）
    ├── pre_conversation.md      ← DM 系统（行为裁定 + 环境旁白 + 场景记忆便签 + 动作可见性 + 临时角色管理）
    ├── post_conversation.md    ← 对话后处理（记忆操作 + 情绪 + 活动变更 + 图片生成判断 + 预定事件）
    ├── memory_merge.md          ← 记忆合并（相似记忆去重合并）
    ├── time_advance.md          ← 时间推进（判断推进量 + 移动意图 + 群组移动意愿/能力）
    ├── activity_chain.md        ← 活动链生成（为 NPC 生成覆盖时间段的连续活动序列）
    ├── multi_responder.md       ← 多角色调度（判断哪些角色回复、回复顺序）
    ├── offscreen_events.md      ← 离屏互动判断（判断同地角色是否会互动）
    ├── offscreen_scene.md       ← 离屏场景生成（生成 NPC 间的离屏互动描写 + 事件影响）
    ├── session_compress.md      ← 对话压缩（长对话摘要生成）
    ├── image_gen.md             ← 图片生成（中文描述 → 英文 prompt + 宽高比）
    └── tail_reminder.md         ← Tail Reminder（利用 U-shaped attention 末尾强化规则）
```

## 数据目录

`data/` 只包含纯游戏数据（配置 + 世界状态 + 存档），存档/恢复只需管 `data/`。运行时产物（媒体文件、日志）在项目根目录独立管理。

```
data/
├── config.json                  ← 全局配置
│                                   ├─ api: base_url, api_key
│                                   ├─ models: primary_story, secondary_story, analysis, analysis_reasoning, embedding(mode/model/local_model), image
│                                   ├─ telegram: bot_token, admin_bot_token, owner_id
│                                   ├─ typing_delay: 打字延迟参数
│                                   ├─ world: start_time, walk_speed, walking_speed_kmh, session_compress_threshold
│                                   ├─ memory_retrieval: rerank_mode, rerank_model, local_rerank_model, recall_top_k, final_top_k, 权重, threshold, min_score
│                                   └─ model_pricing: 各模型的费用单价（USD→CNY 汇率 + 分模型定价）
├── current/                     ← 当前世界数据
│   ├── state.json               ← 世界状态
│   │                               ├─ world_name: 世界名称
│   │                               ├─ current_time: 虚拟时间（ISO 格式）
│   │                               ├─ next_session_id: Session ID 计数器
│   │                               ├─ scheduled_events: [ 预定事件列表 ]
│   │                               ├─ dm_context: { 角色名: "DM 便签" }（DM 场景记忆）
│   │                               └─ characters: { 角色名: { location, sub_location, activity, until,
│   │                                    emotion, memory_add_count, known_locations: [...],
│   │                                    location_since: "ISO时间（玩家到达当前地点的时间）" } }
│   ├── locations.json           ← 地点数据（JSON 数组，含坐标、描述、可选子地点）
│   ├── lore.json                ← 世界设定（世界概述、时代背景、叙事基调、术语表）
│   ├── temp_characters.json     ← 临时角色数据（按角色名索引，含地点、描述、状态）
│   ├── chromadb/               ← ChromaDB 向量数据库（角色记忆存储）
│   ├── events/                  ← 事件记录（每个事件一个 .json 文件，ID 递增）
│   └── sessions/
│       ├── active/              ← 活跃 session
│       └── archive/             ← 已归档 session
└── saves/                       ← 存档快照（每个存档一个子目录）
```

## 媒体与日志目录

```
media/                               ← 生成的媒体文件（项目根目录，与 data/ 平级）
└── <world_name>/
    ├── image/                   ← 生成的图片
    └── voice/                   ← 生成的语音

logs/                                ← 运行日志（项目根目录，与 data/ 平级）
├── engine.log                   ← 引擎日志（按天轮转，保留 30 天）
├── YYYY-MM-DD.log               ← 历史日志
└── turns/                       ← Turn 日志（每轮一个 HTML，完整记录所有 prompt、token 用量和费用）
```

## 角色数据格式

角色记忆存储在 ChromaDB 向量数据库中，通过 metadata 区分角色和板块。每条记忆的数据结构：

```
ChromaDB Document:
  id: "{character}__{section}__{index}"
  document: "记忆内容文本"
  metadata:
    character: "角色名"
    section: "public_base" | "private_base" | "public_dynamic" | "private_dynamic"
    ttl: "永久" | "24h" | "30m"
    created: "2026-01-01T08:00"
    hit_count: 0
```

|  | 🔓 公开（他人可见） | 🔒 私密（仅自己可见） |
|--|---|---|
| **📌 固定**（用户手写） | `public_base` | `private_base` |
| **🔄 可变**（系统自动增删） | `public_dynamic` | `private_dynamic` |

TTL 支持：`永久`、`Nh`（N 小时）、`Nm`（N 分钟）——由 `parse_ttl()` 归一化。

## 地点数据格式

`locations.json` 是 JSON 数组，每个地点包含：

```json
{
  "name": "地点名",
  "x": 0.0,             // 坐标（单位：公里）
  "y": 0.0,
  "description": "地点描述文本",
  "sub_locations": [     // 可选，子地点列表
    { "name": "子地点名", "description": "描述", "is_default": true }
  ]
}
```

## 主要子系统

### 虚拟时间
精确到分钟。每轮对话后 LLM 自动判断推进量（`auto_advance_time`），也支持手动命令调整（`/time set` / `/time +N`）。推进后自动检查活动到期、触发世界模拟、清理过期记忆。大幅跳转（≥ `_LARGE_JUMP_MINUTES`，默认 60 分钟）走完整世界模拟（`simulate_time_period`），小幅跳转走轻量模拟（`check_activity_expiry` + `simulate_small_jump`）。

### 双 Bot
聊天 Bot 负责沉浸式角色对话（消息分段 + 打字延迟模拟），管理 Bot 负责所有控制命令（时间、位置、记忆、存档、事件、会话等），两者共享世界状态，互不干扰。启动时各自注册 Telegram 命令菜单。

**聊天 Bot 命令**：`/call`（打电话）、`/locations`（查看地点和距离）

**管理 Bot 命令**：`/time`、`/where`、`/activities`、`/play`、`/memo`、`/schedule`、`/events`、`/event`、`/sessions`、`/session`、`/saves`、`/save`、`/load`

> `/activities` 查看所有 NPC 的完整活动链；`/schedule` 查看预定事件（支持 `/schedule 角色名` 按角色筛选）。

### Session
每次角色互动创建一个 Session（由 `SessionManager` 单例管理），管理参与者的加入/离开、对话历史、LLM 格式转换、超长对话压缩（超过 30 条消息时生成摘要）。支持面对面（face-to-face）和电话（phone）两种类型，可并行存在。归档时自动从 `active/` 移到 `archive/` 并更新 `state.json`。

消息支持 **可见性过滤**：每条消息可附带 `visible_to`（能看到完整内容的角色列表）和 `redacted_text`（脱敏版本）。`get_history_for(npc_name)` 构建对话历史时自动过滤：不在 `visible_to` 中的角色只看到 `redacted_text`，`redacted_text` 为空则完全跳过该消息。

### DM 系统（Dungeon Master）
绝对中立的第三方裁判和叙述者，在每轮 NPC 回复之前运行（`pre_conversation`），使用 `chat_creative` 模型配置。五大职责：

- **行为裁定**：玩家用()括号描述的动作不再是绝对事实，由 DM 判断实际结果。裁定遵循物理逻辑，合理即通过，不刻意为难。裁定结果以 `[DM]` 标记写入 session，NPC 以此为准。日常琐碎动作（叹气、坐下）不裁定。目前仅裁定玩家动作，NPC 动作暂不裁定
- **环境旁白**：描写场景中未定义角色（店员、路人）和环境氛围。不替已定义 NPC 说话
- **DM 便签**（`dm_context`）：DM 自主管理的短期记忆，存储在 `state.json` 顶层，按角色名索引。用于跨轮次记住临时角色、持续影响（受伤、衣物状态）、延迟发现的事实（如隐秘行为的后果）。DM 自行决定写入和清空时机。切换玩家角色时各自便签独立
- **动作可见性**（`private_targets`）：判断（）括号动作能否被在场角色感知，三级语义：
  - `null` — 公开动作（绝大多数情况），所有人可见
  - `["角色名"]` — 私密给特定角色（悄悄话、偷偷递纸条），仅列出的角色感知细节
  - `[]` — 隐秘行为（偷窃、趁人睡着做事），没有任何在场角色感知

  当 `private_targets` 非 null 时，裁定必须从旁观者视角写（不透露私密内容）。系统会回溯标记用户消息的 `visible_to`，并为非目标 NPC 生成脱敏版本（去掉括号内容）。记忆管道也将根据可见性为非目标 NPC 传入脱敏版 conv_text。
- **临时角色管理**（`temp_characters`）：管理没有角色文件的非玩家角色（店员、路人、配角等）的卡片信息。DM 通过 `temp_characters` 字段输出 add/update/remove 操作，系统自动维护 `temp_characters.json`。详见下方「临时角色系统」章节

展示标识：🎲 = DM 裁定，📖 = 环境旁白

### 三级模型配置

| 配置名 | 用途 | 温度 | 使用场景 |
|--------|------|------|----------|
| `primary_story` | 主力剧情 | 0.65 | NPC 角色回复（`chat_stream`） |
| `secondary_story` | 辅助剧情 | 0.65 | DM、离屏场景等（`chat_json` + `config_key`） |
| `analysis` | 指令分析 | 0.1 | 记忆、时间推进、调度等（`chat_json` 默认） |
| `analysis_reasoning` | 强推理分析 | 0.1 | 记忆合并等需要深度推理的任务 |

### 场景与消息路由
根据角色所在地点判断谁能互动。支持 @定向对话、多角色轮流回复（LLM 决定顺序）、电话呼叫（跨地点）、用户移动检测（LLM 从对话中识别移动意图）、群组移动（多角色一起移动到新地点）。NPC session 的生命周期管理：角色离开地点自动移除参与者，人数不足自动关闭 session。

### 子地点系统
每个地点可定义子地点（sub_locations），支持默认子地点标记。角色有独立的 `known_sub_locations` 列表，默认子地点始终可见。到达新子地点时自动添加到已知列表（`discover_sub_location`）。Dashboard 的地图编辑支持子地点 CRUD，角色编辑页支持子地点下拉联动。

### 已知地点系统
每个角色有独立的 `known_locations` 列表（存储在 `state.json`）。角色只能前往自己已知的地点。到达新地点时自动添加到已知列表（`discover_location`）。活动链生成和距离列表都会按角色的已知地点过滤。离屏场景的可用地点取参与角色已知地点的并集。向后兼容：无字段时回退为全部地点可知。

### 临时角色系统
管理没有角色文件的非玩家角色（店员、路人、受害者、配角等），通过独立卡片实现跨轮次一致性。

- **数据存储**：`data/current/temp_characters.json`，按角色名索引，每个条目包含 `location`、`sub_location`、`description`（外貌）、`state`（当前状态）、`updated`（更新时间）
- **DM 管理**：`pre_conversation.md` 第五节。DM 在旁白中引入有辨识度的临时角色时输出 `add`，状态变化时 `update`，无后续互动价值时 `remove`
- **代码实现**：`utils.py` 提供 `load_temp_characters()`、`save_temp_characters()`、`apply_temp_character_ops()`；`scene.py` 在构建 DM prompt 时注入当前地点的临时角色列表，处理 DM 返回的操作
- **去重规则**：add 前必须检查现有列表，避免同一人重复创建。已定义角色不需要作为临时角色管理
- **精简原则**：只保留有后续互动价值的角色，纯路人（一闪而过）不添加。外貌信息由卡片管理，不在 DM 便签中重复
- **存档包含**：存档保存/恢复自动包含 `temp_characters.json`

### 世界设定系统（Lore）
集中管理世界观设定，为所有 prompt 提供一致的世界背景信息。

- **数据存储**：`data/current/lore.json`，包含：
  - `world_premise`：世界概述（故事发生的背景和核心设定）
  - `era`：时代背景（科技水平、社会环境等）
  - `tone`：叙事基调（叙事风格和氛围定调）
  - `glossary`：术语表（世界中的关键概念/组织/物品，每个支持 `public`/`secret` 双层描述）
- **代码实现**：`utils.py` 提供 `load_lore()`（带内存缓存）、`reload_lore()`（存档切换后强制刷新）、`format_lore_for_prompt(include_secrets)`（格式化为可注入文本，`include_secrets` 控制是否包含秘密信息）
- **注入位置**：`scene.py` 将 lore 注入 DM 的 `pre_conversation` prompt；`world.py` 读取 lore 构建世界上下文用于活动链生成等
- **Dashboard 管理**：`GET/POST /api/lore` 用于查看和编辑世界设定
- **存档包含**：存档保存/恢复自动包含 `lore.json`

### 群组移动
任何角色都可以带其他角色一起移动（玩家带 NPC、NPC 带玩家、NPC 带 NPC）。对话中的群组移动由 `auto_advance_time` 检测（输出 leader + companions），目的地基于领头人的已知地点，LLM 同时判断每个角色的意愿和能力（主动同意/拒绝/被动移动/无法移动）。离屏模拟中的群组移动通过 `offscreen_scene.md` 的 `group_movement` 字段输出（leader + companions + destination），由 `_validate_group_movement` 验证后传递给活动链重新生成（`override_location` = 目的地）。

### 记忆系统
- **存储**：角色记忆存储在 ChromaDB 向量数据库中，按 `character` + `section` 的 metadata 区分。支持 add/update/forget，带 TTL 过期（`Nh`/`Nm`/`永久`）和 LLM 驱动合并（`memory_add_count` 达到阈值自动触发）
- **分析**：每轮对话后异步触发记忆管道（`memory_pipeline.py`），LLM 分析对话内容决定记忆操作（add/update/forget）、情绪更新、活动变更标记（`activity_changed`）
- **检索**：对话生成时使用分层检索机制：
  - 记忆总数 ≤ threshold → 全量注入
  - 记忆总数 > threshold → Tier 1（最近 24 世界小时强制纳入）→ ChromaDB 向量检索 + 关键词混合召回 → rerank 精排 → 按 `final_top_k` 截断
- **Embedding 引擎**：支持云端 API（302.ai）和本地模型（sentence-transformers）两种模式，可在 Dashboard 切换
- **Rerank 引擎**：支持云端 API、本地 CrossEncoder 模型和关闭三种模式

### 世界模拟

#### 公共函数
大幅跳转和小幅跳转共用以下提取的公共函数，避免代码重复：
- `_process_phone_call()`：电话场景处理（互动判断 → 场景生成 → 事件记录 → 记忆分析 → 事件影响）
- `_generate_overlap_scene()`：时空重叠场景生成（位置验证 → 场景生成 → 事件记录 → 记忆分析 → 群组移动处理 → 事件影响）
- `_process_npc_interaction()`：NPC 主动互动处理（互动判断 → 场景生成 → 事件记录 → 记忆分析 → 群组移动处理 → 事件影响）
- `_find_chain_overlaps()`：纯算法检测同时同地角色（过滤 < 5 分钟碎片、合并连续重叠）
- `_validate_group_movement()`：离屏群组移动验证（检查目的地存在性和领头人已知地点）

#### 大幅跳转（`simulate_time_period`）
时间推进 ≥ `_LARGE_JUMP_MINUTES` 时触发：
1. **并行生成活动链**：为每个 NPC 独立生成覆盖整个时间段的活动序列
2. **处理特殊活动**：interact/seek/phone_call（调用 `_process_npc_interaction` + `_process_phone_call`），带 `busy_intervals` 防冲突
3. **算法检测时空重叠**：`_find_chain_overlaps` 找出同时同地角色组
4. **并行互动判断 + 顺序场景生成**：互动检查无副作用可并行；场景生成调用 `_generate_overlap_scene`，顺序执行（每步检查 busy + 位置验证）
5. **事件影响**：离屏场景输出 `activity_impact`，为受影响角色重新生成后续活动链
6. **记忆统一处理**：所有离屏场景通过 `post_conversation` prompt 分析

#### 小幅跳转（`check_activity_expiry` + `simulate_small_jump`）
时间推进 < `_LARGE_JUMP_MINUTES` 时触发：
1. **`check_activity_expiry()`**：遍历所有 NPC，活动到期的重新生成活动链（2 小时），保存到 state
2. **`simulate_small_jump()`**：**复用 state 中已有的活动链**（不额外调 LLM 生成），裁剪到 `[old_time, new_time]` 窗口后执行：
   - 处理特殊活动（interact/seek/phone_call）
   - 时空重叠检测（`_find_chain_overlaps`）
   - 互动判断 + 场景生成（调用 `_generate_overlap_scene`）
   - **不写回 `activity_chain`**（避免用裁剪后的片段链覆盖完整链）

#### 三层防冲突机制
| 机制 | 作用 | 适用范围 |
|------|------|----------|
| `seen_pairs` | 同一对角色同一地点只处理一次 | 大幅 + 小幅 |
| `busy_intervals` | 同一角色在同一时段不卷入多个场景 | 大幅 + 小幅 |
| `overlap_cooldown` | 同一对角色在游戏时间 N 分钟内只判断一次 | **仅小幅**（大幅每次重新生成链，无需冷却） |
| **预定事件跳过** | 有共同预定事件的角色跳过冷却 + 互动判断 | 大幅 + 小幅 |

冷却记录存储在 `state.json` 的 `overlap_cooldown` 字段中，冷却时间等于 `_LARGE_JUMP_MINUTES`（默认 60 分钟）。

### 预定事件系统

管理角色之间的约定、计划和日程（`events.py`）。事件存储在 `state.json` 的 `scheduled_events` 数组中。

#### 事件数据格式

```json
{
  "id": "evt_abc123",
  "time": "2026-01-08T19:00:00",
  "participants": ["角色A", "角色B"],
  "location": "地点名",
  "description": "约定内容",
  "created_by": "角色A",
  "flexible_window": 30,
  "status": "pending"
}
```

- `flexible_window`：**愿意等待时间**（分钟），默认 30。过期判定 = 事件时间 + 等待时间 < 当前时间
- `status`：`pending` / `completed`（双方到场）/ `missed`（有人缺席）
- **玩家到场判断**：利用 `location_since`（玩家到达当前地点的虚拟时间）判断玩家是否在事件时间窗口内就已在场，而不是只看当前位置
- `skipped_by`：NPC 主动放弃时记录 `[{"character": "xxx", "reason": "..."}]`

#### 事件生命周期

```
1. 检测约定
   对话（post_conversation.md）或离屏场景（offscreen_scene.md）
   → LLM 在 events 字段输出 add/update/delete
   → process_event_operations() 写入 state.json

2. 注入活动链
   generate_activity_chain() → format_events_for_prompt(角色, 起止时间)
   → 注入事件信息（ID、参与者、地点）
   → LLM 自主决定赴约（考虑意愿/能力/路程时间）
   → 放弃 → skipped_events → mark_skipped()

3. 重叠检测跳过
   _has_scheduled_event() → 跳过冷却 + 互动判断 → 直接生成场景

4. 过期清理
   大跳转：在 simulate_time_period 内、活动链写入 state 之前调用
           cleanup_expired(new_time, chains=chains)，此时活动链还在内存中
   小跳转：在 advance_time 末尾调用 cleanup_expired(current_time)
   全部到场 → completed
   有人到场有人缺席 → missed + 到场者获得事实记忆 + 活动链截断重生
   全部缺席 → missed
```

#### 取消/修改通知

当一方取消或修改预定事件时（`process_event_operations` 处理 delete/update），自动给其他参与者写入通知记忆并强制过期其活动链。

#### 管理接口

- **Bot 命令**：`/schedule`（全部）、`/schedule 角色名`（某角色的预定）
- **Dashboard API**：`GET/POST/PUT/DELETE /api/scheduled-events`
- **Dashboard 前端**：事件标签页上半部分为预定事件管理（状态分组 + 内联编辑表单）

### Prompt 构建
为每个角色独立构建 system prompt（`character.py: build_system_prompt`）：
```
[System Prompt 结构]
├─ 全局规则（rules.md）
├─ 自身公开设定（public_base，全量）
├─ 自身私密设定（private_base，全量）
├─ 自身外在状态（public_dynamic，智能检索结果）
├─ 自身内心（private_dynamic，智能检索结果）
├─ 在场角色的公开信息（public_base + public_dynamic）
├─ 当前场景信息（虚拟时间、星期、位置、活动、情绪、地点描述）
├─ Session 类型提示（电话通话时追加）
└─ [Tail Reminder]（对话 ≥ 2 轮后追加，末尾强化规则）
```

### Dashboard
FastAPI Web 管理界面（`http://localhost:8080`），标签页包括：
- **会话**：查看活跃和已归档会话
- **事件**：预定事件管理（CRUD、状态分组 pending/completed/missed）+ 世界事件日志
- **角色**：编辑四个 section（entries 数组）、子地点下拉联动
- **地图**：2D 画布（拖拽图钉定位、子地点管理）
- **设置**：模型选择、参数调整、费用配置
- **存档**：保存/恢复/重命名/删除快照，浏览存档内容

### 外部工具
图片生成（LLM 转英文 prompt → 生图 API）、联网搜索。均为异步包装（`asyncio.to_thread`），不阻塞主流程。

### 日志系统

两套并行日志系统，职责明确分工：

#### `engine.log` — 完整事件流水账
以 Turn 为单位组织，记录"发生了什么"。每条日志自动附加 Turn 编号（`[T#0042]`），便于关联 Turn HTML 详情。
- **Turn 生命周期**：开始/结束分隔线
- **用户交互**：用户输入（含位置、在场角色）、角色回复摘要
- **多角色调度**：调度结果
- **时间系统**：时间推进、时间设置、自动推进
- **移动系统**：用户/NPC/群组移动
- **世界模拟**：活动链生成、离屏场景、电话场景、NPC 互动
- **记忆管道**：操作数和情绪摘要
- **预定事件**：添加/更新/删除/完成/过期/跳过
- **Session 事件**：创建/归档/压缩
- **LLM 统计**：每 Turn 的调用次数和 token 汇总
- **警告/错误**：所有失败和异常

按天轮转，保留 30 天。日志级别：DEBUG（校验细节）→ INFO（正常事件）→ WARNING（失败/异常）。

#### `turns/turn_NNNN.html` — 单轮调试视图
记录"怎么发生的"，用于调试和回溯：
- 所有 LLM 调用的完整 prompt 和回复（含 DM 对话前处理的完整 prompt、LLM 回复、可见性信息）
- 记忆检索详情（模式、条数、tier 分布）
- 动作可见性记录（private_targets、原文 vs 脱敏版本）
- LLM 调用费用明细（分模型、分操作类型）
- 可折叠的详细信息面板

自动清理，保留最近 200 个 turn 文件。

## 关键设计决策

1. **ChromaDB + JSON 混合存储**：角色记忆使用 ChromaDB 向量数据库（高效语义检索），世界状态/地点/事件等结构化数据使用 JSON 文件（便于存档/调试）
2. **线程安全**：`state.json` 用 `threading.RLock`（`_state_lock`，支持重入），角色文件用 `threading.Lock`（`_char_lock`）。提供 `state_transaction()` 上下文管理器实现读-改-写原子事务（整个 with 块持锁，退出时自动保存）
3. **异步后台处理**：记忆分析等耗时操作在 daemon 线程中执行，不阻塞用户交互
4. **智能检索 vs 全量注入**：记忆少时全量注入，记忆多时分层检索（Tier 1 + hybrid search + rerank）
5. **双 Bot 分离**：聊天沉浸感不被管理命令打断
6. **ChromaDB 存储**：角色记忆使用 ChromaDB 向量数据库存储，支持高效语义检索和元数据过滤
7. **Embedding/Rerank 引擎可切换**：支持云端 API 和本地模型两种模式，可在 Dashboard 设置中切换
8. **Turn 日志**：HTML 格式完整记录每轮所有 LLM 调用和费用，便于调试回溯
8. **事件影响分离**：离屏场景只判断"谁受影响"，活动重新规划交给专业 prompt
9. **双层去重**：`seen_pairs` 防同对重复 + `busy_intervals` 防同人时间冲突
10. **互动判断并行化**：无副作用的互动检查并行执行，场景生成顺序执行（因为有状态依赖）
11. **位置验证**：场景生成前验证角色是否仍在预期地点，防止 `activity_impact` 改链后的无效场景
12. **记忆统一管道**：对话和离屏场景都通过 `post_conversation` prompt 分析，保证记忆质量一致
13. **已知地点过滤**：角色只能前往已知地点，向后兼容（无字段时回退为全部地点），到达新地点自动发现
14. **群组移动验证**：目的地必须在领头人已知地点中，验证失败时整个群组移动取消
15. **discover_location 时序**：批量更新 state 后再统一调用 `discover_location`，避免 load/save 数据竞争
16. **SSE 推理链清洗**：LLM 返回的流式响应可能泄露推理链（`<think>` 标签等），由 `clean_reasoning_leak` 清洗
17. **Session 压缩**：超过 30 条消息时生成摘要，原始消息不动（确保 Dashboard 和文件完整可查）
18. **记忆合并自动触发**：`memory_add_count` 达到阈值后自动调用 LLM 合并相似记忆
19. **子地点**：支持地点内子地点划分，默认子地点始终可见，非默认子地点需发现后可知
20. **费用追踪**：每轮自动计算 LLM/Embedding/Rerank/图片/语音费用，无额外配置
21. **世界模拟公共函数**：电话处理、重叠场景生成、NPC 互动等逻辑提取为公共函数，大小幅跳转共用，减少代码重复
22. **预定事件自主判断**：NPC 不被强制赴约，LLM 根据意愿/能力/路程时间自主决定，放弃时输出 `skipped_events`
23. **爽约后果**：被放鸽子的 NPC 获得事实记忆（不硬编码情绪），活动链截断重生，后续由 LLM 自然处理
24. **预定事件跳过机制**：有共同预定事件的角色组跳过冷却和互动判断，确保约好的见面不被阻止
25. **取消通知**：事件取消/修改时自动给其他参与者写入记忆并过期活动链，轻量处理不生成场景
26. **DM 行为裁定**：玩家()动作由 DM 裁定结果，NPC 同时看到玩家原始意图和 DM 裁定，以 `[DM]` 为准。合理即通过，不刻意为难
27. **DM 便签独立于角色**：`dm_context` 存储在 `state.json` 顶层按角色名索引，不依赖 session，切换角色时各自独立保留
28. **三级模型分工**：主力剧情（reasoning，高温度）、辅助剧情（non-reasoning，高温度）、指令分析（non-reasoning，低温度），语义清晰，避免温度配置混乱
29. **动作可见性三级语义**：`private_targets: null`（公开）/ `["角色名"]`（私密给特定角色）/ `[]`（隐秘行为，无人感知）。影响 session 历史过滤和记忆管道 conv_text
30. **记忆管道可见性过滤**：传给非目标 NPC 的 conv_text 会去掉括号内容，防止创建“不应该知道”的记忆
31. **隐秘行为延迟发现**：通过 DM 便签持久化隐秘行为的事实，下次交互时 DM 自动叙述发现过程
32. **预定事件检测收紧**：必须同时满足明确时间 + 具体事项 + 双方达成共识，随口一说的意向不算
33. **玩家到场时间检测**：`move_user` 记录 `location_since`，预定事件检查玩家是否在事件窗口内就已在场，而不是只看当前位置快照
34. **预定事件到场检测时序**：`cleanup_expired` 在大跳转中必须在活动链被丢弃之前调用（`simulate_time_period` 的 Step 3.5），通过 `chains` 参数传入内存中的活动链，否则只能看到 NPC 的最终位置（大跳转结束后的位置）而误判为未到场
35. **临时角色卡片**：没有角色文件的 NPC（店员、路人等）通过 `temp_characters.json` 维护独立卡片，DM 自主管理 add/update/remove。外貌由卡片管理不在便签重复，保持精简只留有互动价值的角色
36. **世界设定集中管理**：`lore.json` 集中存储世界观（概述/时代/基调/术语表），通过 `format_lore_for_prompt` 按需注入各 prompt，术语表支持 `public`/`secret` 双层描述控制信息泄露
