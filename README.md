<div align="center">

# 🌍 WorldEngine-LLM

**LLM 驱动的多智能体自主世界模拟引擎**

*An LLM-powered autonomous multi-agent world simulation engine*

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Telegram Bot API](https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?logo=telegram)](https://core.telegram.org/bots/api)

</div>

---

## ✨ 什么是 WorldEngine？

WorldEngine 是一个 **LLM 驱动的触发式多角色世界模拟引擎**。你通过 Telegram 与一个虚拟世界互动——世界中有多个 AI 角色，他们拥有独立的记忆、情绪、日程和社交关系。你扮演其中一个角色（可随时切换），与其他角色对话、移动、打电话、参与事件。

这不是一个简单的聊天机器人。每当你发送一条消息，整个世界会同步推进：

- 🕐 虚拟时间根据对话内容自动流逝
- 🗺️ 不在场的 NPC 各自行动——去不同的地点、做不同的事
- 💬 碰巧在同一地点的 NPC 之间可能自发互动，产生新记忆
- 🎲 一个隐形的 DM（地下城主）裁定你的行为、描述环境

所有状态以 JSON 文件 + ChromaDB 向量数据库存储，配合 Web Dashboard 可视化管理世界。支持任意 OpenAI 兼容 API。

## 🎮 核心特性

### 🌐 触发式世界模拟
每次用户发消息时，系统不仅处理当前对话，还会一次性模拟所有不在场 NPC 在这段时间里的行为。

- **活动链生成**：LLM 为每个 NPC 生成覆盖时间段的连续活动序列
- **离屏场景**：当多个 NPC 碰巧在同一地点，自动判断是否互动并生成场景
- **三层防冲突**：防止同一角色同一时间被卷入多个场景

### 🗺️ 地图与移动系统
基于 2D 坐标的地点系统。地点之间的距离由坐标自动计算，角色移动需要消耗对应的虚拟时间。

- **坐标地图**：每个地点有 XY 坐标，系统自动计算步行时间
- **子地点**：地点内可划分子地点（如「商业街」→「咖啡店」「书店」）
- **自然语言移动**：对话中说"去某地"即可触发移动，AI 自动识别意图
- **群组移动**：角色可以带其他角色一起前往新地点
- **电话系统**：不在同一地点的角色可以通过 `/call` 打电话
- **已知地点**：角色只能前往自己已知的地点，到达新地点后自动发现

### ⏰ 虚拟时间系统
世界有独立的虚拟时间线，时间不会自动流逝。每次你发送消息时，AI 根据对话内容判断应该推进多少时间。

- **智能推进**：AI 根据对话语境自动判断推进量（如"聊了一会"→ 几分钟，"第二天"→ 跳到次日）
- **手动控制**：通过 `/time` 命令可以精确推进或跳转任意时间
- **活动链联动**：时间推进后自动检查 NPC 活动到期、触发世界模拟、清理过期记忆

### 🤖 DM（地下城主）系统
一个隐形的中立裁判和叙述者。在每轮 NPC 回复之前运行，负责维护世界的合理性和沉浸感。

- **行为裁定**：玩家用括号描述的动作由 DM 判断是否成功，合理即通过
- **旁白叙事**：描写环境氛围、路人反应等场景细节
- **临时角色**：自动创建和管理没有角色文件的 NPC（店员、路人等）
- **行为可见性**：控制哪些角色能感知到特定行为（公开/私密/隐秘三级）

### 📝 角色与世界设定
通过 Dashboard 管理所有角色设定和世界观。角色设定分为四个区域，世界观通过 Lore 系统集中管理。

- **角色设定**：公开设定（外貌、职业）、私密设定（内心秘密）、公开动态（外在状态变化）、私密动态（内心变化），设定区手写、动态区 AI 自动维护
- **可见性**：公开信息所有角色可见，私密信息仅自己可见，确保角色之间不会"读心"
- **世界观（Lore）**：集中管理世界概述、时代背景、叙事基调
- **术语表**：定义世界中的关键概念，支持公开/秘密双层描述，控制信息泄露

### 🧠 混合记忆检索系统
角色会自动记住对话中的重要信息（公开/私密），记忆数量多时通过混合检索挑选最相关的注入 LLM。

- **Tier 1 保护**：最近 24 世界小时的记忆始终纳入，确保近期上下文不丢失
- **Embedding + jieba**：向量相似度 + 中文关键词混合召回（支持云端 API 和本地模型切换）
- **Rerank 精排**：两阶段精排保证检索质量（支持云端/本地/关闭三种模式）
- **TTL 过期**：记忆自动过期，支持永久/小时/分钟级
- **LLM 合并**：自动合并相似记忆，减少冗余

### 📅 预定事件系统
角色在对话中做出的约定（如"明天下午见"）会被系统自动检测并记录。到约定时间时，NPC 自行判断是否赴约。

- **自动检测**：AI 从对话中识别约定，自动创建预定事件
- **智能去重**：时间窗口 + 主题双重去重
- **爽约检测**：检查参与者是否到场，为被爽约者生成记忆
- **变更通知**：取消/修改事件时自动通知其他参与者

### 🔍 TurnLogger 调试系统
每次用户交互生成精美的 HTML 调试页面，方便回溯每一步的决策过程和费用消耗。

- 完整的 LLM Prompt 和回复
- Token 用量和费用统计
- 记忆操作详情
- 世界模拟步骤

## 📦 快速开始

> 📖 **完整使用说明**：请查看 [使用指南](docs/USER_GUIDE.md)，包含从零创建世界的详细步骤。

### 前置条件
- Python 3.11+
- 一个 [302.ai](https://302.ai) API Key（支持 OpenAI 兼容格式的 API 也可修改使用）
- 两个 Telegram Bot Token（[BotFather](https://t.me/botfather) 创建）

### 安装

```bash
# 克隆仓库
git clone https://github.com/YOUR_USERNAME/WorldEngine-LLM.git
cd WorldEngine-LLM

# 安装依赖
pip install -r requirements.txt

# 复制配置模板并填入你的 key
cp data/config.example.json data/config.json
# 编辑 data/config.json，填入你的 API Key 和 Bot Token
```

### 创建世界

启动后打开 Dashboard（`http://localhost:8080`）：

1. **存档** — 进入存档管理，新建一个空白世界
2. **地图** — 添加地点、拖拽调整位置、配置子地点
3. **角色** — 创建角色并编写设定
4. **设置** — 填写世界名称、调整世界观等

> 详细步骤请参考 [使用指南](docs/USER_GUIDE.md)

### 运行

```bash
# Windows
start.bat

# 或直接用 Python
python -m world_engine
```

启动后：
- **聊天 Bot**：在 Telegram 中与 NPC 对话
- **管理 Bot**：使用管理命令控制世界
- **Dashboard**：访问 `http://localhost:8080` 管理世界

> ⚠️ **安全提示**：Dashboard 仅供本地使用，请勿暴露到公网。Dashboard API 会返回包含 API Key 的完整配置信息。

## 📁 项目结构

```
WorldEngine/
├── world_engine/          # 核心引擎
│   ├── __main__.py        # 入口点
│   ├── world.py           # 世界模拟（活动链、时间推进、离屏场景）
│   ├── scene.py           # DM 系统（裁定、旁白、临时角色）
│   ├── bot.py             # Telegram 双 Bot 架构
│   ├── character.py       # 角色引擎（Prompt 构建、回复生成）
│   ├── memory.py          # 记忆 CRUD（TTL、合并）
│   ├── memory_retrieval.py # 混合记忆检索（Embedding+jieba+Rerank）
│   ├── memory_pipeline.py # 对话后处理管道
│   ├── chroma_store.py    # ChromaDB 向量数据库存储层
│   ├── embedding.py       # 嵌入向量（云端API/本地模型双引擎）
│   ├── events.py          # 预定事件系统
│   ├── location.py        # 地点与坐标系统
│   ├── llm.py             # LLM 调用（SSE 清洗、重试、连接池）
│   ├── utils.py           # 工具函数 + TurnLogger
│   ├── dashboard.py       # FastAPI Web 管理面板
│   └── prompts/           # LLM Prompt 模板（12 个）
├── data/
│   ├── config.example.json # 配置模板
│   └── saves/             # 世界存档
├── docs/
│   └── ARCHITECTURE.md    # 架构文档
├── requirements.txt
├── start.bat
└── LICENSE
```

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────┐
│                    用户层                              │
│    Telegram Chat Bot  ←→  Telegram Admin Bot          │
│              FastAPI Dashboard (Web)                   │
├─────────────────────────────────────────────────────┤
│                    引擎层                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ DM 系统   │  │ 世界模拟  │  │ 记忆系统          │   │
│  │裁定/旁白  │  │活动链生成 │  │Embedding+Rerank  │   │
│  │临时角色   │  │离屏场景   │  │TTL/合并/管道     │   │
│  │可见性控制 │  │时间推进   │  │情绪更新          │   │
│  └──────────┘  └──────────┘  └──────────────────┘   │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ 事件系统  │  │ 角色引擎  │  │ 调试系统          │   │
│  │预定/去重  │  │Prompt构建│  │TurnLogger HTML   │   │
│  │爽约检测   │  │回复生成   │  │Token/费用统计    │   │
│  └──────────┘  └──────────┘  └──────────────────┘   │
├─────────────────────────────────────────────────────┤
│                    数据层                              │
│         JSON 文件存储  •  状态事务  •  存档管理         │
└─────────────────────────────────────────────────────┘
```

## 🛠️ 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.11+ |
| LLM 接口 | OpenAI 兼容 API（302.ai） |
| 用户界面 | Telegram Bot API + FastAPI |
| 记忆检索 | Embedding + jieba + Rerank（云端/本地可切换） |
| 记忆存储 | ChromaDB 向量数据库 |
| 世界状态 | JSON 文件（轻量、可读） |
| 日志系统 | TurnLogger（HTML）+ 标准日志 |

## 📖 文档

- [架构文档](docs/ARCHITECTURE.md) — 系统设计详解

## 🤝 贡献

欢迎贡献！请随时提交 Issue 或 Pull Request。

## 📄 许可证

本项目采用 [MIT 许可证](LICENSE) 开源。

---

<div align="center">

**如果这个项目对你有帮助，请给个 ⭐ Star！**

</div>
