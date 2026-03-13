"""
本地 llama-server 生命周期管理。

当 Embedding 或 Rerank 配置为 local_api 模式时，自动启动 llama-server 子进程，
并在主进程退出（包括异常退出）时自动终止。

防重复：启动前检测端口是否已被占用，避免多次启动叠加。
"""

import atexit
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from .utils import PROJECT_DIR, load_config

log = logging.getLogger("world_engine")

_processes: list[subprocess.Popen] = []
_cleanup_registered = False


def _is_port_in_use(port: int) -> bool:
    """检查端口是否已被占用。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _parse_port(url: str) -> int:
    """从 URL 中提取端口号。"""
    parsed = urlparse(url)
    return parsed.port or 80


def _find_llama_server() -> str | None:
    """查找 llama-server 可执行文件。

    搜索顺序：
    1. config 中指定的 local_server.llama_server_path
    2. 项目目录 tools/ 下
    3. 系统 PATH
    """
    config = load_config()
    ls_cfg = config.get("local_server", {})

    # 1. config 指定路径
    custom_path = ls_cfg.get("llama_server_path", "")
    if custom_path:
        p = Path(custom_path)
        if not p.is_absolute():
            p = PROJECT_DIR / p
        if p.exists():
            return str(p)

    # 2. 项目 tools/ 目录
    tools_dir = PROJECT_DIR / "tools"
    for name in ["llama-server.exe", "llama-server"]:
        p = tools_dir / name
        if p.exists():
            return str(p)

    # 3. 系统 PATH
    import shutil
    found = shutil.which("llama-server")
    if found:
        return found

    return None


def _wait_for_server(port: int, timeout: float = 30.0) -> bool:
    """等待服务器就绪。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _is_port_in_use(port):
            return True
        time.sleep(0.5)
    return False


def _start_server(
    llama_server: str,
    model_path: str,
    port: int,
    extra_args: list[str],
    label: str,
) -> subprocess.Popen | None:
    """启动一个 llama-server 子进程。"""
    if _is_port_in_use(port):
        log.info(f"[LocalServer] {label}: 端口 {port} 已被占用，跳过启动")
        return None

    # 解析模型路径（支持相对路径）
    mp = Path(model_path)
    if not mp.is_absolute():
        mp = PROJECT_DIR / mp
    if not mp.exists():
        log.warning(f"[LocalServer] {label}: 模型文件不存在: {mp}")
        return None

    cmd = [
        llama_server,
        "-m", str(mp),
        "--port", str(port),
        "-ngl", "99",   # 全部层放 GPU
    ] + extra_args

    log.info(f"[LocalServer] 启动 {label}: {' '.join(cmd)}")

    # 隐藏子进程窗口（Windows）
    kwargs = {}
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = si
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )
    _processes.append(proc)

    # 等待就绪
    if _wait_for_server(port, timeout=30.0):
        log.info(f"[LocalServer] {label} 已就绪 (PID={proc.pid}, 端口={port})")
    else:
        log.warning(f"[LocalServer] {label} 启动超时！(PID={proc.pid})")

    return proc


def _cleanup():
    """终止所有子进程。"""
    for proc in _processes:
        if proc.poll() is None:
            try:
                log.info(f"[LocalServer] 终止子进程 PID={proc.pid}")
                if sys.platform == "win32":
                    proc.terminate()
                else:
                    proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    _processes.clear()


def start():
    """根据配置自动启动所需的 llama-server 实例。

    - 仅在 embedding 或 rerank 配置为 'local_api' 时才启动
    - 需要在 config 中配置 gguf_model 路径
    - 自动注册退出清理
    """
    global _cleanup_registered
    config = load_config()

    emb_cfg = config.get("models", {}).get("embedding", {})
    mr_cfg = config.get("memory_retrieval", {})
    ls_cfg = config.get("local_server", {})

    need_embedding = emb_cfg.get("mode") == "local_api"
    need_rerank = mr_cfg.get("rerank_mode") == "local_api"

    if not need_embedding and not need_rerank:
        return

    if not ls_cfg.get("auto_start", True):
        log.info("[LocalServer] auto_start 已关闭，跳过")
        return

    llama_server = _find_llama_server()
    if not llama_server:
        log.warning(
            "[LocalServer] 未找到 llama-server！"
            "请将 llama-server 放入 tools/ 目录或设置 PATH。"
            "下载地址: https://github.com/ggml-org/llama.cpp/releases"
        )
        return

    # 注册清理
    if not _cleanup_registered:
        atexit.register(_cleanup)
        _cleanup_registered = True

    # 启动 Embedding 服务
    if need_embedding:
        emb_model = emb_cfg.get("gguf_model", "")
        if emb_model:
            port = _parse_port(emb_cfg.get("local_api_base", "http://localhost:8081"))
            _start_server(
                llama_server, emb_model, port,
                extra_args=["-c", "512", "--embedding"],
                label="Embedding",
            )
        else:
            log.warning("[LocalServer] Embedding 已启用 local_api 但未配置 gguf_model")

    # 启动 Rerank 服务
    if need_rerank:
        rr_model = mr_cfg.get("gguf_model", "")
        if rr_model:
            port = _parse_port(mr_cfg.get("local_api_base", "http://localhost:8082"))
            _start_server(
                llama_server, rr_model, port,
                extra_args=["-c", "8192", "--rerank", "--pooling", "rank"],
                label="Reranker",
            )
        else:
            log.warning("[LocalServer] Rerank 已启用 local_api 但未配置 gguf_model")


def shutdown():
    """手动关闭所有 llama-server 子进程。"""
    _cleanup()
