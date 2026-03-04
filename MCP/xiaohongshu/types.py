"""
小红书数据类型定义
对应 Go 版本: xiaohongshu/types.go 和 types.go
使用 Pydantic v2 进行数据验证
"""
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field


# ==================== 基础数据模型 ====================

class Cover(BaseModel):
    """封面图片信息"""
    url: str = ""
    url_default: str = ""
    width: int = 0
    height: int = 0


class Video(BaseModel):
    """视频信息"""
    url: str = ""
    duration: int = 0
    width: int = 0
    height: int = 0


class InteractInfo(BaseModel):
    """互动信息（点赞/评论/收藏数量）"""
    liked: bool = False
    liked_count: str = "0"
    collected: bool = False
    collected_count: str = "0"
    comment_count: str = "0"
    share_count: str = "0"


class User(BaseModel):
    """用户基本信息"""
    user_id: str = ""
    nickname: str = ""
    avatar: str = ""
    desc: str = ""


class NoteCard(BaseModel):
    """笔记卡片（搜索/列表结果中的简化笔记）"""
    note_id: str = ""
    type: str = ""  # normal / video
    display_title: str = ""
    cover: Cover = Field(default_factory=Cover)
    interact_info: InteractInfo = Field(default_factory=InteractInfo)
    user: User = Field(default_factory=User)
    xsec_token: str = ""


class Feed(BaseModel):
    """推荐流中的帖子"""
    id: str = ""
    note_card: NoteCard = Field(default_factory=NoteCard)
    xsec_token: str = ""
    track_id: str = ""


# ==================== 帖子详情数据模型 ====================

class ImageInfo(BaseModel):
    """图片信息"""
    url: str = ""
    width: int = 0
    height: int = 0


class Comment(BaseModel):
    """评论"""
    id: str = ""
    user_info: User = Field(default_factory=User)
    content: str = ""
    like_count: str = "0"
    create_time: int = 0
    sub_comment_count: str = "0"
    sub_comments: List["Comment"] = Field(default_factory=list)


class CommentList(BaseModel):
    """评论列表"""
    comments: List[Comment] = Field(default_factory=list)
    total_count: int = 0
    has_more: bool = False


class FeedDetail(BaseModel):
    """帖子详情"""
    note_id: str = ""
    type: str = ""
    title: str = ""
    desc: str = ""
    user: User = Field(default_factory=User)
    image_list: List[ImageInfo] = Field(default_factory=list)
    video: Optional[Video] = None
    interact_info: InteractInfo = Field(default_factory=InteractInfo)
    tag_list: List[str] = Field(default_factory=list)
    collect_id: str = ""
    xsec_token: str = ""


class FeedDetailResponse(BaseModel):
    """帖子详情完整响应"""
    feed_detail: FeedDetail = Field(default_factory=FeedDetail)
    comment_list: CommentList = Field(default_factory=CommentList)


# ==================== 用户资料数据模型 ====================

class UserBasicInfo(BaseModel):
    """用户基本资料"""
    user_id: str = ""
    nickname: str = ""
    avatar: str = ""
    desc: str = ""
    gender: int = 0
    location: str = ""
    follows: int = 0
    fans: int = 0
    interaction: int = 0
    ip_location: str = ""


class UserNote(BaseModel):
    """用户笔记（资料页展示）"""
    note_id: str = ""
    title: str = ""
    cover_url: str = ""
    liked_count: str = "0"
    xsec_token: str = ""


class UserProfileResponse(BaseModel):
    """用户资料完整响应"""
    basic_info: UserBasicInfo = Field(default_factory=UserBasicInfo)
    notes: List[UserNote] = Field(default_factory=list)


# ==================== HTTP API 数据模型 ====================

class ErrorResponse(BaseModel):
    """错误响应"""
    code: int = -1
    message: str = ""
    error: str = ""


class SuccessResponse(BaseModel):
    """成功响应"""
    code: int = 0
    message: str = "success"
    data: Optional[Any] = None


class MCPToolResult(BaseModel):
    """MCP 工具调用结果"""
    success: bool = True
    message: str = ""
    data: Optional[Any] = None
    error: str = ""


# ==================== 请求参数数据模型 ====================

class FeedDetailRequest(BaseModel):
    """获取帖子详情请求"""
    feed_id: str
    xsec_token: str = ""
    load_comments: bool = True
    max_comments: int = 100


class SearchFeedsRequest(BaseModel):
    """搜索帖子请求"""
    keyword: str
    sort_by: str = ""         # general / time_descending / popularity_descending
    note_type: str = ""       # 0=全部 / 1=视频 / 2=图文
    publish_time: str = ""    # 不限 / 一天内 / 一周内 / 半年内
    search_scope: str = ""    # 全部 / 已关注 / 同城
    location: str = ""        # 搜索范围附加位置


class PostCommentRequest(BaseModel):
    """发表评论请求"""
    feed_id: str
    xsec_token: str = ""
    content: str


class ReplyCommentRequest(BaseModel):
    """回复评论请求"""
    feed_id: str
    xsec_token: str = ""
    comment_id: str
    comment_user_id: str
    content: str


class UserProfileRequest(BaseModel):
    """获取用户资料请求"""
    user_id: str
    xsec_token: str = ""


class ActionResult(BaseModel):
    """操作结果（点赞/收藏等）"""
    success: bool = True
    message: str = ""
    action: str = ""  # like / unlike / collect / uncollect


# ==================== 发布内容数据模型 ====================

class PublishImageContent(BaseModel):
    """发布图文内容"""
    title: str
    content: str
    image_paths: List[str]
    tags: List[str] = Field(default_factory=list)
    is_private: bool = False
    scheduled_time: str = ""    # 定时发布时间 格式: "2024-01-01 12:00"
    is_original: bool = True
    allow_save: bool = True


class PublishVideoContent(BaseModel):
    """发布视频内容"""
    title: str
    content: str
    video_path: str
    cover_path: str = ""
    tags: List[str] = Field(default_factory=list)
    is_private: bool = False
    is_original: bool = True


# ==================== 筛选器数据模型 ====================

class FilterOption(BaseModel):
    """搜索筛选选项"""
    sort_by: str = ""         # general / time_descending / popularity_descending
    note_type: str = ""       # 0 / 1(视频) / 2(图文)
    publish_time: str = ""    # 不限/一天内/一周内/半年内
    search_scope: str = ""    # 全部/已关注/同城
    location: str = ""


# ==================== 内部筛选器（对应 Go internalFilterOption）====================

class InternalFilterOption(BaseModel):
    """内部筛选选项（用于 URL 参数构建）"""
    filter_type: int = 0
    value: int = 0


# Rebuild model for forward references
Comment.model_rebuild()
