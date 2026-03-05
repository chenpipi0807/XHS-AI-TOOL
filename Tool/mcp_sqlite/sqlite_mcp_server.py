"""
Tool/mcp_sqlite/sqlite_mcp_server.py
SQLite MCP 服务器 — 供 Kimi K2.5 Agent 直接读写本地数据库

提供 15 个 MCP 工具：
  ── 数据库读写 ──────────────────────────────────────────
  1.  query_posts          — 查询笔记列表（按项目/状态/关键词）
  2.  get_post_detail      — 获取单篇笔记详情（含最新分析数据）
  3.  query_analytics      — 查询帖子热度分析快照（1h/6h/24h/72h）
  4.  query_images         — 查询生成的图片列表
  5.  save_agent_insight   — 保存 Agent 产生的分析/洞察到 DB
  6.  create_draft_post    — 创建草稿笔记
  7.  update_post_content  — 更新笔记的标题/正文/标签
  ── 图片生成 ────────────────────────────────────────────
  8.  generate_image       — 调用 Gemini 生成图片并保存到项目目录
  ── 本地文件操作 ────────────────────────────────────────
  9.  write_file           — 写入文本文件到项目目录（.md/.json/.txt）
  10. read_file            — 读取项目目录中的文件内容
  11. list_project_files   — 列出项目目录下的文件（含绝对路径）
  12. list_dir_tree        — 递归列出目录树结构（类似 tree /f）
  ── Todo 管理 ───────────────────────────────────────────
  13. create_todo          — 创建 Todo 项
  14. list_todos           — 查询 Todo 列表
  15. update_todo          — 更新 Todo 状态/内容

启动方式：
  python -m Tool.mcp_sqlite.sqlite_mcp_server
或：
  cd Tool && python mcp_sqlite/sqlite_mcp_server.py

端口：SQLITE_MCP_PORT 环境变量（默认 8001）
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# 把 Tool/ 加入路径
_tool_dir = Path(__file__).resolve().parent.parent
if str(_tool_dir) not in sys.path:
    sys.path.insert(0, str(_tool_dir))

from dotenv import load_dotenv
load_dotenv(_tool_dir / ".env", override=False)

import database as db

from mcp.server import Server
from mcp.types import (
    TextContent,
    Tool,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Mount, Route
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SQLite-MCP] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── MCP 服务器实例 ────────────────────────────────────────────────
app = Server("xhs-sqlite-mcp")


def _ok(data: Any) -> list[TextContent]:
    """返回成功结果。MCP SDK 要求 call_tool handler 返回 Iterable[TextContent]。"""
    return [TextContent(type="text", text=json.dumps(data, ensure_ascii=False, default=str))]


def _err(msg: str) -> None:
    """抛出业务错误。SDK 会将未捕获异常封装为 isError=True 的 CallToolResult。"""
    raise RuntimeError(json.dumps({"error": msg}, ensure_ascii=False))


# ── 工具定义 ──────────────────────────────────────────────────────
@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="query_posts",
            description=(
                "查询小红书笔记列表。可按项目、状态、关键词过滤，支持分页。"
                "返回字段：id, title, status, tags, heat_score, likes_count, "
                "comments_count, favorites_count, shares_count, published_at, updated_at"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "项目 ID（不传则查全部）"},
                    "status": {"type": "string", "enum": ["draft", "published", "all"], "description": "状态过滤，默认 all"},
                    "keyword": {"type": "string", "description": "标题/内容关键词搜索"},
                    "limit":  {"type": "integer", "description": "返回条数，默认 20，最大 100"},
                    "offset": {"type": "integer", "description": "分页偏移，默认 0"},
                    "order_by": {"type": "string", "enum": ["heat_score", "published_at", "updated_at", "created_at"],
                                 "description": "排序字段，默认 updated_at DESC"},
                },
            },
        ),
        Tool(
            name="get_post_detail",
            description=(
                "获取单篇笔记的完整详情，包括：标题、正文、标签、所有分析快照（时间序列数据）。"
                "适合分析某篇笔记的增长趋势。"
            ),
            inputSchema={
                "type": "object",
                "required": ["post_id"],
                "properties": {
                    "post_id": {"type": "integer", "description": "笔记 ID"},
                    "include_analytics": {"type": "boolean", "description": "是否包含分析快照，默认 true"},
                },
            },
        ),
        Tool(
            name="query_analytics",
            description=(
                "查询笔记的热度分析快照序列，每个时间点包含：likes/comments/favorites/shares/heat_score。"
                "可用于分析内容在发布后 1h/6h/24h/72h 的涨粉速度和互动情况。"
            ),
            inputSchema={
                "type": "object",
                "required": ["post_id"],
                "properties": {
                    "post_id": {"type": "integer", "description": "笔记 ID"},
                    "limit":   {"type": "integer", "description": "返回最近 N 条快照，默认 10"},
                },
            },
        ),
        Tool(
            name="query_images",
            description=(
                "查询已生成的图片列表，包括提示词、模型、宽高比、本地路径等信息。"
                "可按项目过滤，用于了解已有的图片资产。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "项目 ID（不传则查全部）"},
                    "keyword":    {"type": "string",  "description": "按提示词关键词搜索"},
                    "limit":      {"type": "integer", "description": "返回条数，默认 20"},
                    "offset":     {"type": "integer", "description": "分页偏移，默认 0"},
                },
            },
        ),
        Tool(
            name="save_agent_insight",
            description=(
                "将 Agent 产生的分析结论、运营建议、内容洞察保存到数据库中，"
                "关联到对应的任务或项目，方便后续追溯。"
            ),
            inputSchema={
                "type": "object",
                "required": ["content", "insight_type"],
                "properties": {
                    "task_id":      {"type": "integer", "description": "关联的 Agent 任务 ID（可选）"},
                    "project_id":   {"type": "integer", "description": "关联的项目 ID（可选）"},
                    "insight_type": {"type": "string",
                                     "enum": ["content_analysis", "trend_analysis", "strategy", "todo", "observation"],
                                     "description": "洞察类型"},
                    "content":      {"type": "string", "description": "洞察内容（Markdown 格式）"},
                    "metadata":     {"type": "object", "description": "附加元数据（任意 JSON）"},
                },
            },
        ),
        Tool(
            name="create_draft_post",
            description=(
                "创建一篇新的草稿笔记，可指定标题、正文、话题标签。"
                "Kimi 可用此工具直接生成内容草稿供人工审核后发布。"
            ),
            inputSchema={
                "type": "object",
                "required": ["title"],
                "properties": {
                    "project_id": {"type": "integer", "description": "项目 ID"},
                    "title":      {"type": "string",  "description": "笔记标题（最长 100 字）"},
                    "content":    {"type": "string",  "description": "笔记正文（Markdown）"},
                    "tags":       {"type": "string",  "description": "话题标签，逗号分隔，例如：AI绘画,小红书运营"},
                    "source":     {"type": "string",  "description": "来源标注（默认 kimi_agent）"},
                },
            },
        ),
        Tool(
            name="update_post_content",
            description=(
                "更新已有笔记的标题、正文或话题标签。"
                "Kimi 可用此工具对内容进行 AI 优化后写回数据库。"
            ),
            inputSchema={
                "type": "object",
                "required": ["post_id"],
                "properties": {
                    "post_id": {"type": "integer", "description": "笔记 ID"},
                    "title":   {"type": "string",  "description": "新标题（不传则不修改）"},
                    "content": {"type": "string",  "description": "新正文（不传则不修改）"},
                    "tags":    {"type": "string",  "description": "新标签，逗号分隔（不传则不修改）"},
                },
            },
        ),
        # ── 图片生成 ─────────────────────────────────────────────────────────
        Tool(
            name="generate_image",
            description=(
                "调用 Gemini 3.1 Flash Image 模型生成图片，自动保存到 projects/ 目录。"
                "返回本地文件路径列表，可直接用于笔记发布。"
                "小红书漫画必须使用 aspect_ratio=9:16（竖版）。"
                "用 path 参数指定保存文件名，例如 lol_comic/ep02/page_01.png。"
                "【重要】生成角色图片时，务必通过 ref_image_paths 传入 IP_REF 目录下对应角色的参考图，"
                "最多可传 20 张，Gemini 会以参考图为基准生成风格一致的角色。"
                "参考图绝对路径可通过 list_project_files 或 list_dir_tree 查询 IP_REF 子目录获取。"
            ),
            inputSchema={
                "type": "object",
                "required": ["prompt"],
                "properties": {
                    "prompt":          {"type": "string",  "description": "图片生成提示词（英文），末尾必须包含 aspect ratio 9:16, portrait orientation, vertical"},
                    "path":            {"type": "string",  "description": "保存路径（相对于 projects/），例如 lol_comic/ep02/page_01.png。不传则自动命名。"},
                    "project_id":      {"type": "integer", "description": "关联项目 ID（可选）"},
                    "aspect_ratio":    {"type": "string",  "enum": ["1:1","16:9","9:16","4:3","3:4"],
                                       "description": "宽高比，小红书漫画必须用 9:16"},
                    "count":           {"type": "integer", "description": "生成数量，默认 1，最多 4（指定 path 时仅生成 1 张）"},
                    "post_id":         {"type": "integer", "description": "关联笔记 ID（可选）"},
                    "ref_image_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "参考图绝对路径列表（最多 20 张）。"
                            "生成漫画角色时必须传入，路径指向 IP_REF 目录下对应角色的参考图。"
                            "例如：[\"/abs/path/to/projects/IP_REF/xiaopalu_main_character_0.png\"]。"
                            "可先用 list_dir_tree(subdir='IP_REF') 查询所有参考图的绝对路径。"
                        ),
                    },
                },
            },
        ),
        # ── 本地文件操作 ──────────────────────────────────────────────────────
        Tool(
            name="write_file",
            description=(
                "将文本内容写入项目目录下的文件（支持 .md / .json / .txt）。"
                "路径限制在 projects/ 目录内，防止越权写入系统文件。"
                "用法示例：写文案草稿到 projects/美食博主/posts/2024-01-健康早餐.md，"
                "或写图片路径清单到 projects/美食博主/images/manifest.json。"
            ),
            inputSchema={
                "type": "object",
                "required": ["path", "content"],
                "properties": {
                    "path":    {"type": "string", "description": "相对于 projects/ 的文件路径，例如 美食博主/posts/健康早餐.md"},
                    "content": {"type": "string", "description": "写入的文本内容（Markdown / JSON / 纯文本）"},
                    "append":  {"type": "boolean", "description": "是否追加模式（默认 false = 覆盖）"},
                },
            },
        ),
        Tool(
            name="read_file",
            description=(
                "读取项目目录下的文件内容。"
                "路径限制在 projects/ 目录内。"
            ),
            inputSchema={
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string", "description": "相对于 projects/ 的文件路径"},
                },
            },
        ),
        Tool(
            name="list_project_files",
            description=(
                "列出项目目录下的所有文件（支持按扩展名过滤）。"
                "返回字段包含 path（相对路径）、abs_path（绝对路径，可直接用于 publish_content）、"
                "name、size_bytes、modified。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "subdir": {"type": "string",  "description": "子目录，例如 lol_comic/ep02（不传则列出 projects/ 根目录）"},
                    "ext":    {"type": "string",  "description": "扩展名过滤，例如 .png 或 .md（不传则列出全部）"},
                    "limit":  {"type": "integer", "description": "最多返回文件数，默认 50"},
                },
            },
        ),
        Tool(
            name="list_dir_tree",
            description=(
                "递归列出指定目录的树形结构（类似 Windows tree /f 命令）。"
                "返回目录树文本和文件列表（含绝对路径）。"
                "用于了解项目目录结构、确认文件名和路径，避免猜测文件名。"
                "不传 subdir 则列出整个 projects/ 目录。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "subdir":    {"type": "string",  "description": "子目录，例如 lol_comic/ep02（不传则列出 projects/ 根目录）"},
                    "max_depth": {"type": "integer", "description": "最大递归深度，默认 5（0 表示不限制）"},
                    "files_only":{"type": "boolean", "description": "仅显示文件（不含目录节点），默认 false"},
                },
            },
        ),
        # ── Todo 管理 ─────────────────────────────────────────────────────────
        Tool(
            name="create_todo",
            description=(
                "创建一个 Todo 待办项，可关联到项目或 Agent 任务。"
                "Kimi 可用此工具记录需要人工完成的事项或自己的下一步计划。"
            ),
            inputSchema={
                "type": "object",
                "required": ["title"],
                "properties": {
                    "title":       {"type": "string",  "description": "Todo 标题"},
                    "description": {"type": "string",  "description": "详细说明（可选）"},
                    "project_id":  {"type": "integer", "description": "关联项目 ID（可选）"},
                    "task_id":     {"type": "integer", "description": "关联 Agent 任务 ID（可选）"},
                    "priority":    {"type": "integer", "enum": [0, 1, 2],
                                    "description": "优先级：0=普通, 1=高, 2=紧急（默认 0）"},
                    "due_date":    {"type": "string",  "description": "截止日期，格式 YYYY-MM-DD（可选）"},
                    "tags":        {"type": "string",  "description": "标签，逗号分隔（可选）"},
                    "created_by":  {"type": "string",  "description": "创建者标识，默认 kimi_agent"},
                },
            },
        ),
        Tool(
            name="list_todos",
            description=(
                "查询 Todo 列表，支持按状态、项目、优先级过滤。"
                "返回待办项的 ID、标题、状态、优先级、截止日期等信息。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "按项目过滤（可选）"},
                    "status":     {"type": "string",
                                   "enum": ["pending", "in_progress", "done", "cancelled", "all"],
                                   "description": "状态过滤，默认 all"},
                    "priority":   {"type": "integer", "enum": [0, 1, 2],
                                   "description": "优先级过滤（可选）"},
                    "limit":      {"type": "integer", "description": "返回条数，默认 20"},
                },
            },
        ),
        Tool(
            name="update_todo",
            description=(
                "更新 Todo 项的状态、标题或描述。"
                "Kimi 完成某项任务后应调用此工具将对应 Todo 标记为 done。"
            ),
            inputSchema={
                "type": "object",
                "required": ["todo_id"],
                "properties": {
                    "todo_id":     {"type": "integer", "description": "Todo ID"},
                    "status":      {"type": "string",
                                    "enum": ["pending", "in_progress", "done", "cancelled"],
                                    "description": "新状态"},
                    "title":       {"type": "string",  "description": "新标题（可选）"},
                    "description": {"type": "string",  "description": "新描述（可选）"},
                },
            },
        ),
    ]


# ── 工具执行 ──────────────────────────────────────────────────────
@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """
    MCP SDK 要求此函数返回 Iterable[TextContent]，SDK 内部会将其封装为 CallToolResult。
    业务错误通过 _err() raise RuntimeError，SDK 会捕获并设置 isError=True。
    """
    logger.info(f"[call_tool] {name} args={json.dumps(arguments, ensure_ascii=False)[:200]}")
    if name == "query_posts":
        return _tool_query_posts(arguments)
    elif name == "get_post_detail":
        return _tool_get_post_detail(arguments)
    elif name == "query_analytics":
        return _tool_query_analytics(arguments)
    elif name == "query_images":
        return _tool_query_images(arguments)
    elif name == "save_agent_insight":
        return _tool_save_agent_insight(arguments)
    elif name == "create_draft_post":
        return _tool_create_draft_post(arguments)
    elif name == "update_post_content":
        return _tool_update_post_content(arguments)
    elif name == "generate_image":
        return _tool_generate_image(arguments)
    elif name == "write_file":
        return _tool_write_file(arguments)
    elif name == "read_file":
        return _tool_read_file(arguments)
    elif name == "list_project_files":
        return _tool_list_project_files(arguments)
    elif name == "list_dir_tree":
        return _tool_list_dir_tree(arguments)
    elif name == "create_todo":
        return _tool_create_todo(arguments)
    elif name == "list_todos":
        return _tool_list_todos(arguments)
    elif name == "update_todo":
        return _tool_update_todo(arguments)
    else:
        raise ValueError(f"未知工具: {name}")


# ── 工具实现 ──────────────────────────────────────────────────────

def _tool_query_posts(args: dict) -> list[TextContent]:
    db.init_db()
    project_id = args.get("project_id")
    status     = args.get("status", "all")
    keyword    = args.get("keyword", "")
    limit      = min(int(args.get("limit", 20)), 100)
    offset     = int(args.get("offset", 0))
    order_by   = args.get("order_by", "updated_at")

    allowed_orders = {"heat_score", "published_at", "updated_at", "created_at"}
    if order_by not in allowed_orders:
        order_by = "updated_at"

    conditions = []
    params = []

    if project_id:
        conditions.append("project_id = ?")
        params.append(project_id)
    if status and status != "all":
        conditions.append("status = ?")
        params.append(status)
    if keyword:
        conditions.append("(title LIKE ? OR content LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT id, title, status, tags, heat_score,
               likes_count, comments_count, favorites_count, shares_count,
               published_at, updated_at, created_at, xhs_url, project_id
        FROM posts
        {where}
        ORDER BY {order_by} DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    rows = db.query_all(sql, params)

    # 总数
    count_sql = f"SELECT COUNT(*) as cnt FROM posts {where}"
    total_row = db.query_one(count_sql, params[:-2])
    total = total_row["cnt"] if total_row else 0

    return _ok({"posts": rows, "total": total, "limit": limit, "offset": offset})


def _tool_get_post_detail(args: dict) -> list[TextContent]:
    db.init_db()
    post_id          = int(args["post_id"])
    include_analytics = args.get("include_analytics", True)

    post = db.query_one("SELECT * FROM posts WHERE id = ?", [post_id])
    if not post:
        _err(f"笔记 ID {post_id} 不存在")

    result = {"post": dict(post)}

    if include_analytics:
        analytics = db.query_all(
            "SELECT * FROM post_analytics WHERE post_id = ? ORDER BY snapshot_time ASC",
            [post_id]
        )
        result["analytics"] = analytics

    return _ok(result)


def _tool_query_analytics(args: dict) -> list[TextContent]:
    db.init_db()
    post_id = int(args["post_id"])
    limit   = min(int(args.get("limit", 10)), 50)

    rows = db.query_all(
        """SELECT snapshot_time, hours_after_publish,
                  likes_count, comments_count, favorites_count, shares_count,
                  heat_score
           FROM post_analytics
           WHERE post_id = ?
           ORDER BY snapshot_time DESC
           LIMIT ?""",
        [post_id, limit]
    )

    # 计算增量
    enriched = []
    prev = None
    for row in reversed(rows):
        r = dict(row)
        if prev:
            r["likes_delta"]     = r["likes_count"]     - prev["likes_count"]
            r["comments_delta"]  = r["comments_count"]  - prev["comments_count"]
            r["favorites_delta"] = r["favorites_count"] - prev["favorites_count"]
            r["shares_delta"]    = r["shares_count"]    - prev["shares_count"]
        else:
            r["likes_delta"] = r["comments_delta"] = r["favorites_delta"] = r["shares_delta"] = 0
        enriched.append(r)
        prev = r

    return _ok({"post_id": post_id, "analytics": list(reversed(enriched)), "count": len(enriched)})


def _tool_query_images(args: dict) -> list[TextContent]:
    db.init_db()
    project_id = args.get("project_id")
    keyword    = args.get("keyword", "")
    limit      = min(int(args.get("limit", 20)), 100)
    offset     = int(args.get("offset", 0))

    conditions = []
    params = []
    if project_id:
        conditions.append("project_id = ?")
        params.append(project_id)
    if keyword:
        conditions.append("prompt LIKE ?")
        params.append(f"%{keyword}%")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = db.query_all(
        f"""SELECT id, prompt, model_name, aspect_ratio, local_path, url,
                   created_at, project_id
            FROM images {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset]
    )
    total_row = db.query_one(f"SELECT COUNT(*) as cnt FROM images {where}", params)
    return _ok({"images": rows, "total": total_row["cnt"] if total_row else 0})


def _tool_save_agent_insight(args: dict) -> list[TextContent]:
    db.init_db()
    task_id      = args.get("task_id")
    project_id   = args.get("project_id")
    insight_type = args.get("insight_type", "observation")
    content      = args.get("content", "")
    metadata     = args.get("metadata", {})

    if not content:
        _err("content 不能为空")

    # 保存为 agent_messages 记录（role=assistant，用于持久化洞察）
    msg_id = db.execute(
        """INSERT INTO agent_messages (task_id, role, content, metadata)
           VALUES (?, 'assistant', ?, ?)""",
        [task_id, f"[{insight_type}]\n\n{content}", json.dumps(metadata, ensure_ascii=False)]
    )

    # 如果有任务 ID，更新任务状态
    if task_id:
        db.execute(
            "UPDATE agent_tasks SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [task_id]
        )

    return _ok({
        "success": True,
        "message_id": msg_id,
        "insight_type": insight_type,
        "saved_chars": len(content),
    })


def _tool_create_draft_post(args: dict) -> list[TextContent]:
    db.init_db()
    title      = args.get("title", "").strip()
    content    = args.get("content", "").strip()
    tags       = args.get("tags", "").strip()
    project_id = args.get("project_id")
    source     = args.get("source", "kimi_agent")

    if not title:
        _err("title 不能为空")
    if len(title) > 100:
        _err("title 最长 100 字")

    post_id = db.execute(
        """INSERT INTO posts (project_id, title, content, tags, status, source)
           VALUES (?, ?, ?, ?, 'draft', ?)""",
        [project_id, title, content, tags, source]
    )

    return _ok({
        "success":  True,
        "post_id":  post_id,
        "title":    title,
        "status":   "draft",
        "message":  f"草稿已创建（ID: {post_id}），可在内容管理页面查看和编辑。",
    })


def _tool_update_post_content(args: dict) -> list[TextContent]:
    db.init_db()
    post_id = int(args["post_id"])
    title   = args.get("title")
    content = args.get("content")
    tags    = args.get("tags")

    if not any([title, content, tags]):
        _err("至少提供 title / content / tags 之一")

    post = db.query_one("SELECT id, status FROM posts WHERE id = ?", [post_id])
    if not post:
        _err(f"笔记 ID {post_id} 不存在")

    fields, params = [], []
    if title   is not None: fields.append("title = ?");   params.append(title)
    if content is not None: fields.append("content = ?"); params.append(content)
    if tags    is not None: fields.append("tags = ?");    params.append(tags)
    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(post_id)

    db.execute(f"UPDATE posts SET {', '.join(fields)} WHERE id = ?", params)

    return _ok({
        "success": True,
        "post_id": post_id,
        "updated_fields": [f for f in ["title", "content", "tags"]
                           if args.get(f) is not None],
        "message": f"笔记 ID {post_id} 已更新。",
    })


# ── 新增工具实现 ──────────────────────────────────────────────────

# projects/ 根目录（位于 Tool/data/projects/）
_PROJECTS_ROOT = _tool_dir / "data" / "projects"


def _safe_path(rel_path: str) -> Path | None:
    """
    将用户传入的相对路径解析为绝对路径，并验证其在 _PROJECTS_ROOT 内（防路径穿越）。
    返回 None 表示路径非法。
    """
    try:
        target = (_PROJECTS_ROOT / rel_path).resolve()
        if _PROJECTS_ROOT.resolve() in target.parents or target == _PROJECTS_ROOT.resolve():
            return target
        return None
    except Exception:
        return None


def _tool_generate_image(args: dict) -> list[TextContent]:
    prompt          = args.get("prompt", "").strip()
    rel_path        = args.get("path", "").strip()   # 用户指定的保存路径，如 lol_comic/ep02/page_01.png
    project_id      = args.get("project_id")
    aspect_ratio    = args.get("aspect_ratio", "9:16")
    post_id         = args.get("post_id")
    ref_image_paths = args.get("ref_image_paths") or []   # 参考图绝对路径列表（最多 20 张）

    # 有指定 path 时固定生成 1 张，否则按 count
    if rel_path:
        count = 1
    else:
        count = min(int(args.get("count", 1)), 4)

    if not prompt:
        _err("prompt 不能为空")

    # ── 详细日志：打印接收到的参考图路径 ────────────────────────────
    ref_log_lines = []
    ref_log_lines.append(f"[generate_image] 接收到 ref_image_paths 原始值: {repr(args.get('ref_image_paths'))}")

    # ── 校验参考图路径 ────────────────────────────────────────────────
    if ref_image_paths:
        if not isinstance(ref_image_paths, list):
            ref_image_paths = [str(ref_image_paths)]
        # 只保留存在的文件，最多 20 张
        valid_refs = []
        for p in ref_image_paths[:20]:
            p = str(p).strip()
            abs_p = os.path.abspath(p)
            exists = os.path.isfile(abs_p)
            size_info = f"{os.path.getsize(abs_p)} 字节" if exists else "不存在"
            log_line = f"  路径: {abs_p} | 存在={exists} | {size_info}"
            ref_log_lines.append(log_line)
            logger.info(f"[generate_image] 参考图校验: {log_line}")
            if exists:
                valid_refs.append(abs_p)
            else:
                logger.warning(f"[generate_image] 参考图不存在，跳过: {abs_p}")
        ref_image_paths = valid_refs
        ref_log_lines.append(f"[generate_image] 有效参考图 {len(ref_image_paths)} 张: {ref_image_paths}")
    else:
        ref_log_lines.append("[generate_image] 未传入参考图（纯文生图）")

    for line in ref_log_lines:
        logger.info(line)

    # ── 确定保存目录和文件名 ─────────────────────────────────────────
    if rel_path:
        # 用户指定了完整路径，如 lol_comic/ep02/page_01.png
        target_path = _safe_path(rel_path)
        if target_path is None:
            _err(f"路径不合法（必须在 projects/ 目录内）: {rel_path}")
        output_dir      = target_path.parent
        # output_filename 不含扩展名
        output_filename = target_path.stem
    else:
        # 自动保存到 projects/generated/
        output_dir      = _PROJECTS_ROOT / "generated"
        output_filename = None   # 让 gemini_image 自动命名

    try:
        from image.gemini_image import generate_image
        result = generate_image(
            prompt=prompt,
            ref_image_paths=ref_image_paths if ref_image_paths else None,
            output_dir=output_dir,
            output_filename=output_filename,
            aspect_ratio=aspect_ratio,
        )
    except Exception as e:
        _err(f"生图调用失败: {e}")

    if not result.get("success"):
        _err(result.get("error", "生图失败，未知错误"))

    # gemini_image.generate_image 返回的保存路径 key 是 "images"
    saved_paths: list[str] = result.get("images", [])

    if not saved_paths:
        _err("生图成功但未保存文件，请检查 output_dir 配置。image_data 长度: "
             + str(len(result.get("image_data", []))))

    # ── 写入 images 表 ───────────────────────────────────────────────
    db.init_db()
    image_ids = []
    for p in saved_paths:
        img_id = db.execute(
            """INSERT INTO images (project_id, post_id, model, prompt, output_paths,
                                   aspect_ratio, status)
               VALUES (?, ?, 'gemini', ?, ?, ?, 'generated')""",
            [project_id, post_id, prompt, json.dumps([p], ensure_ascii=False), aspect_ratio]
        )
        image_ids.append(img_id)

    # ── 汇总参考图日志（返回给 Kimi 可见）───────────────────────────
    ref_summary = "\n".join(ref_log_lines)

    return _ok({
        "success":         True,
        "saved_paths":     saved_paths,
        "image_ids":       image_ids,
        "count":           len(saved_paths),
        "aspect_ratio":    aspect_ratio,
        "ref_images_used": ref_image_paths,
        "ref_images_count": len(ref_image_paths),
        "ref_images_log":  ref_summary,
        "message":         (
            f"已生成 {len(saved_paths)} 张图片，保存路径: {saved_paths}\n"
            f"参考图使用情况（共 {len(ref_image_paths)} 张）:\n{ref_summary}"
        ),
    })


def _tool_write_file(args: dict) -> list[TextContent]:
    rel_path = args.get("path", "").strip()
    content  = args.get("content", "")
    append   = bool(args.get("append", False))

    if not rel_path:
        _err("path 不能为空")

    # 只允许 .md / .json / .txt 扩展名
    allowed_exts = {".md", ".json", ".txt", ".csv"}
    ext = Path(rel_path).suffix.lower()
    if ext not in allowed_exts:
        _err(f"不支持的文件类型 {ext}，仅允许：{', '.join(allowed_exts)}")

    target = _safe_path(rel_path)
    if target is None:
        _err(f"非法路径：{rel_path}，文件必须位于 projects/ 目录内")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(target, mode, encoding="utf-8") as f:
            f.write(content)
        size = target.stat().st_size
        return _ok({
            "success":    True,
            "path":       str(target),
            "rel_path":   rel_path,
            "size_bytes": size,
            "mode":       "append" if append else "overwrite",
            "message":    f"文件已{'追加' if append else '写入'}：{rel_path}（{size} 字节）",
        })
    except Exception as e:
        _err(f"写入文件失败: {e}")


def _tool_read_file(args: dict) -> list[TextContent]:
    rel_path = args.get("path", "").strip()
    if not rel_path:
        _err("path 不能为空")

    target = _safe_path(rel_path)
    if target is None:
        _err(f"非法路径：{rel_path}")

    if not target.exists():
        _err(f"文件不存在：{rel_path}")

    try:
        content = target.read_text(encoding="utf-8")
        return _ok({
            "success":    True,
            "path":       str(target),
            "rel_path":   rel_path,
            "content":    content,
            "size_bytes": len(content.encode("utf-8")),
        })
    except Exception as e:
        _err(f"读取文件失败: {e}")


def _tool_list_project_files(args: dict) -> list[TextContent]:
    subdir = args.get("subdir", "").strip()
    ext    = args.get("ext", "").strip().lower()
    limit  = min(int(args.get("limit", 50)), 200)

    if subdir:
        base = _safe_path(subdir)
        if base is None:
            _err(f"非法路径：{subdir}")
    else:
        base = _PROJECTS_ROOT

    _PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)

    if not base.exists():
        return _ok({"files": [], "total": 0, "base": str(base), "projects_root": str(_PROJECTS_ROOT)})

    files = []
    try:
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if ext and p.suffix.lower() != ext:
                continue
            rel = p.relative_to(_PROJECTS_ROOT)
            files.append({
                "path":       str(rel).replace("\\", "/"),
                "abs_path":   str(p.resolve()).replace("\\", "/"),
                "name":       p.name,
                "size_bytes": p.stat().st_size,
                "modified":   p.stat().st_mtime,
            })
            if len(files) >= limit:
                break
    except Exception as e:
        _err(f"列举文件失败: {e}")

    return _ok({
        "files":         files,
        "total":         len(files),
        "base":          str(base),
        "projects_root": str(_PROJECTS_ROOT),
    })


def _tool_list_dir_tree(args: dict) -> list[TextContent]:
    """
    递归列出目录树（类似 tree /f），返回：
    - tree_text: 可读的树形文本
    - files: 文件列表（含 abs_path）
    - dirs: 目录列表
    """
    subdir     = args.get("subdir", "").strip()
    max_depth  = int(args.get("max_depth", 5))
    files_only = bool(args.get("files_only", False))

    if subdir:
        base = _safe_path(subdir)
        if base is None:
            _err(f"非法路径：{subdir}")
            return []  # 实际不会到达，_err 会 raise
    else:
        base = _PROJECTS_ROOT

    _PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)

    if not base.exists():
        return _ok({"tree_text": f"{base} (不存在)", "files": [], "dirs": []})

    lines: list[str] = []
    all_files: list[dict] = []
    all_dirs:  list[str]  = []

    def _walk(path: Path, prefix: str, depth: int) -> None:
        if max_depth > 0 and depth > max_depth:
            return
        try:
            children = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return

        entries = list(children)
        for i, child in enumerate(entries):
            is_last = (i == len(entries) - 1)
            connector = "└── " if is_last else "├── "
            extension = "    " if is_last else "│   "

            if child.is_dir():
                rel = str(child.relative_to(_PROJECTS_ROOT)).replace("\\", "/")
                if not files_only:
                    lines.append(f"{prefix}{connector}📁 {child.name}/")
                all_dirs.append(rel)
                _walk(child, prefix + extension, depth + 1)
            else:
                rel = str(child.relative_to(_PROJECTS_ROOT)).replace("\\", "/")
                lines.append(f"{prefix}{connector}{child.name}")
                all_files.append({
                    "path":       rel,
                    "abs_path":   str(child.resolve()).replace("\\", "/"),
                    "name":       child.name,
                    "size_bytes": child.stat().st_size,
                })

    base_label = str(base.relative_to(_PROJECTS_ROOT)) if base != _PROJECTS_ROOT else "projects/"
    lines.append(f"📂 {base_label}")
    _walk(base, "", 1)

    tree_text = "\n".join(lines)

    return _ok({
        "tree_text":     tree_text,
        "files":         all_files,
        "dirs":          all_dirs,
        "file_count":    len(all_files),
        "dir_count":     len(all_dirs),
        "projects_root": str(_PROJECTS_ROOT).replace("\\", "/"),
    })


def _tool_create_todo(args: dict) -> list[TextContent]:
    db.init_db()
    title       = args.get("title", "").strip()
    description = args.get("description", "")
    project_id  = args.get("project_id")
    task_id     = args.get("task_id")
    priority    = int(args.get("priority", 0))
    due_date    = args.get("due_date", "")
    tags        = args.get("tags", "")
    created_by  = args.get("created_by", "kimi_agent")

    if not title:
        _err("title 不能为空")
    if priority not in (0, 1, 2):
        priority = 0

    todo_id = db.execute(
        """INSERT INTO todos (project_id, task_id, title, description, priority,
                              due_date, tags, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [project_id, task_id, title, description, priority, due_date, tags, created_by]
    )

    priority_labels = {0: "普通", 1: "高", 2: "紧急"}
    return _ok({
        "success":  True,
        "todo_id":  todo_id,
        "title":    title,
        "priority": priority_labels.get(priority, "普通"),
        "message":  f"Todo 已创建（ID: {todo_id}）：{title}",
    })


def _tool_list_todos(args: dict) -> list[TextContent]:
    db.init_db()
    project_id = args.get("project_id")
    status     = args.get("status", "all")
    priority   = args.get("priority")
    limit      = min(int(args.get("limit", 20)), 100)

    conditions, params = [], []
    if project_id:
        conditions.append("project_id = ?")
        params.append(project_id)
    if status and status != "all":
        conditions.append("status = ?")
        params.append(status)
    if priority is not None:
        conditions.append("priority = ?")
        params.append(int(priority))

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = db.query_all(
        f"""SELECT id, title, description, status, priority, due_date, tags,
                   created_by, project_id, task_id, created_at, updated_at
            FROM todos {where}
            ORDER BY priority DESC, created_at DESC
            LIMIT ?""",
        params + [limit]
    )

    priority_labels = {0: "普通", 1: "高", 2: "紧急"}
    enriched = []
    for r in rows:
        d = dict(r)
        d["priority_label"] = priority_labels.get(d.get("priority", 0), "普通")
        enriched.append(d)

    total_row = db.query_one(f"SELECT COUNT(*) as cnt FROM todos {where}", params)
    return _ok({
        "todos":  enriched,
        "total":  total_row["cnt"] if total_row else 0,
        "filter": {"status": status, "project_id": project_id},
    })


def _tool_update_todo(args: dict) -> list[TextContent]:
    db.init_db()
    todo_id     = args.get("todo_id")
    status      = args.get("status")
    title       = args.get("title")
    description = args.get("description")

    if todo_id is None:
        _err("todo_id 不能为空")

    todo = db.query_one("SELECT id, title, status FROM todos WHERE id = ?", [int(todo_id)])
    if not todo:
        _err(f"Todo ID {todo_id} 不存在")

    valid_statuses = {"pending", "in_progress", "done", "cancelled"}
    fields, params = [], []
    if status is not None:
        if status not in valid_statuses:
            _err(f"无效状态: {status}，可选: {', '.join(valid_statuses)}")
        fields.append("status = ?")
        params.append(status)
    if title is not None:
        fields.append("title = ?")
        params.append(title)
    if description is not None:
        fields.append("description = ?")
        params.append(description)

    if not fields:
        _err("至少提供 status / title / description 之一")

    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(int(todo_id))
    db.execute(f"UPDATE todos SET {', '.join(fields)} WHERE id = ?", params)

    status_icons = {"pending": "⏳", "in_progress": "🔄", "done": "✅", "cancelled": "❌"}
    new_status = status or todo["status"]
    return _ok({
        "success":    True,
        "todo_id":    todo_id,
        "new_status": new_status,
        "icon":       status_icons.get(new_status, ""),
        "message":    f"Todo [{todo_id}] 已更新 → {new_status}",
    })


# ── Starlette ASGI 封装 ───────────────────────────────────────────
def create_starlette_app(mcp_server: Server, *, path: str = "/mcp") -> Starlette:
    """
    创建 Starlette ASGI 应用，使用 StreamableHTTP 传输（与 XHS MCP 保持一致）
    """
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.responses import JSONResponse

    # 创建会话管理器（json_response=True：返回普通JSON而非SSE流，客户端无需处理chunked stream）
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        json_response=True,
        stateless=True,
    )

    async def handle_mcp(scope, receive, send):
        """MCP 请求处理器"""
        await session_manager.handle_request(scope, receive, send)

    async def health_check(request: Request):
        return JSONResponse({"status": "ok", "server": "xhs-sqlite-mcp", "tools": 15})

    async def lifespan(app):
        """应用生命周期管理"""
        async with session_manager.run():
            yield

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health_check),
            Mount(path, app=handle_mcp),
        ],
    )


def main():
    db.init_db()  # 确保数据库已初始化
    port = int(os.environ.get("SQLITE_MCP_PORT", 8001))
    logger.info(f"[SQLite MCP] 启动中，端口 {port}，数据库 {os.environ.get('DB_PATH', './data/xhs_tool.db')}")
    starlette_app = create_starlette_app(app, path="/mcp")
    uvicorn.run(starlette_app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
