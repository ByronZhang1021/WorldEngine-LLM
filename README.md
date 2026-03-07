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

WorldEngine 是一个**自主运行的虚拟世界模拟器**。它不是一个简单的聊天机器人——而是一个完整的世界，NPC 拥有独立的日常活动、记忆、情绪和社交关系，即使你不在场，世界也在继续运转。

### 核心理念

- **NPC 是活的** — 每个 NPC 都有由 LLM 生成的活动链，在虚拟时间线上独立行动
- **DM（地下城主）裁定一切** — 玩家的行为经过 DM 系统审核，确保叙事一致性
- **世界会自己转** — 离屏的 NPC 之间也会发生互动，产生记忆，影响世界

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

## 🎮 核心特性

### 🤖 DM（地下城主）系统
- **行为裁定**：DM 审核玩家行为的合理性，拒绝不合理操作
- **旁白叙事**：DM 生成第三人称旁白，丰富场景描述
- **临时角色**：DM 可以临时创建/管理路人 NPC
- **行为可见性**：控制哪些角色能感知到特定行为

### 🌐 自主世界模拟
- **活动链生成**：LLM 为每个 NPC 生成未来的活动计划
- **离屏场景**：当多个 NPC 在同一地点时，自动生成互动场景
- **虚拟时间线**：基于对话节奏的智能时间推进
- **地点发现**：NPC 可以发现和探索新区域

### 🧠 混合记忆检索系统
- **Tier 1 保护**：最近 24 世界小时的记忆始终纳入
- **Embedding + jieba**：向量相似度 + 中文关键词混合召回
- **Rerank 精排**：两阶段精排保证检索质量
- **TTL 过期**：记忆自动过期，支持永久/小时/分钟级
- **LLM 合并**：自动合并相似记忆，减少冗余

### 📅 预定事件系统
- **智能去重**：时间窗口 + 主题双重去重
- **爽约检测**：检查参与者是否到场，为被爽约者生成记忆
- **变更通知**：取消/修改事件时自动通知其他参与者
- **活动链联动**：事件完成/过期后自动截断相关活动链

### 🔍 TurnLogger 调试系统
每次用户交互生成精美的 HTML 调试页面，包含：
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

1. 在 `data/saves/save_示例/` 中查看示例世界结构
2. 创建你自己的世界数据（角色、地点、世界观）
3. 通过 Dashboard 或手动复制到 `data/current/` 来加载世界

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

## 🛠️ 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.11+ |
| LLM 接口 | OpenAI 兼容 API（302.ai） |
| 用户界面 | Telegram Bot API + FastAPI |
| 记忆检索 | Embedding + jieba + Rerank |
| 数据存储 | JSON 文件（轻量、可读、可版本控制） |
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
