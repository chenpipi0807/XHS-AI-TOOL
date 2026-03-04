#!/usr/bin/env python
"""
Tool/cli.py — 小红书 AI 运营助手 终端 CLI
============================================

用法：
  cd Tool && python cli.py

内置命令（以 / 开头）：
  /h  /help          显示帮助
  /q  /quit /exit    退出
  /c  /clear         清空对话历史
  /tools             列出当前可用 MCP 工具
  /mcp               检测 MCP 服务状态
  /start             一键启动所有 MCP 服务（SQLite + XHS）
  /start-sqlite      仅启动 SQLite MCP 服务（后台）
  /start-xhs         仅启动 XHS MCP 服务（后台）
  /stop              停止所有 MCP 服务
  /proj [ID]         切换/查看当前项目
  /posts             列出最近帖子
  /drafts            列出草稿
  /analyze [天数]    分析账号数据（默认 7 天）
  /plan              生成本周内容计划
  /gen <主题>        快捷生成一篇小红书帖子
  /img <提示词>      生成图片（Gemini）
  /todo              查看 Todo 列表
  /todo add <内容>   添加 Todo
  /todo done <ID>    标记 Todo 完成

直接输入任意文字 → 与 Kimi Agent 对话（支持 MCP 工具调用）
输入技巧：Enter 发送，Esc+Enter 或 Ctrl+J 换行，支持直接粘贴多行文本
"""

from __future__ import annotations

import os
import sys
import subprocess
import time
import json
import logging
import threading
import itertools
from pathlib import Path
from typing import Optional

# ── 路径设置 ────────────────────────────────────────────────────────────────
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

from dotenv import load_dotenv
load_dotenv(_THIS_DIR / ".env", override=False)

# ── 多行输入（prompt_toolkit）────────────────────────────────────────────────
# Enter = 发送，Escape+Enter 或 Alt+Enter = 换行
# 粘贴多行文本：整段进 buffer，按 Enter 一次性发送 ✓
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.formatted_text import ANSI

    _pt_bindings = KeyBindings()

    # Escape+Enter（某些终端 Shift+Enter 会发送此序列）→ 插入换行
    @_pt_bindings.add("escape", "enter")
    def _pt_escape_enter(event):
        """Escape+Enter / Alt+Enter 插入换行"""
        event.current_buffer.insert_text("\n")

    # Ctrl+J 也可作为换行的快捷键（备用）
    @_pt_bindings.add("c-j")
    def _pt_ctrl_j(event):
        """Ctrl+J 插入换行（备用 Shift+Enter）"""
        event.current_buffer.insert_text("\n")

    _pt_session: "PromptSession | None" = None

    def _read_input(prompt_text: str) -> str:
        global _pt_session
        if _pt_session is None:
            _pt_session = PromptSession(
                key_bindings=_pt_bindings,
                multiline=False,
            )
        return _pt_session.prompt(ANSI(prompt_text))

    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False

    def _read_input(prompt_text: str) -> str:  # type: ignore[misc]
        return input(prompt_text)

# ── 日志静默（CLI 模式只显示必要信息）───────────────────────────────────────
logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anthropic").setLevel(logging.ERROR)

# ── ANSI 颜色 ────────────────────────────────────────────────────────────────
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    GRAY   = "\033[90m"
    MAGENTA= "\033[95m"
    WHITE  = "\033[97m"

def _c(text: str, color: str) -> str:
    """包裹颜色代码（Windows 需要 ANSI 支持）"""
    return f"{color}{text}{C.RESET}"

# Windows 启用 ANSI
if sys.platform == "win32":
    import ctypes
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass

# ── 后台 MCP 进程管理 ────────────────────────────────────────────────────────
_SERVICES: dict[str, subprocess.Popen] = {}

def _start_mcp(name: str, cmd: list[str], cwd: Path) -> bool:
    """启动 MCP 后台进程，返回是否成功"""
    if name in _SERVICES:
        proc = _SERVICES[name]
        if proc.poll() is None:
            print(_c(f"  ✓ {name} 已在运行 (PID {proc.pid})", C.YELLOW))
            return True
    try:
        env = os.environ.copy()
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _SERVICES[name] = proc
        time.sleep(1.5)  # 等待启动
        if proc.poll() is None:
            print(_c(f"  ✓ {name} 启动成功 (PID {proc.pid})", C.GREEN))
            return True
        else:
            print(_c(f"  ✗ {name} 启动失败（进程已退出）", C.RED))
            return False
    except Exception as e:
        print(_c(f"  ✗ {name} 启动失败: {e}", C.RED))
        return False

def _stop_all():
    for name, proc in list(_SERVICES.items()):
        if proc.poll() is None:
            proc.terminate()
            print(_c(f"  ✓ {name} 已停止", C.GRAY))
    _SERVICES.clear()

def _check_port(port: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except Exception:
        return False

# ── 全局 Agent 实例 ──────────────────────────────────────────────────────────
_agent = None
_current_project_id: Optional[int] = None

def _get_agent(force_new: bool = False):
    global _agent
    if _agent is None or force_new:
        from agent.kimi_agent import KimiAgent
        _agent = KimiAgent(project_id=_current_project_id, max_tool_calls=20)
    return _agent

# ── 输出辅助 ─────────────────────────────────────────────────────────────────
def _print_banner():
    print()
    print(_c("╔══════════════════════════════════════════╗", C.MAGENTA))
    print(_c("║   🌺 小红书 AI 运营助手 · CLI  v1.0      ║", C.MAGENTA))
    print(_c("║   输入 /h 查看命令，直接输入消息对话      ║", C.MAGENTA))
    print(_c("╚══════════════════════════════════════════╝", C.MAGENTA))
    print()

def _print_help():
    lines = [
        ("", ""),
        ("── 对话", ""),
        ("  <任意文字>",              "与 Kimi Agent 对话（支持 MCP 工具）"),
        ("  /c  /clear",              "清空当前对话历史"),
        ("", ""),
        ("── MCP 服务", ""),
        ("  /mcp",                    "检测 MCP 服务在线状态"),
        ("  /tools",                  "列出所有可用 MCP 工具"),
        ("  /r  /run  /start",        "一键启动全部 MCP（SQLite :17231 + XHS :17232）"),
        ("  /start-sqlite",           "仅启动 SQLite MCP（端口 17231）"),
        ("  /start-xhs",              "仅启动 XHS MCP（端口 17232）"),
        ("  /stop",                   "停止所有后台 MCP 服务"),
        ("", ""),
        ("── 小红书 XHS", ""),
        ("  /xhs",                    "查看 XHS 详细状态（登录态、cookie 有效期、工具列表）"),
        ("  /login",                  "获取小红书扫码登录二维码"),
        ("  /logout",                 "清除登录 cookie（下次需重新扫码）"),
        ("", ""),
        ("── 内容操作", ""),
        ("  /posts",                  "列出最近 10 篇帖子"),
        ("  /drafts",                 "列出草稿"),
        ("  /analyze [天数]",         "分析账号数据（默认 7 天）"),
        ("  /plan",                   "生成本周内容计划"),
        ("  /gen <主题>",             "快速生成小红书帖子（如：/gen 减脂早餐）"),
        ("  /img <提示词>",           "生成图片（如：/img 可爱猫咪 赛博风）"),
        ("", ""),
        ("── Todo 管理", ""),
        ("  /todo",                   "查看所有待办 Todo（pending + in_progress）"),
        ("  /todo all",               "查看全部 Todo（含已完成）"),
        ("  /todo add <标题>",        "添加新 Todo（如：/todo add 写明天的帖子）"),
        ("  /todo done <ID>",         "将 Todo 标记为完成"),
        ("  /todo start <ID>",        "将 Todo 标记为进行中"),
        ("  /todo cancel <ID>",       "取消 Todo"),
        ("", ""),
        ("── 项目", ""),
        ("  /proj",                   "查看当前项目"),
        ("  /proj list",              "列出所有项目"),
        ("  /proj <ID>",              "切换到指定项目"),
        ("", ""),
        ("── 其他", ""),
        ("  /h  /help",               "显示此帮助"),
        ("  /q  /quit /exit",         "退出"),
        ("", ""),
    ]
    for cmd, desc in lines:
        if not cmd and not desc:
            print()
        elif not desc:
            print(_c(cmd, C.BOLD))
        else:
            print(f"  {_c(cmd, C.CYAN):<35} {_c(desc, C.GRAY)}")

def _print_mcp_status():
    sqlite_port = int(os.getenv("SQLITE_MCP_PORT", "17231"))
    xhs_port    = int(os.getenv("XHS_MCP_PORT",    "17232"))
    sq = _check_port(sqlite_port)
    xh = _check_port(xhs_port)
    sq_str = _c("● 在线", C.GREEN) if sq else _c("○ 离线", C.RED)
    xh_str = _c("● 在线", C.GREEN) if xh else _c("○ 离线", C.RED)
    print(f"\n  SQLite MCP  :{sqlite_port}  {sq_str}")
    print(f"  XHS    MCP  :{xhs_port}  {xh_str}\n")


def _print_xhs_status():
    """显示 XHS MCP 详细状态：服务在线、cookie 有效期、登录状态"""
    import json as _json, time as _time
    from pathlib import Path

    xhs_port = int(os.getenv("XHS_MCP_PORT", "17232"))
    online   = _check_port(xhs_port)

    print()
    print(_c("── 小红书 MCP 状态 ─────────────────────────────", C.BOLD))
    # 服务状态
    svc_str = _c("● 在线", C.GREEN) if online else _c("○ 离线（用 /r 启动）", C.RED)
    print(f"  服务状态   {svc_str}  (:{xhs_port})")

    # Cookie 状态（从 MCP/cookies.json 直接读取，不触发浏览器）
    cookie_path = Path(__file__).parent.parent / "MCP" / "cookies.json"
    now = _time.time()
    if cookie_path.exists():
        try:
            cookies = _json.loads(cookie_path.read_text(encoding="utf-8"))
            total   = len(cookies)
            # 找 web_session
            session = next((c for c in cookies if c.get("name") == "web_session"), None)
            if session:
                exp = session.get("expires", -1)
                if exp == -1 or exp > now:
                    import datetime
                    if exp == -1:
                        exp_str = "永不过期"
                    else:
                        dt = datetime.datetime.fromtimestamp(exp)
                        exp_str = dt.strftime("%Y-%m-%d %H:%M")
                    login_str = _c(f"● 已登录  (cookie 有效至 {exp_str})", C.GREEN)
                else:
                    import datetime
                    dt = datetime.datetime.fromtimestamp(exp)
                    login_str = _c(f"✗ cookie 已过期 ({dt.strftime('%Y-%m-%d')})", C.RED)
            else:
                login_str = _c("✗ 未找到 web_session（未登录）", C.RED)

            # 统计过期 cookie
            expired = sum(
                1 for c in cookies
                if c.get("expires", -1) != -1 and c["expires"] < now
            )
            print(f"  登录状态   {login_str}")
            print(f"  Cookie     共 {total} 个，其中 {_c(str(expired)+' 个已过期', C.YELLOW if expired else C.GRAY)}")
        except Exception as e:
            print(f"  Cookie     {_c(f'读取失败: {e}', C.RED)}")
    else:
        print(f"  Cookie     {_c('cookies.json 不存在，请先登录', C.RED)}")

    # XHS MCP 工具列表（如果服务在线）
    if online:
        try:
            import urllib.request as _ur
            payload = _json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}).encode()
            req = _ur.Request(
                f"http://localhost:{xhs_port}/mcp/",
                data=payload,
                headers={"Content-Type":"application/json","Accept":"application/json, text/event-stream"},
                method="POST",
            )
            with _ur.urlopen(req, timeout=5) as r:
                data  = _json.loads(r.read())
                tools = data.get("result", {}).get("tools", [])
            print(f"  工具列表   共 {_c(str(len(tools))+' 个', C.CYAN)} XHS 工具：")
            for t in tools:
                print(f"    {_c('·', C.GRAY)} {_c(t['name'], C.WHITE):<32} {_c(t['description'][:50], C.GRAY)}")
        except Exception as e:
            print(f"  工具列表   {_c(f'获取失败: {e}', C.YELLOW)}")
    print()


def _handle_xhs_login():
    """调用 XHS MCP 的 get_login_qrcode 工具获取扫码二维码"""
    xhs_port = int(os.getenv("XHS_MCP_PORT", "17232"))
    if not _check_port(xhs_port):
        print(_c("  ✗ XHS MCP 未运行，请先用 /r 启动", C.RED))
        return
    print(_c("  📱 正在获取小红书登录二维码...", C.CYAN))
    _stream_chat("请调用 get_login_qrcode 工具获取小红书扫码登录二维码，并告知我扫码步骤。")


def _handle_xhs_logout():
    """调用 XHS MCP 的 delete_cookies 工具清除登录"""
    xhs_port = int(os.getenv("XHS_MCP_PORT", "17232"))
    if not _check_port(xhs_port):
        print(_c("  ✗ XHS MCP 未运行，请先用 /r 启动", C.RED))
        return
    print(_c("  🗑️  正在清除小红书登录 cookie...", C.CYAN))
    _stream_chat("请调用 delete_cookies 工具清除小红书登录 cookie。")

def _print_tools():
    agent = _get_agent()
    agent._init()
    tools = agent._tools
    if not tools:
        print(_c("  (无可用工具——请先启动 MCP 服务)", C.YELLOW))
        return
    print(_c(f"\n  共 {len(tools)} 个工具：", C.BOLD))
    for t in tools:
        server = agent._tool_server.get(t["name"])
        sname = server.server_name if server else "?"
        print(f"  {_c('●', C.CYAN)} {_c(t['name'], C.WHITE):<35} "
              f"{_c('['+sname+']', C.GRAY):<12} "
              f"{_c(t['description'][:60], C.GRAY)}")
    print()

# ── 进度条（慢工具专用）────────────────────────────────────────────────────────

# 慢工具集合（与 kimi_agent._SLOW_TOOLS 保持一致）
# XHS 所有工具首次调用都会触发 Playwright 浏览器冷启动，需要 spinner
_SLOW_TOOLS_CLI = {
    "generate_image",
    "check_login_status", "get_login_qrcode", "delete_cookies",
    "publish_content", "publish_with_video",
    "list_feeds", "search_feeds", "get_feed_detail",
    "post_comment_to_feed", "reply_comment_in_feed",
    "like_feed", "favorite_feed",
    "user_profile", "get_my_profile",
}

class _SpinnerThread:
    """后台 spinner，在同一行原地刷新，直到 stop() 被调用"""

    _FRAMES = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

    def __init__(self, label: str):
        self._label = label
        self._stop_evt = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self, final_line: str = ""):
        self._stop_evt.set()
        self._thread.join()
        # 清除当前行后输出最终状态
        sys.stdout.write(f"\r\033[K{final_line}\n")
        sys.stdout.flush()

    def _run(self):
        elapsed = 0.0
        for frame in itertools.cycle(self._FRAMES):
            if self._stop_evt.is_set():
                break
            mins, secs = divmod(int(elapsed), 60)
            timer = f"{mins:02d}:{secs:02d}" if mins else f"{secs:02d}s"
            line = (
                f"\r  {_c(frame, C.CYAN)} "
                f"{_c(self._label, C.YELLOW)}  "
                f"{_c(timer, C.GRAY)}"
            )
            sys.stdout.write(line)
            sys.stdout.flush()
            time.sleep(0.1)
            elapsed += 0.1


def _stream_chat(message: str):
    """流式输出 Agent 回复"""
    agent = _get_agent()
    print()
    print(_c("🤖 Kimi: ", C.CYAN), end="", flush=True)

    full_text = ""
    in_text = False
    _spinner: "_SpinnerThread | None" = None

    def _stop_spinner(final_line: str = ""):
        nonlocal _spinner
        if _spinner is not None:
            _spinner.stop(final_line)
            _spinner = None

    try:
        for chunk in agent.chat_stream(message):
            ctype = chunk.get("type", "")

            if ctype == "text":
                delta = chunk.get("delta", "")
                if delta:
                    print(delta, end="", flush=True)
                    full_text += delta
                    in_text = True

            elif ctype == "tool_start":
                if in_text:
                    print()
                    in_text = False
                name = chunk.get("name", "")
                args = chunk.get("arguments", {})
                args_str = json.dumps(args, ensure_ascii=False)
                if len(args_str) > 80:
                    args_str = args_str[:80] + "…"
                print(_c(f"\n  🔧 调用工具: {name}", C.YELLOW))
                print(_c(f"     参数: {args_str}", C.GRAY))

                # 耗时工具 → 启动 spinner 进度条
                if name in _SLOW_TOOLS_CLI:
                    label = {
                        "generate_image":       "🎨 生图中，请稍候",
                        "publish_content":      "📤 发布笔记中",
                        "publish_with_video":   "🎬 上传视频中",
                        "check_login_status":   "🔐 检查登录状态",
                        "get_login_qrcode":     "📱 获取登录二维码",
                        "delete_cookies":       "🗑️  清除登录信息",
                        "list_feeds":           "📋 获取推荐内容",
                        "search_feeds":         "🔍 搜索小红书",
                        "get_feed_detail":      "📖 获取笔记详情",
                        "post_comment_to_feed": "💬 发表评论",
                        "reply_comment_in_feed":"↩️  回复评论",
                        "like_feed":            "❤️  点赞",
                        "favorite_feed":        "⭐ 收藏",
                        "user_profile":         "👤 获取用户信息",
                        "get_my_profile":       "👤 获取我的信息",
                    }.get(name, f"⚙️  {name} 执行中")
                    _spinner = _SpinnerThread(label).start()

            elif ctype == "tool_result":
                name = chunk.get("name", "")
                result = chunk.get("result", "")
                # 先停 spinner，再打印结果
                result_preview = result[:120] + "…" if len(result) > 120 else result
                ok = not result_preview.startswith('{"error"')
                icon = "✓" if ok else "✗"
                color = C.GREEN if ok else C.RED
                _stop_spinner(
                    _c(f"  {icon} {name} 完成: {result_preview}", color)
                )
                print(_c("\n🤖 Kimi: ", C.CYAN), end="", flush=True)
                in_text = False

            elif ctype == "error":
                _stop_spinner()
                print()
                print(_c(f"\n  ✗ 错误: {chunk.get('content', '')}", C.RED))

            elif ctype == "done":
                _stop_spinner()
                if in_text:
                    print()

    except KeyboardInterrupt:
        _stop_spinner()
        print(_c("\n  (已中断)", C.YELLOW))

    print()

# ── 命令处理 ─────────────────────────────────────────────────────────────────

def _handle_posts(status: str = "all"):
    try:
        import database as db
        db.init_db()
        posts = db.get_posts(project_id=_current_project_id, limit=10)
        if status == "draft":
            posts = [p for p in posts if p.get("status") == "draft"]
        if not posts:
            print(_c("  (暂无帖子)", C.GRAY))
            return
        print(_c(f"\n  最近帖子（共 {len(posts)} 篇）：", C.BOLD))
        for p in posts:
            st = p.get("status", "?")
            st_color = C.YELLOW if st == "draft" else C.GREEN
            print(f"  [{_c(str(p['id']), C.CYAN)}] "
                  f"{_c(st, st_color):<8} "
                  f"{p.get('title','(无标题)')[:50]}")
        print()
    except Exception as e:
        print(_c(f"  获取帖子失败: {e}", C.RED))

def _handle_proj(args: str):
    global _agent, _current_project_id
    try:
        import database as db
        db.init_db()
        if not args or args == "list":
            projects = db.get_all_projects()
            if not projects:
                print(_c("  (暂无项目)", C.GRAY))
                return
            for p in projects:
                mark = _c("◀ 当前", C.GREEN) if p["id"] == _current_project_id else ""
                print(f"  [{_c(str(p['id']), C.CYAN)}] {p['name']} {mark}")
            return
        pid = int(args.strip())
        proj = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
        if not proj:
            print(_c(f"  项目 {pid} 不存在", C.RED))
            return
        _current_project_id = pid
        _agent = None  # 重建 agent
        print(_c(f"  ✓ 已切换到项目: {proj['name']}", C.GREEN))
    except ValueError:
        print(_c("  用法: /proj [ID|list]", C.YELLOW))
    except Exception as e:
        print(_c(f"  操作失败: {e}", C.RED))

def _handle_img(prompt: str):
    if not prompt:
        print(_c("  用法: /img <提示词>", C.YELLOW))
        return
    print(_c(f"  🎨 正在生成图片: {prompt}", C.CYAN))
    try:
        from image.gemini_image import generate_image
        result = generate_image(
            prompt=prompt,
            project_id=_current_project_id or 0,
        )
        if result.get("success"):
            paths = result.get("saved_paths", [])
            print(_c(f"  ✓ 生成成功！保存路径:", C.GREEN))
            for p in paths:
                print(_c(f"    {p}", C.WHITE))
        else:
            print(_c(f"  ✗ 生成失败: {result.get('error', '未知错误')}", C.RED))
    except Exception as e:
        print(_c(f"  ✗ 错误: {e}", C.RED))

def _handle_todo(args: str):
    """处理 /todo 命令"""
    try:
        import database as db
        db.init_db()
    except Exception as e:
        print(_c(f"  数据库错误: {e}", C.RED))
        return

    parts = args.strip().split(None, 1) if args.strip() else []
    sub   = parts[0].lower() if parts else ""
    rest  = parts[1].strip() if len(parts) > 1 else ""

    # /todo add <标题>
    if sub == "add":
        if not rest:
            print(_c("  用法: /todo add <标题>", C.YELLOW))
            return
        try:
            import database as db
            db.init_db()
            todo_id = db.execute(
                """INSERT INTO todos (title, project_id, created_by, status, priority)
                   VALUES (?, ?, 'user', 'pending', 0)""",
                [rest, _current_project_id]
            )
            print(_c(f"  ✓ Todo 已创建 [ID: {todo_id}]: {rest}", C.GREEN))
        except Exception as e:
            print(_c(f"  创建失败: {e}", C.RED))
        return

    # /todo done <ID>
    if sub == "done":
        _todo_set_status(rest, "done", "✅")
        return

    # /todo start <ID>
    if sub == "start":
        _todo_set_status(rest, "in_progress", "🔄")
        return

    # /todo cancel <ID>
    if sub == "cancel":
        _todo_set_status(rest, "cancelled", "❌")
        return

    # /todo [all] — 列出
    show_all = (sub == "all")
    _todo_list(show_all=show_all)


def _todo_set_status(id_str: str, status: str, icon: str):
    if not id_str.isdigit():
        print(_c(f"  用法: /todo done <ID>", C.YELLOW))
        return
    try:
        import database as db
        todo = db.query_one("SELECT id, title FROM todos WHERE id = ?", [int(id_str)])
        if not todo:
            print(_c(f"  Todo ID {id_str} 不存在", C.RED))
            return
        db.execute(
            "UPDATE todos SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [status, int(id_str)]
        )
        print(_c(f"  {icon} [{id_str}] {todo['title']} → {status}", C.GREEN))
    except Exception as e:
        print(_c(f"  操作失败: {e}", C.RED))


def _todo_list(show_all: bool = False):
    try:
        import database as db
        db.init_db()
        if show_all:
            rows = db.query_all(
                "SELECT * FROM todos ORDER BY priority DESC, created_at DESC LIMIT 50", []
            )
        else:
            rows = db.query_all(
                """SELECT * FROM todos WHERE status IN ('pending','in_progress')
                   ORDER BY priority DESC, created_at DESC LIMIT 30""", []
            )

        if not rows:
            msg = "（暂无 Todo）" if show_all else "（没有待办项，/todo all 查看全部）"
            print(_c(f"  {msg}", C.GRAY))
            return

        status_icons = {
            "pending":     _c("⏳ 待办", C.YELLOW),
            "in_progress": _c("🔄 进行", C.CYAN),
            "done":        _c("✅ 完成", C.GREEN),
            "cancelled":   _c("❌ 取消", C.GRAY),
        }
        priority_marks = {0: "", 1: _c(" [高]", C.YELLOW), 2: _c(" [紧急]", C.RED)}

        title_label = "全部 Todo" if show_all else "待办 Todo"
        print(_c(f"\n  {title_label}（共 {len(rows)} 项）：", C.BOLD))
        for r in rows:
            sid  = _c(str(r["id"]).rjust(4), C.GRAY)
            icon = status_icons.get(r["status"], r["status"])
            pri  = priority_marks.get(r.get("priority", 0), "")
            due  = f"  截止: {r['due_date']}" if r.get("due_date") else ""
            print(f"  [{sid}] {icon}  {r['title']}{pri}{_c(due, C.GRAY)}")
        print()
    except Exception as e:
        print(_c(f"  读取 Todo 失败: {e}", C.RED))


def _handle_analyze(days_str: str):
    days = 7
    if days_str:
        try:
            days = int(days_str.strip())
        except ValueError:
            pass
    msg = f"请分析我最近 {days} 天的小红书账号数据，给出详细的数据报告和优化建议。"
    _stream_chat(msg)

# ── 一键启动所有 MCP ──────────────────────────────────────────────────────────

def _start_all_mcp():
    """一键启动 SQLite MCP + XHS MCP（已运行则跳过）"""
    global _agent
    sqlite_port = int(os.getenv("SQLITE_MCP_PORT", "17231"))
    xhs_port    = int(os.getenv("XHS_MCP_PORT",    "17232"))
    mcp_dir     = _THIS_DIR.parent / "MCP"
    started_any = False

    if _check_port(sqlite_port):
        print(_c(f"  ✓ SQLite MCP 已在 :{sqlite_port} 运行", C.GREEN))
    else:
        print(_c(f"  ▶ 启动 SQLite MCP 服务（端口 {sqlite_port}）...", C.CYAN))
        ok = _start_mcp(
            "sqlite_mcp",
            [sys.executable, "-m", "mcp_sqlite.sqlite_mcp_server"],
            cwd=_THIS_DIR,
        )
        if ok:
            started_any = True

    if _check_port(xhs_port):
        print(_c(f"  ✓ XHS MCP 已在 :{xhs_port} 运行", C.GREEN))
    else:
        print(_c(f"  ▶ 启动 XHS MCP 服务（端口 {xhs_port}）...", C.CYAN))
        ok = _start_mcp(
            "xhs_mcp",
            [sys.executable, "main.py", "--port", str(xhs_port)],
            cwd=mcp_dir,
        )
        if ok:
            started_any = True

    if started_any:
        print(_c("  ⏳ 等待服务就绪...", C.CYAN))
        import time; time.sleep(2)
        _agent = None  # 重建 agent 以重新发现工具
        print(_c("  ✓ MCP 服务启动完成，Agent 将重新发现工具", C.GREEN))

# ── 主循环 ───────────────────────────────────────────────────────────────────

def main():
    global _agent

    _print_banner()

    # 检查数据库
    try:
        import database as db
        db.init_db()
    except Exception as e:
        print(_c(f"  ⚠ 数据库初始化失败: {e}", C.YELLOW))

    # 自动启动 SQLite MCP（如果未运行）
    sqlite_port = int(os.getenv("SQLITE_MCP_PORT", "17231"))
    if not _check_port(sqlite_port):
        print(_c(f"  ▶ 自动启动 SQLite MCP 服务（端口 {sqlite_port}）...", C.CYAN))
        _start_mcp(
            "sqlite_mcp",
            [sys.executable, "-m", "mcp_sqlite.sqlite_mcp_server"],
            cwd=_THIS_DIR,
        )
        import time; time.sleep(1)

    # 检查 MCP 状态
    _print_mcp_status()

    while True:
        try:
            raw = _read_input(_c("你 (Shift+Enter换行): ", C.GREEN)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not raw:
            continue

        # ── 命令解析 ────────────────────────────────────────────
        if raw.startswith("/"):
            parts = raw[1:].split(None, 1)
            cmd   = parts[0].lower() if parts else ""
            args  = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("h", "help"):
                _print_help()

            elif cmd in ("q", "quit", "exit"):
                print(_c("  再见！", C.MAGENTA))
                break

            elif cmd in ("c", "clear"):
                _agent = None
                print(_c("  ✓ 对话历史已清空", C.GREEN))

            elif cmd == "mcp":
                _print_mcp_status()

            elif cmd == "tools":
                _print_tools()

            elif cmd in ("r", "run", "start"):
                _start_all_mcp()

            elif cmd == "xhs":
                _print_xhs_status()

            elif cmd == "login":
                _handle_xhs_login()

            elif cmd == "logout":
                _handle_xhs_logout()

            elif cmd == "start-sqlite":
                sqlite_port = int(os.getenv("SQLITE_MCP_PORT", "17231"))
                if _check_port(sqlite_port):
                    print(_c(f"  ✓ SQLite MCP 已在 :{sqlite_port} 运行", C.GREEN))
                else:
                    print(_c(f"  ▶ 启动 SQLite MCP 服务（端口 {sqlite_port}）...", C.CYAN))
                    _start_mcp(
                        "sqlite_mcp",
                        [sys.executable, "-m", "mcp_sqlite.sqlite_mcp_server"],
                        cwd=_THIS_DIR,
                    )
                    # 重建 agent 以重新发现工具
                    _agent = None

            elif cmd == "start-xhs":
                xhs_port = int(os.getenv("XHS_MCP_PORT", "17232"))
                mcp_dir = _THIS_DIR.parent / "MCP"
                if _check_port(xhs_port):
                    print(_c(f"  ✓ XHS MCP 已在 :{xhs_port} 运行", C.GREEN))
                else:
                    print(_c(f"  ▶ 启动 XHS MCP 服务（端口 {xhs_port}）...", C.CYAN))
                    _start_mcp(
                        "xhs_mcp",
                        [sys.executable, "main.py"],
                        cwd=mcp_dir,
                    )
                    _agent = None

            elif cmd == "stop":
                _stop_all()
                _agent = None

            elif cmd == "posts":
                _handle_posts()

            elif cmd == "drafts":
                _handle_posts(status="draft")

            elif cmd == "analyze":
                _handle_analyze(args)

            elif cmd == "plan":
                _stream_chat("请为我制定本周（7天）的小红书内容发布计划，包括主题、发布时间、预期方向。")

            elif cmd == "gen":
                if not args:
                    print(_c("  用法: /gen <主题>  例如: /gen 健身早餐", C.YELLOW))
                else:
                    _stream_chat(f"请为我生成一篇关于「{args}」的小红书爆款帖子，包括标题、正文和标签。")

            elif cmd == "img":
                _handle_img(args)

            elif cmd == "todo":
                _handle_todo(args)

            elif cmd == "proj":
                _handle_proj(args)

            else:
                print(_c(f"  未知命令: /{cmd}  （输入 /h 查看帮助）", C.YELLOW))

        else:
            # 普通消息 → Agent 对话
            _stream_chat(raw)

    # 退出时停止后台服务
    _stop_all()


if __name__ == "__main__":
    main()
