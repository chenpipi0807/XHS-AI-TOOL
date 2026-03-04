"""
Tool/agent/kimi_agent.py
Kimi Agent — 小红书运营 AI 核心

特性：
  - 模型：kimi-k2-5（Moonshot，通过 OpenAI 兼容端点调用）
  - API 端点：https://api.moonshot.cn/v1（OpenAI SDK 格式）
  - 自动发现并绑定 MCP 工具（XHS MCP + SQLite MCP）
  - SSE 流式输出供前端实时渲染
  - 支持 Function Calling 循环（多轮工具调用→结果→继续思考）
  - 任务历史持久化到 SQLite
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Generator, Iterator, Optional

# ── 路径初始化 ─────────────────────────────────────────────────────────────
_tool_dir = Path(__file__).resolve().parent.parent
if str(_tool_dir) not in sys.path:
    sys.path.insert(0, str(_tool_dir))

from dotenv import load_dotenv
load_dotenv(_tool_dir / ".env", override=False)

# ── 第三方依赖 ─────────────────────────────────────────────────────────────
try:
    from openai import OpenAI
except ImportError:
    raise ImportError("请安装 openai>=1.0: pip install openai")

# ── 本地模块 ───────────────────────────────────────────────────────────────
import database as db

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
# 配置常量
# ════════════════════════════════════════════════════════════════════════════

KIMI_API_KEY      = os.getenv("KIMI_API_KEY", "")
KIMI_API_BASE     = os.getenv("KIMI_API_BASE_URL", "https://api.moonshot.cn/v1")
KIMI_MODEL        = os.getenv("KIMI_MODEL", "kimi-k2-5")
XHS_MCP_URL       = os.getenv("XHS_MCP_URL", "http://localhost:17232/mcp/")
SQLITE_MCP_PORT   = int(os.getenv("SQLITE_MCP_PORT", "17231"))
SQLITE_MCP_URL    = f"http://localhost:{SQLITE_MCP_PORT}/mcp/"

# ── Skills 目录（自动加载所有 .md 文件）────────────────────────────────────
_SKILLS_DIR = _tool_dir / "skills"


def _load_skills() -> str:
    """加载 Tool/skills/ 目录下所有 .md skill 文件，拼接为字符串。"""
    if not _SKILLS_DIR.exists():
        return ""
    parts = []
    for md_file in sorted(_SKILLS_DIR.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"## Skill: {md_file.stem}\n\n{content}")
        except Exception as e:
            logger.warning(f"加载 skill 文件失败 {md_file}: {e}")
    return "\n\n---\n\n".join(parts)


# ════════════════════════════════════════════════════════════════════════════
# 系统提示构建
# ════════════════════════════════════════════════════════════════════════════

def _build_system_prompt(current_time: str = "", project_context: str = "") -> str:
    """构建 Kimi Agent 的系统提示词（含 Skills）"""
    skills_text = _load_skills()
    skills_section = f"\n\n# 技能手册\n\n{skills_text}" if skills_text else ""

    return f"""你是一位专业的小红书内容运营 AI 助手，擅长创作 LoL（英雄联盟）漫画内容并管理发布流程。

## 核心能力
- 生成 LoL 英雄漫画图片（调用 generate_image 工具）
- 管理本地项目文件（write_file / read_file / list_project_files）
- 创建和管理小红书帖子草稿（create_draft_post / update_post_content）
- 查询发布数据和分析（query_posts / query_analytics）
- 维护任务清单（create_todo / list_todos / update_todo）

## 工具使用原则
1. **主动使用工具**：收到生图、存文件、查数据等请求时，立即调用对应工具，不要只用文字描述
2. **逐步执行**：复杂任务拆分为多个工具调用步骤
3. **文件路径规范**：图片存到 `projects/lol_comic/ep02/` 等项目子目录
4. **错误处理**：工具调用失败时说明原因并尝试替代方案

## 当前状态
- 当前时间：{current_time}
- 项目上下文：{project_context}{skills_section}"""


# ════════════════════════════════════════════════════════════════════════════
# MCP 客户端（HTTP JSON-RPC）
# ════════════════════════════════════════════════════════════════════════════

class MCPClient:
    """
    轻量级 MCP HTTP 客户端，通过 JSON-RPC 2.0 over HTTP 与 MCP 服务器通信。
    兼容标准 MCP over HTTP (StreamableHTTP transport)。
    """

    def __init__(self, base_url: str, server_name: str = "mcp"):
        # 确保 URL 末尾带斜杠，避免 Starlette Mount 对无斜杠路径返回 307 重定向
        # urllib.request 不跟随 POST 的 307，会导致工具发现失败（_tools=[]）
        self.base_url = base_url.rstrip("/") + "/"
        self.server_name = server_name
        self._tools_cache: list[dict] | None = None

    def _post(self, method: str, params: dict, timeout: int = 30) -> dict:
        """
        发送 JSON-RPC 请求到 MCP StreamableHTTP 服务器。

        MCP 服务器配置了 json_response=True，响应为标准 HTTP JSON（带 Content-Length），
        urllib.read() 可以直接返回完整响应，无需处理 SSE 流。

        请求头须包含 Accept: application/json, text/event-stream（MCP 协议要求），
        但实际响应为 Content-Type: application/json 的完整 JSON 对象。
        """
        import urllib.request

        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": method,
            "params": params,
        }
        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.base_url,
                data=req_data,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw)
        except Exception as e:
            logger.debug(f"[MCPClient:{self.server_name}] POST 失败: {e}")
            raise

    def list_tools(self) -> list[dict]:
        if self._tools_cache is not None:
            return self._tools_cache
        try:
            result = self._post("tools/list", {})
            tools = result.get("result", {}).get("tools", [])
            self._tools_cache = tools
            return tools
        except Exception as e:
            logger.debug(f"[MCPClient:{self.server_name}] list_tools 失败: {e}")
            return []

    # 耗时较长的工具，使用更大的超时时间（300秒）
    # XHS 所有工具首次调用会触发 Playwright 浏览器冷启动（30-60秒），需要更长超时
    _SLOW_TOOLS = {
        # 图像生成
        "generate_image",
        # XHS 登录/浏览器类（首次调用冷启动 Chromium，耗时最长）
        "check_login_status", "get_login_qrcode", "delete_cookies",
        # XHS 发布类（Playwright 操作，耗时较长）
        "publish_content", "publish_with_video",
        # XHS 内容浏览类（需要导航页面）
        "list_feeds", "search_feeds", "get_feed_detail",
        # XHS 互动类
        "post_comment_to_feed", "reply_comment_in_feed",
        "like_feed", "favorite_feed",
        # XHS 用户信息类
        "user_profile", "get_my_profile",
    }

    def call_tool(self, name: str, arguments: dict) -> str:
        timeout = 300 if name in self._SLOW_TOOLS else 60
        result = self._post("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        content = result.get("result", {}).get("content", [])
        if isinstance(content, list):
            texts = [c.get("text", "") for c in content if isinstance(c, dict)]
            return "\n".join(texts)
        return str(content)


# ════════════════════════════════════════════════════════════════════════════
# Kimi Agent（OpenAI SDK + Function Calling）
# ════════════════════════════════════════════════════════════════════════════

class KimiAgent:
    """
    Kimi Agent（OpenAI 兼容协议 + Function Calling）
    - API 端点：https://api.moonshot.cn/v1（OpenAI 兼容）
    - 模型：kimi-k2-5（支持 function calling）
    - 自动发现 XHS + SQLite MCP 工具
    - 支持流式 SSE 输出
    - 持久化消息历史
    """

    def __init__(
        self,
        project_id: Optional[int] = None,
        task_id: Optional[int] = None,
        max_tool_calls: int = 20,
    ):
        self.project_id = project_id
        self.task_id = task_id
        self.max_tool_calls = max_tool_calls

        # OpenAI 兼容客户端（指向 Kimi API）
        self.client = OpenAI(
            api_key=KIMI_API_KEY,
            base_url=KIMI_API_BASE,
        )

        # MCP 客户端
        self.xhs_mcp    = MCPClient(XHS_MCP_URL, server_name="xhs")
        self.sqlite_mcp = MCPClient(SQLITE_MCP_URL, server_name="sqlite")

        # 工具注册表（OpenAI function calling 格式）
        self._tools: list[dict] = []                   # OpenAI tool 格式
        self._tool_server: dict[str, MCPClient] = {}   # 工具名 → 客户端

        # 消息历史（内存）—— OpenAI 格式
        self._messages: list[dict] = []
        self._system_prompt: str = ""
        self._initialized = False

    # ── 初始化 ────────────────────────────────────────────────────────────

    def _init(self):
        """懒初始化：加载 MCP 工具 + 构建系统提示"""
        if self._initialized:
            return

        # 加载项目上下文
        project_ctx = ""
        if self.project_id:
            try:
                ctx = db.get_project_context(self.project_id)
                project_ctx = json.dumps(ctx, ensure_ascii=False, default=str)
            except Exception as e:
                logger.warning(f"获取项目上下文失败: {e}")

        # 构建系统提示
        from datetime import datetime
        self._system_prompt = _build_system_prompt(
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
            project_context=project_ctx or "（未选择项目）",
        )

        # 加载历史消息（如果有 task_id）
        if self.task_id:
            try:
                history = db.get_messages(self.task_id)
                for msg in history:
                    if msg.get("role") in ("user", "assistant"):
                        self._messages.append({
                            "role": msg["role"],
                            "content": msg["content"] or "",
                        })
            except Exception as e:
                logger.warning(f"加载消息历史失败: {e}")

        # 发现 MCP 工具
        self._discover_tools()
        self._initialized = True

    def _discover_tools(self):
        """从两个 MCP 服务发现工具，转换为 OpenAI function calling 格式"""
        # 检查 DB 中禁用的工具
        disabled_tools: set[str] = set()
        try:
            db_tools = db.get_mcp_tools()
            disabled_tools = {t["name"] for t in db_tools if not t.get("enabled", True)}
        except Exception:
            pass

        for mcp_client in [self.sqlite_mcp, self.xhs_mcp]:
            try:
                raw_tools = mcp_client.list_tools()
            except Exception as e:
                logger.debug(f"[KimiAgent] {mcp_client.server_name} MCP 不可达，跳过: {e}")
                raw_tools = []

            for tool in raw_tools:
                name = tool.get("name", "")
                if not name or name in disabled_tools:
                    continue

                # 转换为 OpenAI function calling 格式
                input_schema = tool.get("inputSchema", {"type": "object", "properties": {}})
                if not isinstance(input_schema, dict):
                    input_schema = {"type": "object", "properties": {}}
                if "type" not in input_schema:
                    input_schema["type"] = "object"

                # OpenAI function calling 格式
                tool_def = {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get("description", ""),
                        "parameters": input_schema,
                    },
                }
                self._tools.append(tool_def)
                self._tool_server[name] = mcp_client

        logger.info(f"[KimiAgent] 发现工具 {len(self._tools)} 个: {[t['function']['name'] for t in self._tools]}")

    # ── 工具执行 ──────────────────────────────────────────────────────────

    def _execute_tool(self, tool_name: str, arguments: dict) -> str:
        """执行 MCP 工具调用"""
        mcp = self._tool_server.get(tool_name)
        if not mcp:
            return json.dumps({"error": f"工具 {tool_name} 未找到"}, ensure_ascii=False)

        # 记录 MCP 调用
        try:
            db.record_mcp_call(tool_name=tool_name, arguments=arguments)
        except Exception:
            pass

        try:
            result = mcp.call_tool(tool_name, arguments)
            return result
        except Exception as e:
            error_msg = f"工具执行失败: {e}"
            logger.error(f"[KimiAgent] {error_msg}", exc_info=True)
            return json.dumps({"error": error_msg}, ensure_ascii=False)

    # ── 流式对话核心（OpenAI function calling）────────────────────────────

    def chat_stream(
        self,
        message: str,
        task_id: Optional[int] = None,
    ) -> Generator[dict, None, None]:
        """
        发送消息，返回 SSE chunk 生成器。

        每个 chunk 是一个 dict，格式：
          {"type": "text",        "content": "...", "delta": "..."}
          {"type": "tool_start",  "name": "...", "arguments": {...}}
          {"type": "tool_result", "name": "...", "result": "..."}
          {"type": "error",       "content": "..."}
          {"type": "done",        "content": "完整回复", "task_id": ...}
        """
        if task_id:
            self.task_id = task_id

        self._init()

        # 添加用户消息
        self._messages.append({"role": "user", "content": message})
        if self.task_id:
            try:
                db.save_message(self.task_id, "user", message)
            except Exception:
                pass

        full_response = ""
        tool_call_count = 0

        try:
            while tool_call_count < self.max_tool_calls:
                # ── 构建请求参数 ────────────────────────────────────────
                # 系统消息放在 messages 列表最前面（OpenAI 格式）
                messages_with_system = [
                    {"role": "system", "content": self._system_prompt},
                ] + self._messages

                create_kwargs: dict[str, Any] = {
                    "model": KIMI_MODEL,
                    "max_tokens": 8192,
                    "messages": messages_with_system,
                    "stream": True,
                }
                if self._tools:
                    create_kwargs["tools"] = self._tools
                    create_kwargs["tool_choice"] = "auto"

                # ── 流式收集响应 ────────────────────────────────────────
                assistant_text = ""
                assistant_reasoning = ""   # 收集 thinking/reasoning_content
                # tool_calls 收集：call_id → {id, name, arguments_str}
                tool_calls_map: dict[int, dict] = {}
                finish_reason = ""

                stream = self.client.chat.completions.create(**create_kwargs)

                for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    finish_reason = choice.finish_reason or finish_reason

                    # 收集 reasoning_content（kimi-k2 thinking 模式）
                    raw_delta = delta.model_extra if hasattr(delta, "model_extra") else {}
                    if raw_delta:
                        rc = raw_delta.get("reasoning_content") or ""
                        if rc:
                            assistant_reasoning += rc

                    # 文本内容
                    if delta.content:
                        assistant_text += delta.content
                        full_response += delta.content
                        yield {
                            "type": "text",
                            "delta": delta.content,
                            "content": assistant_text,
                        }

                    # function calling 增量
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_calls_map:
                                tool_calls_map[idx] = {
                                    "id": tc_delta.id or f"call_{idx}",
                                    "name": "",
                                    "arguments_str": "",
                                }
                            if tc_delta.id:
                                tool_calls_map[idx]["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    tool_calls_map[idx]["name"] += tc_delta.function.name
                                if tc_delta.function.arguments:
                                    tool_calls_map[idx]["arguments_str"] += tc_delta.function.arguments

                # ── 无工具调用 → 对话结束 ──────────────────────────────
                if not tool_calls_map or finish_reason == "stop":
                    final_msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": assistant_text,
                    }
                    if assistant_reasoning:
                        final_msg["reasoning_content"] = assistant_reasoning
                    self._messages.append(final_msg)
                    if self.task_id and assistant_text:
                        try:
                            db.save_message(self.task_id, "assistant", assistant_text)
                        except Exception:
                            pass
                    break

                # ── 有工具调用 ─────────────────────────────────────────
                # 构建 OpenAI 格式的 assistant 消息（含 tool_calls）
                tool_calls_list = []
                for idx in sorted(tool_calls_map.keys()):
                    tc = tool_calls_map[idx]
                    tool_calls_list.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments_str"],
                        },
                    })

                # 把 assistant 消息（含 tool_calls）加入历史
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_text or None,
                    "tool_calls": tool_calls_list,
                }
                # kimi-k2 thinking 模式：必须带上 reasoning_content，否则下一轮 400
                if assistant_reasoning:
                    assistant_msg["reasoning_content"] = assistant_reasoning
                self._messages.append(assistant_msg)

                # 执行所有工具，收集结果
                for tc in tool_calls_list:
                    tool_name = tc["function"]["name"]
                    tool_call_count += 1

                    # 解析参数
                    try:
                        arguments = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        arguments = {}

                    # 通知前端：工具开始
                    yield {
                        "type": "tool_start",
                        "name": tool_name,
                        "arguments": arguments,
                        "call_id": tc["id"],
                    }

                    # 执行工具
                    result_text = self._execute_tool(tool_name, arguments)

                    # 通知前端：工具结果
                    yield {
                        "type": "tool_result",
                        "name": tool_name,
                        "result": result_text,
                        "call_id": tc["id"],
                    }

                    # 把工具结果加入消息历史（OpenAI 格式：role=tool）
                    self._messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_text,
                    })

                # 继续循环，让 Kimi 处理工具结果
                continue

            else:
                # 达到最大工具调用次数
                yield {"type": "error", "content": f"已达到最大工具调用次数（{self.max_tool_calls}），对话终止"}

        except Exception as e:
            logger.error(f"[KimiAgent] chat_stream 异常: {e}", exc_info=True)
            yield {"type": "error", "content": str(e)}

        finally:
            # 发送完成事件
            yield {
                "type": "done",
                "content": full_response,
                "task_id": self.task_id,
                "tool_call_count": tool_call_count,
            }

    # ── 非流式接口（供后台任务使用）──────────────────────────────────────

    def chat(self, message: str, task_id: Optional[int] = None) -> str:
        """非流式对话，返回完整回复文本"""
        full = ""
        for chunk in self.chat_stream(message=message, task_id=task_id):
            if chunk.get("type") == "done":
                full = chunk.get("content", "")
        return full

    # ── 快捷方法：内容生成 ────────────────────────────────────────────────

    def generate_post_content(
        self,
        topic: str,
        style: str = "日常分享",
        target_audience: str = "都市女性",
        keywords: list[str] | None = None,
    ) -> Generator[dict, None, None]:
        """生成小红书帖子文案"""
        keywords_str = "、".join(keywords) if keywords else "无"
        prompt = (
            f"请为我创作一篇小红书笔记：\n"
            f"主题：{topic}\n"
            f"风格：{style}\n"
            f"目标受众：{target_audience}\n"
            f"关键词：{keywords_str}\n\n"
            f"请输出：\n"
            f"1. 【标题】（带 emoji，吸引眼球）\n"
            f"2. 【正文】（口语化，分段，多 emoji，结尾引导互动）\n"
            f"3. 【标签】（5-8个，精准+热门组合）\n"
        )
        return self.chat_stream(prompt)

    def analyze_account(self, days: int = 7) -> Generator[dict, None, None]:
        """分析账号近期表现"""
        prompt = (
            f"请分析我的小红书账号近 {days} 天的表现，包括：\n"
            f"1. 整体数据趋势（使用 query_analytics 工具）\n"
            f"2. 最佳内容识别（热度最高的帖子）\n"
            f"3. 互动率分析\n"
            f"4. 改进建议（至少 3 条）\n"
            f"请用数据说话，给出具体可执行的建议。"
        )
        return self.chat_stream(prompt)

    def plan_weekly_content(self) -> Generator[dict, None, None]:
        """规划本周内容发布计划"""
        prompt = (
            "请根据当前项目数据和账号情况，为我规划本周的内容发布计划：\n"
            "1. 分析当前库存草稿（使用 query_posts 工具）\n"
            "2. 确定本周发布主题（3-5篇）\n"
            "3. 给出最佳发布时间（结合历史数据）\n"
            "4. 每篇内容的核心卖点\n"
            "输出格式：Markdown 表格 + 每篇内容简介"
        )
        return self.chat_stream(prompt)


# ════════════════════════════════════════════════════════════════════════════
# 便捷工厂函数
# ════════════════════════════════════════════════════════════════════════════

def create_agent(project_id: Optional[int] = None, task_id: Optional[int] = None) -> KimiAgent:
    """创建 KimiAgent 实例"""
    return KimiAgent(project_id=project_id, task_id=task_id)


# ── 测试入口 ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agent = KimiAgent(project_id=1)
    print(f"模型: {KIMI_MODEL}")
    print(f"API: {KIMI_API_BASE}")
    print("测试对话（非流式）:")
    resp = agent.chat("你好！简单介绍一下你自己和你的能力。")
    print(resp)
