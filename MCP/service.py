"""
小红书业务服务层
对应 Go 版本: service.go
将所有操作模块统一封装，提供统一的错误处理和日志记录
"""
import logging
from typing import List, Optional, Any, Dict

from xiaohongshu.types import (
    Feed, FeedDetailResponse, UserProfileResponse,
    FilterOption, PublishImageContent, PublishVideoContent,
    MCPToolResult,
)
from xiaohongshu.login import check_login_status, get_login_qrcode, wait_for_login
from xiaohongshu.feeds import get_feeds_list
from xiaohongshu.search import search_feeds
from xiaohongshu.feed_detail import get_feed_detail
from xiaohongshu.publish import publish_image_content
from xiaohongshu.publish_video import publish_video_content
from xiaohongshu.comment_feed import post_comment, reply_comment
from xiaohongshu.like_favorite import like_feed, favorite_feed
from xiaohongshu.user_profile import get_user_profile, get_my_profile
from cookies.cookies import delete_cookies
from pkg.downloader import ensure_local_paths

logger = logging.getLogger(__name__)


def _success_result(data: Any = None, message: str = "操作成功") -> MCPToolResult:
    """创建成功结果"""
    return MCPToolResult(success=True, message=message, data=data)


def _error_result(error: str, message: str = "操作失败") -> MCPToolResult:
    """创建错误结果"""
    return MCPToolResult(success=False, message=message, error=error)


class XiaohongshuService:
    """小红书业务服务层"""

    # ==================== 登录相关 ====================

    async def check_login(self) -> MCPToolResult:
        """
        检查登录状态
        对应 Go: CheckLoginStatus
        """
        try:
            is_logged = await check_login_status()
            return _success_result(
                data={"logged_in": is_logged},
                message="已登录" if is_logged else "未登录，请先扫码登录",
            )
        except Exception as e:
            logger.error(f"检查登录状态失败: {e}")
            return _error_result(str(e), "检查登录状态失败")

    async def get_qrcode(self) -> MCPToolResult:
        """
        获取登录二维码
        对应 Go: GetLoginQRCode
        """
        try:
            # 先检查是否已登录
            is_logged = await check_login_status()
            if is_logged:
                return _success_result(
                    data={"logged_in": True, "qrcode": None},
                    message="已登录，无需扫码",
                )

            qrcode_b64 = await get_login_qrcode()
            if not qrcode_b64:
                return _error_result("无法获取登录二维码", "获取二维码失败")

            return _success_result(
                data={
                    "logged_in": False,
                    "qrcode": f"data:image/png;base64,{qrcode_b64}",
                    "tip": "请使用小红书 App 扫描二维码登录",
                },
                message="请扫描二维码登录",
            )
        except Exception as e:
            logger.error(f"获取二维码失败: {e}")
            return _error_result(str(e), "获取二维码失败")

    async def delete_login_cookies(self) -> MCPToolResult:
        """
        删除登录 cookies
        对应 Go: DeleteCookies
        """
        try:
            success = delete_cookies()
            if success:
                return _success_result(message="Cookies 已删除，下次使用需重新登录")
            return _error_result("删除 cookies 文件失败", "删除 cookies 失败")
        except Exception as e:
            logger.error(f"删除 cookies 失败: {e}")
            return _error_result(str(e), "删除 cookies 失败")

    # ==================== 内容发布 ====================

    async def publish_content(
        self,
        title: str,
        content: str,
        image_paths: List[str],
        tags: Optional[List[str]] = None,
        is_private: bool = False,
        scheduled_time: str = "",
        is_original: bool = True,
    ) -> MCPToolResult:
        """
        发布图文内容
        对应 Go: PublishContent
        支持本地图片路径和 HTTP URL（URL 会自动下载）
        """
        try:
            if not image_paths:
                return _error_result("至少需要一张图片", "参数错误")

            # 处理图片路径（如果是 URL 则下载）
            local_paths = await ensure_local_paths(image_paths)
            if not local_paths:
                return _error_result("图片路径无效或下载失败", "图片处理失败")

            result = await publish_image_content(
                title=title,
                content=content,
                image_paths=local_paths,
                tags=tags or [],
                is_private=is_private,
                scheduled_time=scheduled_time,
                is_original=is_original,
            )

            # publish_image_content 返回 dict: {"success": bool, "post_url": str, ...}
            if isinstance(result, dict):
                if result.get("success"):
                    return _success_result(
                        data=result,
                        message="图文发布成功",
                    )
                return _error_result("发布操作未返回成功状态", "发布失败")
            # 兼容旧版 bool 返回
            if result:
                return _success_result(
                    data={"title": title, "images_count": len(local_paths)},
                    message="图文发布成功",
                )
            return _error_result("发布操作未返回成功状态", "发布失败")

        except FileNotFoundError as e:
            return _error_result(str(e), "文件不存在")
        except Exception as e:
            logger.error(f"发布图文失败: {e}")
            return _error_result(str(e), "发布图文失败")

    async def publish_with_video(
        self,
        title: str,
        content: str,
        video_path: str,
        cover_path: str = "",
        tags: Optional[List[str]] = None,
        is_private: bool = False,
        is_original: bool = True,
    ) -> MCPToolResult:
        """
        发布视频内容
        对应 Go: PublishVideo
        """
        try:
            if not video_path:
                return _error_result("视频路径不能为空", "参数错误")

            success = await publish_video_content(
                title=title,
                content=content,
                video_path=video_path,
                cover_path=cover_path,
                tags=tags or [],
                is_private=is_private,
                is_original=is_original,
            )

            if success:
                return _success_result(
                    data={"title": title, "video_path": video_path},
                    message="视频发布成功",
                )
            return _error_result("发布操作未返回成功状态", "发布失败")

        except FileNotFoundError as e:
            return _error_result(str(e), "文件不存在")
        except Exception as e:
            logger.error(f"发布视频失败: {e}")
            return _error_result(str(e), "发布视频失败")

    # ==================== 内容获取 ====================

    async def list_feeds(self) -> MCPToolResult:
        """
        获取首页推荐列表
        对应 Go: GetFeedsList
        """
        try:
            feeds = await get_feeds_list()
            feeds_data = [f.model_dump() for f in feeds]
            return _success_result(
                data={"feeds": feeds_data, "count": len(feeds)},
                message=f"获取到 {len(feeds)} 个推荐内容",
            )
        except Exception as e:
            logger.error(f"获取推荐列表失败: {e}")
            return _error_result(str(e), "获取推荐列表失败")

    async def search(
        self,
        keyword: str,
        sort_by: str = "",
        note_type: str = "",
        publish_time: str = "",
        search_scope: str = "",
        location: str = "",
    ) -> MCPToolResult:
        """
        搜索内容
        对应 Go: SearchFeeds
        """
        try:
            if not keyword.strip():
                return _error_result("搜索关键词不能为空", "参数错误")

            filter_option = None
            if any([sort_by, note_type, publish_time, search_scope, location]):
                filter_option = FilterOption(
                    sort_by=sort_by,
                    note_type=note_type,
                    publish_time=publish_time,
                    search_scope=search_scope,
                    location=location,
                )

            feeds = await search_feeds(keyword, filter_option)
            feeds_data = [f.model_dump() for f in feeds]
            return _success_result(
                data={"feeds": feeds_data, "count": len(feeds), "keyword": keyword},
                message=f"搜索 '{keyword}' 找到 {len(feeds)} 个结果",
            )
        except ValueError as e:
            return _error_result(str(e), "参数错误")
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return _error_result(str(e), "搜索失败")

    async def get_feed_detail_service(
        self,
        feed_id: str,
        xsec_token: str = "",
        load_comments: bool = True,
        max_comments: int = 100,
    ) -> MCPToolResult:
        """
        获取帖子详情
        对应 Go: GetFeedDetail
        """
        try:
            if not feed_id.strip():
                return _error_result("帖子 ID 不能为空", "参数错误")

            result = await get_feed_detail(
                feed_id=feed_id,
                xsec_token=xsec_token,
                load_comments=load_comments,
                max_comments=max_comments,
            )

            return _success_result(
                data=result.model_dump(),
                message=f"获取帖子详情成功，评论数: {len(result.comment_list.comments)}",
            )
        except Exception as e:
            logger.error(f"获取帖子详情失败: {e}")
            return _error_result(str(e), "获取帖子详情失败")

    # ==================== 评论操作 ====================

    async def post_comment_to_feed(
        self,
        feed_id: str,
        content: str,
        xsec_token: str = "",
        feed_url: str = "",
    ) -> MCPToolResult:
        """
        发表评论
        对应 Go: PostCommentToFeed
        支持 feed_id+xsec_token 或完整 feed_url (优先使用 feed_url)
        """
        try:
            if not feed_id.strip() and not feed_url.strip():
                return _error_result("feed_id 和 feed_url 不能同时为空", "参数错误")
            if not content.strip():
                return _error_result("评论内容不能为空", "参数错误")

            success = await post_comment(
                feed_id=feed_id,
                content=content,
                xsec_token=xsec_token,
                feed_url=feed_url,
            )

            if success:
                return _success_result(
                    data={"feed_id": feed_id, "feed_url": feed_url, "content": content},
                    message="评论发表成功",
                )
            return _error_result("评论发表操作未成功", "发表评论失败")

        except Exception as e:
            logger.error(f"发表评论失败: {e}")
            return _error_result(str(e), "发表评论失败")

    async def reply_comment_in_feed(
        self,
        feed_id: str,
        comment_id: str,
        comment_user_id: str,
        content: str,
        xsec_token: str = "",
        feed_url: str = "",
    ) -> MCPToolResult:
        """
        回复评论
        对应 Go: ReplyCommentInFeed
        支持 feed_id+xsec_token 或完整 feed_url (优先使用 feed_url)
        """
        try:
            if not all([comment_id.strip(), content.strip()]):
                return _error_result("comment_id、content 不能为空", "参数错误")
            if not feed_id.strip() and not feed_url.strip():
                return _error_result("feed_id 和 feed_url 不能同时为空", "参数错误")

            success = await reply_comment(
                feed_id=feed_id,
                comment_id=comment_id,
                content=content,
                xsec_token=xsec_token,
                feed_url=feed_url,
            )

            if success:
                return _success_result(
                    data={"feed_id": feed_id, "comment_id": comment_id, "content": content},
                    message="回复评论成功",
                )
            return _error_result("回复评论操作未成功", "回复评论失败")

        except Exception as e:
            logger.error(f"回复评论失败: {e}")
            return _error_result(str(e), "回复评论失败")

    # ==================== 点赞/收藏 ====================

    async def like_feed_action(
        self,
        feed_id: str,
        xsec_token: str = "",
    ) -> MCPToolResult:
        """
        点赞/取消点赞
        对应 Go: LikeFeed
        """
        try:
            if not feed_id.strip():
                return _error_result("帖子 ID 不能为空", "参数错误")

            result = await like_feed(feed_id=feed_id, xsec_token=xsec_token)
            return _success_result(data=result, message=result.get("message", "操作成功"))

        except Exception as e:
            logger.error(f"点赞操作失败: {e}")
            return _error_result(str(e), "点赞操作失败")

    async def favorite_feed_action(
        self,
        feed_id: str,
        xsec_token: str = "",
    ) -> MCPToolResult:
        """
        收藏/取消收藏
        对应 Go: FavoriteFeed
        """
        try:
            if not feed_id.strip():
                return _error_result("帖子 ID 不能为空", "参数错误")

            result = await favorite_feed(feed_id=feed_id, xsec_token=xsec_token)
            return _success_result(data=result, message=result.get("message", "操作成功"))

        except Exception as e:
            logger.error(f"收藏操作失败: {e}")
            return _error_result(str(e), "收藏操作失败")

    # ==================== 用户资料 ====================

    async def get_user_profile_service(
        self,
        user_id: str,
        xsec_token: str = "",
    ) -> MCPToolResult:
        """
        获取用户资料
        对应 Go: GetUserProfile
        """
        try:
            if not user_id.strip():
                return _error_result("用户 ID 不能为空", "参数错误")

            profile = await get_user_profile(user_id=user_id, xsec_token=xsec_token)
            return _success_result(
                data=profile.model_dump(),
                message=f"获取用户 {profile.basic_info.nickname} 资料成功",
            )
        except Exception as e:
            logger.error(f"获取用户资料失败: {e}")
            return _error_result(str(e), "获取用户资料失败")

    async def get_my_profile_service(self) -> MCPToolResult:
        """
        获取当前登录用户资料
        对应 Go: GetMyProfile
        """
        try:
            profile = await get_my_profile()
            return _success_result(
                data=profile.model_dump(),
                message=f"获取我的资料成功: {profile.basic_info.nickname}",
            )
        except Exception as e:
            logger.error(f"获取我的资料失败: {e}")
            return _error_result(str(e), "获取我的资料失败")


# 全局服务实例
_service_instance: Optional[XiaohongshuService] = None


def get_service() -> XiaohongshuService:
    """获取全局服务实例（单例）"""
    global _service_instance
    if _service_instance is None:
        _service_instance = XiaohongshuService()
    return _service_instance
