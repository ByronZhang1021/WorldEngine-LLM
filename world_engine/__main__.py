"""World Engine — 独立多角色世界模拟系统。

用法: python -m world_engine
同时启动 聊天Bot + 管理Bot + Dashboard（http://localhost:8080）
"""
import asyncio
import signal
import sys
import threading
import webbrowser

from .utils import log, DATA_DIR
from .bot import create_chat_bot, create_admin_bot
from .dashboard import start_dashboard


def _run_dashboard():
    """在后台线程中启动 Dashboard。"""
    try:
        start_dashboard(port=8080)
    except Exception as e:
        log("warning", f"Dashboard 异常退出: {e}")


async def main():
    """启动 World Engine（聊天Bot + 管理Bot + Dashboard）。"""
    log("info", "=" * 60)
    log("info", "World Engine 启动中...")
    log("info", f"数据目录: {DATA_DIR}")
    log("info", "=" * 60)

    # 启动本地 llama-server（如果配置了 local_api 模式）
    from . import local_server
    local_server.start()

    # 后台线程启动 Dashboard
    dashboard_thread = threading.Thread(target=_run_dashboard, daemon=True)
    dashboard_thread.start()
    log("info", "Dashboard 启动在 http://localhost:8080")

    # 延迟打开浏览器（给 Dashboard 一点启动时间）
    def _open_browser():
        import time
        time.sleep(2)
        webbrowser.open("http://localhost:8080")
    threading.Thread(target=_open_browser, daemon=True).start()

    # 创建 Bot
    chat_bot = create_chat_bot()
    admin_bot = create_admin_bot()

    stop_event = asyncio.Event()

    def _signal_handler():
        log("info", "收到退出信号，正在关闭...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    # 启动聊天 Bot
    async with chat_bot:
        await chat_bot.initialize()
        await chat_bot.start()
        await chat_bot.updater.start_polling(drop_pending_updates=True)
        log("info", "聊天 Bot 已启动，等待消息...")

        # 注册聊天 Bot 命令菜单
        try:
            from telegram import BotCommand
            await chat_bot.bot.set_my_commands([
                BotCommand("call", "打电话给某个角色"),
                BotCommand("myplan", "查看你的预定事件"),
                BotCommand("locations", "查看地点和距离"),
                BotCommand("help", "查看详细帮助"),
            ])
            log("info", "聊天 Bot 命令菜单已注册")
        except Exception as e:
            log("warning", f"聊天 Bot 命令菜单注册失败: {e}")

        # 启动管理 Bot（如果配置了）
        if admin_bot:
            async with admin_bot:
                await admin_bot.initialize()
                await admin_bot.start()
                await admin_bot.updater.start_polling(drop_pending_updates=True)
                log("info", "管理 Bot 已启动，等待命令...")

                # 注册管理 Bot 命令菜单
                try:
                    await admin_bot.bot.set_my_commands([
                        BotCommand("time", "查看/调整虚拟时间"),
                        BotCommand("where", "查看所有角色位置"),
                        BotCommand("activities", "查看所有角色活动链"),
                        BotCommand("move", "强制移动角色到指定地点"),
                        BotCommand("play", "切换扮演的角色"),
                        BotCommand("memo", "查看角色记忆"),
                        BotCommand("schedule", "查看预定事件"),
                        BotCommand("events", "查看事件列表"),
                        BotCommand("event", "查看事件详情"),
                        BotCommand("sessions", "查看对话记录"),
                        BotCommand("session", "查看对话详情"),
                        BotCommand("saves", "查看存档列表"),
                        BotCommand("save", "保存当前存档"),
                        BotCommand("load", "加载存档"),
                        BotCommand("help", "查看详细帮助"),
                    ])
                    log("info", "管理 Bot 命令菜单已注册")
                except Exception as e:
                    log("warning", f"管理 Bot 命令菜单注册失败: {e}")

                try:
                    await stop_event.wait()
                except KeyboardInterrupt:
                    pass
                finally:
                    log("info", "正在停止...")
                    await admin_bot.updater.stop()
                    await admin_bot.stop()
                    await admin_bot.shutdown()
        else:
            try:
                await stop_event.wait()
            except KeyboardInterrupt:
                pass

        log("info", "正在停止聊天 Bot...")
        await chat_bot.updater.stop()
        await chat_bot.stop()
        await chat_bot.shutdown()

    # 关闭本地 llama-server
    local_server.shutdown()

    log("info", "World Engine 已关闭。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
