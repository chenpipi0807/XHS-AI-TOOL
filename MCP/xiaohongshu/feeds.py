"""
小红书首页推荐 Feed 列表模块
对应 Go 版本: xiaohongshu/feeds.go
从页面 DOM 提取 Feed 数据（window.__INITIAL_STATE__ 是 Vue 响应式对象，有循环引用，
需要在 JS 端手动序列化为纯对象，不能直接 return 整个对象）
"""
import asyncio
import logging
from typing import List

from browser.browser import get_browser_page
from xiaohongshu.types import Feed, NoteCard, User, InteractInfo, Cover

logger = logging.getLogger(__name__)

XHS_HOME_URL = "https://www.xiaohongshu.com"


def _parse_feed_from_js(item: dict) -> Feed:
    """将 JS 提取的字典数据转换为 Feed 对象"""
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

    return Feed(
        id=str(item.get("id", "") or ""),
        note_card=note_card,
        xsec_token=str(item.get("xsecToken", "") or ""),
        track_id=str(item.get("trackId", "") or ""),
    )


async def get_feeds_list() -> List[Feed]:
    """
    获取首页推荐 Feed 列表
    对应 Go: GetFeedsList()
    导航到首页，从 window.__INITIAL_STATE__.feed.feeds 提取数据
    注意: __INITIAL_STATE__ 是 Vue 响应式对象，有循环引用，
    必须在 JS 端手动序列化为纯对象数组，不能直接 return state.feed.feeds
    """
    try:
        page = await get_browser_page()

        logger.info("导航到小红书首页获取推荐列表...")
        await page.goto(XHS_HOME_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)  # 等待 JS 状态初始化

        # 在 JS 端手动序列化，避免 Vue 响应式对象的循环引用问题
        feeds_data = await page.evaluate("""
            () => {
                try {
                    const state = window.__INITIAL_STATE__;
                    if (!state || !state.feed || !state.feed.feeds) {
                        return [];
                    }
                    const feeds = state.feed.feeds;
                    if (!feeds || !feeds.length) return [];

                    // 手动序列化为纯对象，避免循环引用
                    const result = [];
                    for (let i = 0; i < feeds.length; i++) {
                        try {
                            const item = feeds[i];
                            if (!item) continue;

                            const nc = item.noteCard || {};
                            const user = nc.user || {};
                            const interact = nc.interactInfo || {};
                            const cover = nc.cover || {};

                            result.push({
                                id: String(item.id || ''),
                                xsecToken: String(item.xsecToken || ''),
                                trackId: String(item.trackId || ''),
                                noteCard: {
                                    noteId: String(nc.noteId || ''),
                                    type: String(nc.type || ''),
                                    displayTitle: String(nc.displayTitle || ''),
                                    xsecToken: String(nc.xsecToken || ''),
                                    user: {
                                        userId: String(user.userId || user.user_id || ''),
                                        nickname: String(user.nickname || ''),
                                        avatar: String(user.avatar || ''),
                                        desc: String(user.desc || ''),
                                    },
                                    interactInfo: {
                                        liked: !!interact.liked,
                                        likedCount: String(interact.likedCount || '0'),
                                        collected: !!interact.collected,
                                        collectedCount: String(interact.collectedCount || '0'),
                                        commentCount: String(interact.commentCount || '0'),
                                        shareCount: String(interact.shareCount || '0'),
                                    },
                                    cover: {
                                        url: String(cover.url || ''),
                                        urlDefault: String(cover.urlDefault || ''),
                                        width: Number(cover.width || 0),
                                        height: Number(cover.height || 0),
                                    },
                                },
                            });
                        } catch(innerErr) {
                            // 跳过单条解析失败
                        }
                    }
                    return result;
                } catch(e) {
                    return { error: String(e) };
                }
            }
        """)

        # 处理错误情况
        if isinstance(feeds_data, dict) and feeds_data.get("error"):
            logger.warning(f"JS 提取 feeds 失败: {feeds_data['error']}")
            feeds_data = []

        if not feeds_data:
            logger.warning("__INITIAL_STATE__ 未获取到 feeds，尝试从 DOM 提取...")
            await asyncio.sleep(3)
            # 降级方案：从 DOM 提取笔记卡片基础数据（包括 xsec_token）
            feeds_data = await page.evaluate("""
                () => {
                    try {
                        // 尝试从页面 DOM 的笔记卡片提取基础数据
                        // 小红书首页的笔记卡片通常是 section.note-item 或 a[href*="/explore/"]
                        const result = [];
                        // 方法1: 找所有 /explore/ 链接
                        const links = document.querySelectorAll('a[href*="/explore/"]');
                        const seen = new Set();
                        for (const link of links) {
                            try {
                                const href = link.getAttribute('href') || '';
                                // 提取 noteId 和 xsec_token
                                const idMatch = href.match(/\\/explore\\/([a-f0-9]+)/);
                                if (!idMatch) continue;
                                const noteId = idMatch[1];
                                if (seen.has(noteId)) continue;
                                seen.add(noteId);

                                // 提取 xsec_token
                                const tokenMatch = href.match(/xsec_token=([^&]+)/);
                                const xsecToken = tokenMatch ? decodeURIComponent(tokenMatch[1]) : '';

                                // 找标题
                                const card = link.closest('section, article, div[class*="card"], div[class*="note"]') || link.parentElement;
                                const titleEl = card ? card.querySelector('[class*="title"], span, p') : null;
                                const title = titleEl ? (titleEl.innerText || '').trim() : '';

                                // 找封面图
                                const imgEl = card ? card.querySelector('img') : null;
                                const coverUrl = imgEl ? (imgEl.src || imgEl.getAttribute('data-src') || '') : '';

                                result.push({
                                    id: noteId,
                                    xsecToken: xsecToken,
                                    trackId: '',
                                    noteCard: {
                                        noteId: noteId,
                                        type: 'normal',
                                        displayTitle: title,
                                        xsecToken: xsecToken,
                                        user: { userId: '', nickname: '', avatar: '', desc: '' },
                                        interactInfo: {
                                            liked: false, likedCount: '0',
                                            collected: false, collectedCount: '0',
                                            commentCount: '0', shareCount: '0',
                                        },
                                        cover: { url: coverUrl, urlDefault: coverUrl, width: 0, height: 0 },
                                    },
                                });
                            } catch(e2) {}
                        }
                        return result;
                    } catch(e) {
                        return [];
                    }
                }
            """)

        if not feeds_data:
            logger.warning("获取到空的 feeds 列表")
            return []

        feeds = []
        for item in feeds_data:
            try:
                feed = _parse_feed_from_js(item)
                if feed.id:
                    feeds.append(feed)
            except Exception as e:
                logger.debug(f"解析 feed 数据失败: {e}, 数据: {item}")
                continue

        logger.info(f"成功获取 {len(feeds)} 个推荐 feeds")
        return feeds

    except Exception as e:
        logger.error(f"获取 feeds 列表失败: {e}")
        raise
