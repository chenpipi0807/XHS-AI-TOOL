"""
小红书 MCP 服务器
对应 Go 版本: mcp_server.go
注册 13 个 MCP 工具，使用 HTTP Streamable 传输协议
监听端口: 18060，路径: /mcp
"""
import asyncio
import json
import logging
import sys
import os

# 确保 MCP 目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server import Server
from mcp.types import (
    Tool,
    TextContent,
)

from service import get_service

logger = logging.getLogger(__name__)

# 创建 MCP Server 实例
app = Server("xiaohongshu-mcp")
service = get_service()


def _result_to_text(result) -> list[TextContent]:
    """
    将 MCPToolResult 转换为 MCP SDK 期望的 list[TextContent]。

    MCP SDK Server.call_tool() 装饰器内部执行:
        results = await func(name, arguments)
        CallToolResult(content=list(results), isError=False)
    因此处理函数必须返回 Iterable[TextContent]，而非 CallToolResult 对象。
    若返回 CallToolResult，SDK 对其调用 list() 会迭代出字段名元组，
    导致 Pydantic 验证失败（"9 validation errors"）。

    业务失败（result.success=False）时抛出 RuntimeError，
    SDK 捕获后自动返回 isError=True 的 CallToolResult。
    """
    content = json.dumps(result.model_dump(), ensure_ascii=False, indent=2)
    if not result.success:
        raise RuntimeError(content)
    return [TextContent(type="text", text=content)]


# ==================== 工具注册 ====================

@app.list_tools()
async def list_tools() -> list[Tool]:
    """列出所有可用的 MCP 工具"""
    return [
        # 1. 检查登录状态
        Tool(
            name="check_login_status",
            description="检查当前小红书的登录状态，返回是否已登录",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        # 2. 获取登录二维码
        Tool(
            name="get_login_qrcode",
            description=(
                "获取小红书登录二维码（Base64编码的图片）。"
                "如果已登录则返回已登录状态。"
                "获取二维码后，用户需要使用小红书App扫码登录。"
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        # 3. 删除 cookies
        Tool(
            name="delete_cookies",
            description="删除保存的登录 cookies，下次使用时需要重新登录",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        # 4. 发布图文
        Tool(
            name="publish_content",
            description=(
                "发布小红书图文笔记。支持多张图片、话题标签、"
                "定时发布、原创声明和可见范围设置。"
                "图片支持本地路径和 HTTP URL（URL 会自动下载）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "笔记标题。⚠️严格限制：最多20个字符（含emoji、标点、空格），超出将被自动截断！请在调用前数好字数。",
                    },
                    "content": {
                        "type": "string",
                        "description": "笔记正文内容",
                    },
                    "image_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "图片路径列表，支持本地路径或 HTTP URL，最多9张",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "话题标签列表（不含#号）",
                    },
                    "is_private": {
                        "type": "boolean",
                        "description": "是否设为私密（默认公开）",
                        "default": False,
                    },
                    "scheduled_time": {
                        "type": "string",
                        "description": "定时发布时间，格式: '2024-01-01 12:00'（留空则立即发布）",
                        "default": "",
                    },
                    "is_original": {
                        "type": "boolean",
                        "description": "是否声明原创（默认True）",
                        "default": True,
                    },
                },
                "required": ["title", "content", "image_paths"],
            },
        ),
        # 5. 发布视频
        Tool(
            name="publish_with_video",
            description="发布小红书视频笔记。支持视频文件上传和封面设置。",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "视频标题",
                    },
                    "content": {
                        "type": "string",
                        "description": "视频描述内容",
                    },
                    "video_path": {
                        "type": "string",
                        "description": "视频文件本地路径",
                    },
                    "cover_path": {
                        "type": "string",
                        "description": "封面图片路径（可选）",
                        "default": "",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "话题标签列表",
                    },
                    "is_private": {
                        "type": "boolean",
                        "description": "是否私密",
                        "default": False,
                    },
                    "is_original": {
                        "type": "boolean",
                        "description": "是否原创",
                        "default": True,
                    },
                },
                "required": ["title", "content", "video_path"],
            },
        ),
        # 6. 获取推荐列表
        Tool(
            name="list_feeds",
            description=(
                "获取小红书首页推荐内容列表。"
                "返回笔记ID、标题、封面、作者、点赞数等信息。"
                "需要先登录才能获取个性化推荐。"
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        # 7. 搜索内容
        Tool(
            name="search_feeds",
            description=(
                "搜索小红书笔记内容。支持按排序方式、笔记类型、"
                "发布时间、搜索范围等条件筛选。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "排序方式: general（综合）/ popularity_descending（最热）/ time_descending（最新）",
                        "default": "",
                    },
                    "note_type": {
                        "type": "string",
                        "description": "笔记类型: 0（全部）/ 1（视频）/ 2（图文）",
                        "default": "",
                    },
                    "publish_time": {
                        "type": "string",
                        "description": "发布时间: 不限 / 一天内 / 一周内 / 半年内",
                        "default": "",
                    },
                    "search_scope": {
                        "type": "string",
                        "description": "搜索范围: 全部 / 已关注 / 同城",
                        "default": "",
                    },
                    "location": {
                        "type": "string",
                        "description": "位置筛选",
                        "default": "",
                    },
                },
                "required": ["keyword"],
            },
        ),
        # 8. 获取帖子详情
        Tool(
            name="get_feed_detail",
            description=(
                "获取小红书帖子的详细信息，包括正文、图片列表、"
                "评论列表等。支持自动加载所有评论。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "feed_id": {
                        "type": "string",
                        "description": "帖子 ID",
                    },
                    "xsec_token": {
                        "type": "string",
                        "description": "安全 token（从列表接口获取）",
                        "default": "",
                    },
                    "load_comments": {
                        "type": "boolean",
                        "description": "是否加载评论（默认True）",
                        "default": True,
                    },
                    "max_comments": {
                        "type": "integer",
                        "description": "最大加载评论数（默认100）",
                        "default": 100,
                    },
                },
                "required": ["feed_id"],
            },
        ),
        # 9. 发表评论
        Tool(
            name="post_comment_to_feed",
            description="在小红书帖子下发表评论",
            inputSchema={
                "type": "object",
                "properties": {
                    "feed_id": {
                        "type": "string",
                        "description": "帖子 ID",
                    },
                    "content": {
                        "type": "string",
                        "description": "评论内容",
                    },
                    "xsec_token": {
                        "type": "string",
                        "description": "安全 token（可选）",
                        "default": "",
                    },
                },
                "required": ["feed_id", "content"],
            },
        ),
        # 10. 回复评论
        Tool(
            name="reply_comment_in_feed",
            description="回复小红书帖子中的某条评论",
            inputSchema={
                "type": "object",
                "properties": {
                    "feed_id": {
                        "type": "string",
                        "description": "帖子 ID",
                    },
                    "comment_id": {
                        "type": "string",
                        "description": "要回复的评论 ID",
                    },
                    "comment_user_id": {
                        "type": "string",
                        "description": "评论作者的用户 ID",
                    },
                    "content": {
                        "type": "string",
                        "description": "回复内容",
                    },
                    "xsec_token": {
                        "type": "string",
                        "description": "安全 token（可选）",
                        "default": "",
                    },
                },
                "required": ["feed_id", "comment_id", "comment_user_id", "content"],
            },
        ),
        # 11. 点赞
        Tool(
            name="like_feed",
            description=(
                "点赞或取消点赞小红书帖子。"
                "如果帖子当前未点赞则点赞，已点赞则取消点赞。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "feed_id": {
                        "type": "string",
                        "description": "帖子 ID",
                    },
                    "xsec_token": {
                        "type": "string",
                        "description": "安全 token（可选）",
                        "default": "",
                    },
                },
                "required": ["feed_id"],
            },
        ),
        # 12. 收藏
        Tool(
            name="favorite_feed",
            description=(
                "收藏或取消收藏小红书帖子。"
                "如果帖子当前未收藏则收藏，已收藏则取消收藏。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "feed_id": {
                        "type": "string",
                        "description": "帖子 ID",
                    },
                    "xsec_token": {
                        "type": "string",
                        "description": "安全 token（可选）",
                        "default": "",
                    },
                },
                "required": ["feed_id"],
            },
        ),
        # 13. 用户资料
        Tool(
            name="user_profile",
            description=(
                "获取小红书用户资料，包括基本信息（昵称、头像、"
                "粉丝数、关注数）和用户发布的笔记列表。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "用户 ID",
                    },
                    "xsec_token": {
                        "type": "string",
                        "description": "安全 token（可选）",
                        "default": "",
                    },
                },
                "required": ["user_id"],
            },
        ),
    ]


# ==================== 工具调用处理 ====================

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """
    处理所有 MCP 工具调用。

    返回 list[TextContent]，由 MCP SDK 包装成 CallToolResult。
    业务失败或未知工具时抛出异常，SDK 捕获后设置 isError=True。
    """
    logger.info(f"调用工具: {name}, 参数: {arguments}")

    # 1. 检查登录状态
    if name == "check_login_status":
        result = await service.check_login()
        return _result_to_text(result)

    # 2. 获取登录二维码
    elif name == "get_login_qrcode":
        result = await service.get_qrcode()
        return _result_to_text(result)

    # 3. 删除 cookies
    elif name == "delete_cookies":
        result = await service.delete_login_cookies()
        return _result_to_text(result)

    # 4. 发布图文
    elif name == "publish_content":
        # 服务端强制截断标题到20个字符，防止 AI 生成超长标题
        raw_title = arguments.get("title", "")
        if len(raw_title) > 20:
            logger.warning(f"标题超长({len(raw_title)}字)，自动截断至20字: {raw_title!r}")
            raw_title = raw_title[:20]
        result = await service.publish_content(
            title=raw_title,
            content=arguments.get("content", ""),
            image_paths=arguments.get("image_paths", []),
            tags=arguments.get("tags"),
            is_private=arguments.get("is_private", False),
            scheduled_time=arguments.get("scheduled_time", ""),
            is_original=arguments.get("is_original", True),
        )
        return _result_to_text(result)

    # 5. 发布视频
    elif name == "publish_with_video":
        result = await service.publish_with_video(
            title=arguments.get("title", ""),
            content=arguments.get("content", ""),
            video_path=arguments.get("video_path", ""),
            cover_path=arguments.get("cover_path", ""),
            tags=arguments.get("tags"),
            is_private=arguments.get("is_private", False),
            is_original=arguments.get("is_original", True),
        )
        return _result_to_text(result)

    # 6. 获取推荐列表
    elif name == "list_feeds":
        result = await service.list_feeds()
        return _result_to_text(result)

    # 7. 搜索内容
    elif name == "search_feeds":
        result = await service.search(
            keyword=arguments.get("keyword", ""),
            sort_by=arguments.get("sort_by", ""),
            note_type=arguments.get("note_type", ""),
            publish_time=arguments.get("publish_time", ""),
            search_scope=arguments.get("search_scope", ""),
            location=arguments.get("location", ""),
        )
        return _result_to_text(result)

    # 8. 获取帖子详情
    elif name == "get_feed_detail":
        result = await service.get_feed_detail_service(
            feed_id=arguments.get("feed_id", ""),
            xsec_token=arguments.get("xsec_token", ""),
            load_comments=arguments.get("load_comments", True),
            max_comments=arguments.get("max_comments", 100),
        )
        return _result_to_text(result)

    # 9. 发表评论
    elif name == "post_comment_to_feed":
        result = await service.post_comment_to_feed(
            feed_id=arguments.get("feed_id", ""),
            content=arguments.get("content", ""),
            xsec_token=arguments.get("xsec_token", ""),
        )
        return _result_to_text(result)

    # 10. 回复评论
    elif name == "reply_comment_in_feed":
        result = await service.reply_comment_in_feed(
            feed_id=arguments.get("feed_id", ""),
            comment_id=arguments.get("comment_id", ""),
            comment_user_id=arguments.get("comment_user_id", ""),
            content=arguments.get("content", ""),
            xsec_token=arguments.get("xsec_token", ""),
        )
        return _result_to_text(result)

    # 11. 点赞
    elif name == "like_feed":
        result = await service.like_feed_action(
            feed_id=arguments.get("feed_id", ""),
            xsec_token=arguments.get("xsec_token", ""),
        )
        return _result_to_text(result)

    # 12. 收藏
    elif name == "favorite_feed":
        result = await service.favorite_feed_action(
            feed_id=arguments.get("feed_id", ""),
            xsec_token=arguments.get("xsec_token", ""),
        )
        return _result_to_text(result)

    # 13. 用户资料
    elif name == "user_profile":
        result = await service.get_user_profile_service(
            user_id=arguments.get("user_id", ""),
            xsec_token=arguments.get("xsec_token", ""),
        )
        return _result_to_text(result)

    else:
        raise ValueError(f"未知工具: {name}")


def create_starlette_app(mcp_server: Server, *, path: str = "/mcp"):
    """
    创建 Starlette ASGI 应用，集成 StreamableHTTP 传输
    使用 StreamableHTTPSessionManager 管理会话
    """
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount
    from starlette.responses import JSONResponse
    from starlette.requests import Request
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

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
        return JSONResponse({
            "status": "ok",
            "service": "xiaohongshu-mcp",
            "version": "1.0.0",
        })

    async def lifespan(app):
        """应用生命周期管理"""
        async with session_manager.run():
            yield

    starlette_app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health_check),
            Mount(path, app=handle_mcp),
        ],
    )

    return starlette_app
