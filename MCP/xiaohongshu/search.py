"""
小红书搜索模块
对应 Go 版本: xiaohongshu/search.go
支持关键词搜索 + 多维度筛选器
"""
import asyncio
import logging
from typing import List, Optional
from urllib.parse import quote

from browser.browser import get_browser_page
from xiaohongshu.types import Feed, NoteCard, User, InteractInfo, Cover, FilterOption

logger = logging.getLogger(__name__)

XHS_SEARCH_URL = "https://www.xiaohongshu.com/search_result"

# 筛选器类型常量（对应 Go internalFilterOption）
FILTER_TYPE_SORT = 0          # 排序方式
FILTER_TYPE_NOTE_TYPE = 1     # 笔记类型
FILTER_TYPE_PUBLISH_TIME = 2  # 发布时间
FILTER_TYPE_SEARCH_SCOPE = 3  # 搜索范围


def _convert_sort_by(sort_by: str) -> Optional[dict]:
    """转换排序方式筛选器"""
    sort_map = {
        "general": {"filterType": FILTER_TYPE_SORT, "value": 0},
        "popularity_descending": {"filterType": FILTER_TYPE_SORT, "value": 1},
        "time_descending": {"filterType": FILTER_TYPE_SORT, "value": 2},
    }
    return sort_map.get(sort_by)


def _convert_note_type(note_type: str) -> Optional[dict]:
    """转换笔记类型筛选器"""
    type_map = {
        "0": {"filterType": FILTER_TYPE_NOTE_TYPE, "value": 0},
        "1": {"filterType": FILTER_TYPE_NOTE_TYPE, "value": 1},  # 视频
        "2": {"filterType": FILTER_TYPE_NOTE_TYPE, "value": 2},  # 图文
    }
    return type_map.get(note_type)


def _convert_publish_time(publish_time: str) -> Optional[dict]:
    """转换发布时间筛选器"""
    time_map = {
        "不限": {"filterType": FILTER_TYPE_PUBLISH_TIME, "value": 0},
        "一天内": {"filterType": FILTER_TYPE_PUBLISH_TIME, "value": 1},
        "一周内": {"filterType": FILTER_TYPE_PUBLISH_TIME, "value": 2},
        "半年内": {"filterType": FILTER_TYPE_PUBLISH_TIME, "value": 3},
    }
    return time_map.get(publish_time)


def _convert_search_scope(search_scope: str) -> Optional[dict]:
    """转换搜索范围筛选器"""
    scope_map = {
        "全部": {"filterType": FILTER_TYPE_SEARCH_SCOPE, "value": 0},
        "已关注": {"filterType": FILTER_TYPE_SEARCH_SCOPE, "value": 1},
        "同城": {"filterType": FILTER_TYPE_SEARCH_SCOPE, "value": 2},
    }
    return scope_map.get(search_scope)


def _build_search_url(keyword: str, filter_option: Optional[FilterOption] = None) -> str:
    """
    构建搜索 URL
    格式: https://www.xiaohongshu.com/search_result?keyword=xxx&type=51&filters=[...]
    """
    encoded_keyword = quote(keyword)
    base_url = f"{XHS_SEARCH_URL}?keyword={encoded_keyword}&type=51"

    if not filter_option:
        return base_url

    import json
    filters = []

    if filter_option.sort_by:
        f = _convert_sort_by(filter_option.sort_by)
        if f:
            filters.append(f)

    if filter_option.note_type:
        f = _convert_note_type(filter_option.note_type)
        if f:
            filters.append(f)

    if filter_option.publish_time:
        f = _convert_publish_time(filter_option.publish_time)
        if f:
            filters.append(f)

    if filter_option.search_scope:
        f = _convert_search_scope(filter_option.search_scope)
        if f:
            filters.append(f)

    if filters:
        filters_json = json.dumps(filters, separators=(",", ":"))
        base_url += f"&filters={quote(filters_json)}"

    return base_url


def _parse_search_feed(item: dict) -> Optional[Feed]:
    """将搜索结果字典转换为 Feed 对象"""
    try:
        note_card_data = item.get("noteCard", {}) or {}
        user_data = note_card_data.get("user", {}) or {}
        interact_data = note_card_data.get("interactInfo", {}) or {}
        cover_data = note_card_data.get("cover", {}) or {}

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

        cover = Cover(
            url=str(cover_data.get("url", "") or ""),
            url_default=str(cover_data.get("urlDefault", "") or ""),
            width=int(cover_data.get("width", 0) or 0),
            height=int(cover_data.get("height", 0) or 0),
        )

        note_card = NoteCard(
            note_id=str(note_card_data.get("noteId", "") or ""),
            type=str(note_card_data.get("type", "") or ""),
            display_title=str(note_card_data.get("displayTitle", "") or ""),
            cover=cover,
            interact_info=interact_info,
            user=user,
            xsec_token=str(note_card_data.get("xsecToken", "") or ""),
        )

        feed = Feed(
            id=str(item.get("id", "") or ""),
            note_card=note_card,
            xsec_token=str(item.get("xsecToken", "") or ""),
            track_id=str(item.get("trackId", "") or ""),
        )

        return feed if feed.id else None

    except Exception as e:
        logger.debug(f"解析搜索结果失败: {e}")
        return None


async def search_feeds(
    keyword: str,
    filter_option: Optional[FilterOption] = None
) -> List[Feed]:
    """
    搜索小红书笔记
    对应 Go: Search()
    导航到搜索页面，从 window.__INITIAL_STATE__.search.feeds 提取数据

    :param keyword: 搜索关键词
    :param filter_option: 可选筛选选项
    :return: Feed 列表
    """
    if not keyword.strip():
        raise ValueError("搜索关键词不能为空")

    try:
        page = await get_browser_page()

        search_url = _build_search_url(keyword, filter_option)
        logger.info(f"搜索关键词: {keyword}, URL: {search_url}")

        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)  # 等待 JS 状态初始化

        # 从 window.__INITIAL_STATE__ 提取搜索结果
        feeds_data = await page.evaluate("""
            () => {
                try {
                    const state = window.__INITIAL_STATE__;
                    if (!state || !state.search || !state.search.feeds) {
                        return [];
                    }
                    return state.search.feeds;
                } catch(e) {
                    return [];
                }
            }
        """)

        if not feeds_data:
            logger.warning("未获取到搜索结果，等待后重试")
            await asyncio.sleep(3)
            feeds_data = await page.evaluate("""
                () => {
                    try {
                        const state = window.__INITIAL_STATE__;
                        if (!state || !state.search || !state.search.feeds) {
                            return [];
                        }
                        return state.search.feeds;
                    } catch(e) {
                        return [];
                    }
                }
            """)

        if not feeds_data:
            logger.warning(f"关键词 '{keyword}' 未找到搜索结果")
            return []

        feeds = []
        for item in feeds_data:
            feed = _parse_search_feed(item)
            if feed:
                feeds.append(feed)

        logger.info(f"关键词 '{keyword}' 搜索到 {len(feeds)} 个结果")
        return feeds

    except Exception as e:
        logger.error(f"搜索失败: {e}")
        raise
