# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [1.1.0] - 2026-03-13

### Added
- **ChromaDB 记忆存储** — 替代 JSON 文件，支持语义向量搜索
  - 新增 `chroma_store.py` 存储层
  - 新增 `migrate_to_chroma.py` 一键迁移脚本
- **Embedding/Rerank 三模式切换** — 在 Dashboard 中配置
  - ☁️ 云端 API（如 302.ai）
  - 💻 本地模型（sentence-transformers）
  - 🖥️ 本地 API（llama-server + GGUF 量化模型）
- **llama-server 自动生命周期管理** — 新增 `local_server.py`
  - 主程序启动时自动拉起 llama-server 子进程
  - 端口检测防重复启动
  - 主进程退出时（含异常）自动终止子进程
- Dashboard 新增 Embedding/Rerank 模式设置卡片（含本地 API 地址配置）
- 新增 `models/` 和 `tools/` 目录的 `.gitignore` 规则
- 新增默认世界和模板存档作为初始数据

### Changed
- `memory.py` 全面重写为 ChromaDB 后端
- `memory_retrieval.py` 改用 ChromaDB 向量搜索 + 可配置精排
- `memory_pipeline.py` 适配新记忆和检索逻辑
- `dashboard.py` 角色加载/保存/删除/归档适配 ChromaDB
- `config.json` 新增 `embedding.mode`、`local_api_base`、`gguf_model`、`memory_retrieval.rerank_mode`、`local_server` 等配置项
- 依赖新增 `chromadb`、`sentence-transformers`

### Fixed
- 修复存档模式下角色重命名/删除后残留的问题
- 修复存档模式下 lore 数据不显示的问题
- 新建角色不再默认添加名字条目

### Docs
- 更新 README、ARCHITECTURE.md、USER_GUIDE.md 反映新架构

## [1.0.0] - 2026-03-07

- 初始发布
