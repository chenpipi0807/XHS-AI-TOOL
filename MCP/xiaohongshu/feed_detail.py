"""
小红书帖子详情模块（含评论加载）
对应 Go 版本: xiaohongshu/feed_detail.go
这是最复杂的模块，包含人类模拟滚动、重试逻辑、评论分页加载
"""
import asyncio
import logging
import random
from typing import List, Optional
from dataclasses import dataclass

from playwright.async_api import Page, ElementHandle

from browser.browser import get_browser_page
from xiaohongshu.types import (
    FeedDetail, FeedDetailResponse, CommentList, Comment,
    User, InteractInfo, Cover, Video, ImageInfo
)

logger = logging.getLogger(__name__)

XHS_EXPLORE_URL = "https://www.xiaohongshu.com/explore/{feed_id}"

# CSS 选择器
COMMENT_INPUT_SELECTOR = "div.input-box div.content-edit"
COMMENT_LIST_SELECTOR = ".comment-list"
COMMENT_ITEM_SELECTOR = ".comment-item"
END_CONTAINER_SELECTOR = ".end-container, .no-more"
LOAD_MORE_SELECTOR = ".load-more-comments, .more-comments"
SUB_COMMENT_SELECTOR = ".sub-comment-item"
MORE_REPLIES_SELECTOR = ".more-replies, .expand-replies"


@dataclass
class CommentLoadConfig:
    """评论加载配置"""
    click_more_replies: bool = True
    max_replies_threshold: int = 3    # 超过此数量才点击展开回复
    max_comment_items: int = 100      # 最大评论数量
    scroll_speed: str = "normal"      # slow / normal / fast
    max_scroll_attempts: int = 30     # 最大滚动次数


async def _get_scroll_top(page: Page) -> int:
    """获取当前滚动位置"""
    try:
        return await page.evaluate("() => document.documentElement.scrollTop || document.body.scrollTop")
    except Exception:
        return 0


async def _get_comment_count(page: Page) -> int:
    """获取当前已加载的评论数量"""
    try:
        count = await page.evaluate("""
            () => {
                const items = document.querySelectorAll('.comment-item');
                return items ? items.length : 0;
            }
        """)
        return int(count or 0)
    except Exception:
        return 0


async def _get_total_comment_count(page: Page) -> int:
    """从页面获取总评论数"""
    try:
        count = await page.evaluate("""
            () => {
                try {
                    const state = window.__INITIAL_STATE__;
                    if (!state) return 0;
                    const noteMap = state.note && state.note.noteDetailMap;
                    if (!noteMap) return 0;
                    for (const key in noteMap) {
                        const note = noteMap[key];
                        if (note && note.interactInfo && note.interactInfo.commentCount) {
                            return parseInt(note.interactInfo.commentCount) || 0;
                        }
                    }
                    return 0;
                } catch(e) {
                    return 0;
                }
            }
        """)
        return int(count or 0)
    except Exception:
        return 0


async def _check_end_container(page: Page) -> bool:
    """检查是否到达评论末尾"""
    try:
        result = await page.evaluate("""
            () => {
                const endContainer = document.querySelector('.end-container');
                if (endContainer) {
                    const style = window.getComputedStyle(endContainer);
                    return style.display !== 'none' && style.visibility !== 'hidden';
                }
                // 检查"没有更多了"等文字
                const allText = document.body.innerText;
                return allText.includes('没有更多了') || allText.includes('已经到底了');
            }
        """)
        return bool(result)
    except Exception:
        return False


async def _click_element_with_human_behavior(page: Page, element: ElementHandle) -> bool:
    """模拟人类点击行为（带随机延迟）"""
    try:
        # 随机小延迟
        await asyncio.sleep(random.uniform(0.2, 0.8))
        await element.scroll_into_view_if_needed()
        await asyncio.sleep(random.uniform(0.1, 0.3))
        await element.click()
        await asyncio.sleep(random.uniform(0.3, 0.8))
        return True
    except Exception as e:
        logger.debug(f"点击元素失败: {e}")
        return False


async def _click_more_reply_buttons(page: Page, config: CommentLoadConfig) -> int:
    """
    点击所有"查看更多回复"按钮
    返回点击的次数
    """
    clicked = 0
    try:
        # 查找所有展开回复的按钮
        more_reply_selectors = [
            ".show-more",
            ".expand-btn",
            "span.more-replies",
            ".sub-comment-more",
        ]

        for selector in more_reply_selectors:
            buttons = await page.query_selector_all(selector)
            for btn in buttons:
                try:
                    visible = await btn.is_visible()
                    if visible:
                        await _click_element_with_human_behavior(page, btn)
                        clicked += 1
                        await asyncio.sleep(0.5)
                except Exception:
                    continue

    except Exception as e:
        logger.debug(f"点击更多回复按钮失败: {e}")

    return clicked


async def _load_all_comments(page: Page, config: CommentLoadConfig) -> None:
    """
    通过滚动加载所有评论
    模拟人类滚动行为
    """
    speed_config = {
        "slow": {"min_delay": 1.5, "max_delay": 3.0, "scroll_dist": 300},
        "normal": {"min_delay": 0.8, "max_delay": 1.5, "scroll_dist": 500},
        "fast": {"min_delay": 0.3, "max_delay": 0.8, "scroll_dist": 700},
    }
    sc = speed_config.get(config.scroll_speed, speed_config["normal"])

    last_comment_count = 0
    no_change_count = 0
    max_no_change = 5  # 连续5次没有新评论则停止

    for attempt in range(config.max_scroll_attempts):
        current_count = await _get_comment_count(page)
        logger.debug(f"滚动 {attempt + 1}/{config.max_scroll_attempts}, 当前评论数: {current_count}")

        # 检查是否到达末尾
        if await _check_end_container(page):
            logger.info("已到达评论末尾")
            break

        # 检查是否达到最大评论数
        if current_count >= config.max_comment_items:
            logger.info(f"已达到最大评论数: {config.max_comment_items}")
            break

        # 检查是否有新评论加载
        if current_count == last_comment_count:
            no_change_count += 1
            if no_change_count >= max_no_change:
                logger.info("评论不再增加，停止滚动")
                break
        else:
            no_change_count = 0
            last_comment_count = current_count

        # 点击展开更多回复
        if config.click_more_replies:
            await _click_more_reply_buttons(page, config)

        # 滚动页面
        scroll_dist = sc["scroll_dist"] + random.randint(-100, 100)
        await page.evaluate(f"window.scrollBy(0, {scroll_dist})")
        delay = random.uniform(sc["min_delay"], sc["max_delay"])
        await asyncio.sleep(delay)

        # 检查滚动是否停止（到达底部）
        scroll_top = await _get_scroll_top(page)
        if scroll_top == 0 and attempt > 0:
            logger.debug("滚动位置为0，可能到达底部")
            no_change_count += 1


async def _parse_comments_from_state(page: Page) -> List[Comment]:
    """从 window.__INITIAL_STATE__ 解析评论数据"""
    try:
        comments_data = await page.evaluate("""
            () => {
                try {
                    const state = window.__INITIAL_STATE__;
                    if (!state || !state.note) return [];

                    // 尝试从 note.noteDetailMap 获取评论
                    const commentMap = state.note.commentMap || {};
                    const commentIds = state.note.commentIds || [];

                    if (commentIds.length > 0) {
                        return commentIds.map(id => commentMap[id]).filter(Boolean);
                    }

                    // 备选: 从 noteDetailMap 获取
                    const noteDetailMap = state.note.noteDetailMap || {};
                    for (const key in noteDetailMap) {
                        const detail = noteDetailMap[key];
                        if (detail && detail.comments) {
                            return detail.comments;
                        }
                    }

                    return [];
                } catch(e) {
                    return [];
                }
            }
        """)
        return _convert_comments_data(comments_data or [])
    except Exception as e:
        logger.debug(f"从状态解析评论失败: {e}")
        return []


async def _parse_comments_from_dom(page: Page) -> List[Comment]:
    """从 DOM 解析评论数据（备用方案）"""
    try:
        comments_data = await page.evaluate("""
            () => {
                const comments = [];
                const commentItems = document.querySelectorAll('.comment-item');

                commentItems.forEach(item => {
                    try {
                        const userEl = item.querySelector('.user-info .name, .nickname');
                        const contentEl = item.querySelector('.content, .comment-content');
                        const likeEl = item.querySelector('.like-count, .likes');
                        const userIdEl = item.querySelector('[data-user-id], .user-link');
                        const commentIdEl = item.getAttribute('data-comment-id') ||
                                           item.querySelector('[data-comment-id]')?.getAttribute('data-comment-id');

                        const comment = {
                            id: commentIdEl || '',
                            content: contentEl ? contentEl.textContent.trim() : '',
                            likeCount: likeEl ? likeEl.textContent.trim() : '0',
                            userInfo: {
                                userId: userIdEl ? (userIdEl.getAttribute('data-user-id') || '') : '',
                                nickname: userEl ? userEl.textContent.trim() : '',
                            },
                            subComments: []
                        };

                        // 获取子评论
                        const subItems = item.querySelectorAll('.sub-comment-item');
                        subItems.forEach(subItem => {
                            const subUser = subItem.querySelector('.user-info .name, .nickname');
                            const subContent = subItem.querySelector('.content, .comment-content');
                            comment.subComments.push({
                                id: subItem.getAttribute('data-comment-id') || '',
                                content: subContent ? subContent.textContent.trim() : '',
                                userInfo: {
                                    userId: '',
                                    nickname: subUser ? subUser.textContent.trim() : '',
                                },
                                likeCount: '0',
                                subComments: []
                            });
                        });

                        comments.push(comment);
                    } catch(e) {}
                });

                return comments;
            }
        """)
        return _convert_comments_data(comments_data or [])
    except Exception as e:
        logger.debug(f"从DOM解析评论失败: {e}")
        return []


def _convert_comments_data(comments_data: list) -> List[Comment]:
    """将原始评论数据转换为 Comment 对象列表"""
    comments = []
    for item in comments_data:
        if not isinstance(item, dict):
            continue
        try:
            user_data = item.get("userInfo", {}) or {}
            sub_comments_data = item.get("subComments", []) or []

            user = User(
                user_id=str(user_data.get("userId", "") or ""),
                nickname=str(user_data.get("nickname", "") or ""),
                avatar=str(user_data.get("avatar", "") or ""),
            )

            sub_comments = _convert_comments_data(sub_comments_data)

            comment = Comment(
                id=str(item.get("id", "") or ""),
                user_info=user,
                content=str(item.get("content", "") or ""),
                like_count=str(item.get("likeCount", "0") or "0"),
                create_time=int(item.get("createTime", 0) or 0),
                sub_comment_count=str(item.get("subCommentCount", "0") or "0"),
                sub_comments=sub_comments,
            )
            comments.append(comment)
        except Exception as e:
            logger.debug(f"转换评论数据失败: {e}")
            continue
    return comments


async def _extract_feed_detail(page: Page, feed_id: str) -> FeedDetail:
    """从 window.__INITIAL_STATE__ 提取帖子详情"""
    detail_data = await page.evaluate(f"""
        () => {{
            try {{
                const state = window.__INITIAL_STATE__;
                if (!state || !state.note || !state.note.noteDetailMap) return null;

                const noteMap = state.note.noteDetailMap;
                // 尝试直接通过 feedId 获取
                if (noteMap['{feed_id}']) return noteMap['{feed_id}'];

                // 获取第一个可用的 note detail
                const keys = Object.keys(noteMap);
                if (keys.length > 0) return noteMap[keys[0]];
                return null;
            }} catch(e) {{
                return null;
            }}
        }}
    """)

    if not detail_data:
        return FeedDetail(note_id=feed_id)

    try:
        user_data = detail_data.get("user", {}) or {}
        interact_data = detail_data.get("interactInfo", {}) or {}
        image_list_data = detail_data.get("imageList", []) or []
        video_data = detail_data.get("video", {}) or {}
        tag_list_data = detail_data.get("tagList", []) or []

        user = User(
            user_id=str(user_data.get("userId", "") or ""),
            nickname=str(user_data.get("nickname", "") or ""),
            avatar=str(user_data.get("avatar", "") or ""),
            desc=str(user_data.get("desc", "") or ""),
        )

        interact_info = InteractInfo(
            liked=bool(interact_data.get("liked", False)),
            liked_count=str(interact_data.get("likedCount", "0") or "0"),
            collected=bool(interact_data.get("collected", False)),
            collected_count=str(interact_data.get("collectedCount", "0") or "0"),
            comment_count=str(interact_data.get("commentCount", "0") or "0"),
            share_count=str(interact_data.get("shareCount", "0") or "0"),
        )

        image_list = []
        for img in image_list_data:
            if isinstance(img, dict):
                image_list.append(ImageInfo(
                    url=str(img.get("url", "") or ""),
                    width=int(img.get("width", 0) or 0),
                    height=int(img.get("height", 0) or 0),
                ))

        video = None
        if video_data:
            video = Video(
                url=str(video_data.get("url", "") or ""),
                duration=int(video_data.get("duration", 0) or 0),
                width=int(video_data.get("width", 0) or 0),
                height=int(video_data.get("height", 0) or 0),
            )

        tags = []
        for tag in tag_list_data:
            if isinstance(tag, dict):
                tag_name = tag.get("name", "") or ""
                if tag_name:
                    tags.append(str(tag_name))
            elif isinstance(tag, str):
                tags.append(tag)

        return FeedDetail(
            note_id=str(detail_data.get("noteId", feed_id) or feed_id),
            type=str(detail_data.get("type", "") or ""),
            title=str(detail_data.get("title", "") or ""),
            desc=str(detail_data.get("desc", "") or ""),
            user=user,
            image_list=image_list,
            video=video,
            interact_info=interact_info,
            tag_list=tags,
            collect_id=str(detail_data.get("collectId", "") or ""),
            xsec_token=str(detail_data.get("xsecToken", "") or ""),
        )
    except Exception as e:
        logger.error(f"解析帖子详情失败: {e}")
        return FeedDetail(note_id=feed_id)


async def get_feed_detail(
    feed_id: str,
    xsec_token: str = "",
    load_comments: bool = True,
    max_comments: int = 100,
) -> FeedDetailResponse:
    """
    获取帖子详情（含评论）
    对应 Go: GetFeedDetail()

    :param feed_id: 帖子 ID
    :param xsec_token: 安全 token
    :param load_comments: 是否加载评论
    :param max_comments: 最大加载评论数
    :return: FeedDetailResponse
    """
    try:
        page = await get_browser_page()

        # 构建 URL
        url = XHS_EXPLORE_URL.format(feed_id=feed_id)
        if xsec_token:
            url += f"?xsec_token={xsec_token}&xsec_source=pc_feed"

        logger.info(f"获取帖子详情: {feed_id}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # 提取帖子详情
        feed_detail = await _extract_feed_detail(page, feed_id)

        # 加载评论
        comment_list = CommentList()
        if load_comments:
            config = CommentLoadConfig(
                max_comment_items=max_comments,
                scroll_speed="normal",
            )

            logger.info("开始加载评论...")
            await _load_all_comments(page, config)

            # 首先尝试从 JS 状态获取评论
            comments = await _parse_comments_from_state(page)

            # 如果 JS 状态获取失败，从 DOM 获取
            if not comments:
                logger.info("从 JS 状态获取评论失败，尝试从 DOM 获取")
                comments = await _parse_comments_from_dom(page)

            total_count = await _get_total_comment_count(page)

            comment_list = CommentList(
                comments=comments,
                total_count=total_count or len(comments),
                has_more=len(comments) < (total_count or 0),
            )

            logger.info(f"加载评论完成，共 {len(comments)} 条，总计 {comment_list.total_count} 条")

        return FeedDetailResponse(
            feed_detail=feed_detail,
            comment_list=comment_list,
        )

    except Exception as e:
        logger.error(f"获取帖子详情失败 {feed_id}: {e}")
        raise
