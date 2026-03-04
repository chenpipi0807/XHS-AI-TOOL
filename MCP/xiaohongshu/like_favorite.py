"""
小红书点赞/收藏模块
对应 Go 版本: xiaohongshu/like_favorite.go
选择器:
  点赞: .interact-container .left .like-lottie
  收藏: .interact-container .left .reds-icon.collect-icon
"""
import asyncio
import logging
import random

from playwright.async_api import Page

from browser.browser import get_browser_page

logger = logging.getLogger(__name__)

XHS_EXPLORE_URL = "https://www.xiaohongshu.com/explore/{feed_id}"

# CSS 选择器（与 Go 版本一致）
LIKE_BTN_SELECTOR = ".interact-container .left .like-lottie"
COLLECT_BTN_SELECTOR = ".interact-container .left .reds-icon.collect-icon"

# 备用选择器
LIKE_BTN_FALLBACKS = [
    ".like-btn",
    ".like-action",
    "[class*='like-lottie']",
    ".interact-btn.like",
]
COLLECT_BTN_FALLBACKS = [
    ".collect-btn",
    ".collect-action",
    "[class*='collect-icon']",
    ".interact-btn.collect",
]


async def _navigate_to_feed(page: Page, feed_id: str, xsec_token: str = "") -> None:
    """导航到帖子详情页"""
    url = XHS_EXPLORE_URL.format(feed_id=feed_id)
    if xsec_token:
        url += f"?xsec_token={xsec_token}&xsec_source=pc_feed"

    current_url = page.url
    if feed_id in current_url:
        return

    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)


async def _get_interact_state(page: Page, feed_id: str) -> tuple:
    """
    从 window.__INITIAL_STATE__ 获取当前点赞/收藏状态
    对应 Go: getInteractState()
    返回 (liked, collected)
    """
    state = await page.evaluate(f"""
        () => {{
            try {{
                const state = window.__INITIAL_STATE__;
                if (!state || !state.note || !state.note.noteDetailMap) {{
                    return {{ liked: false, collected: false }};
                }}
                const noteMap = state.note.noteDetailMap;
                const note = noteMap['{feed_id}'] || noteMap[Object.keys(noteMap)[0]];
                if (!note || !note.interactInfo) {{
                    return {{ liked: false, collected: false }};
                }}
                return {{
                    liked: !!note.interactInfo.liked,
                    collected: !!note.interactInfo.collected,
                }};
            }} catch(e) {{
                return {{ liked: false, collected: false }};
            }}
        }}
    """)
    liked = bool(state.get("liked", False)) if state else False
    collected = bool(state.get("collected", False)) if state else False
    return liked, collected


async def _click_interact_btn(page: Page, primary_selector: str, fallbacks: list) -> bool:
    """点击互动按钮（带人类行为模拟）"""
    # 尝试主选择器
    try:
        btn = await page.wait_for_selector(primary_selector, timeout=5000, state="visible")
        if btn:
            await asyncio.sleep(random.uniform(0.3, 0.8))
            await btn.scroll_into_view_if_needed()
            await asyncio.sleep(random.uniform(0.1, 0.3))
            await btn.click()
            await asyncio.sleep(random.uniform(0.5, 1.0))
            return True
    except Exception:
        pass

    # 尝试备用选择器
    for selector in fallbacks:
        try:
            btn = await page.query_selector(selector)
            if btn:
                visible = await btn.is_visible()
                if visible:
                    await asyncio.sleep(random.uniform(0.3, 0.8))
                    await btn.click()
                    await asyncio.sleep(0.5)
                    return True
        except Exception:
            continue

    return False


async def like_feed(
    feed_id: str,
    xsec_token: str = "",
) -> dict:
    """
    点赞帖子（如果已点赞则取消点赞）
    对应 Go: LikeAction()

    :param feed_id: 帖子 ID
    :param xsec_token: 安全 token
    :return: {"success": True, "action": "like"/"unlike", "message": "..."}
    """
    try:
        page = await get_browser_page()

        await _navigate_to_feed(page, feed_id, xsec_token)
        await asyncio.sleep(1)

        # 获取当前状态
        liked, _ = await _get_interact_state(page, feed_id)
        action = "unlike" if liked else "like"

        logger.info(f"帖子 {feed_id} 当前点赞状态: {liked}, 执行操作: {action}")

        # 点击点赞按钮
        success = await _click_interact_btn(page, LIKE_BTN_SELECTOR, LIKE_BTN_FALLBACKS)

        if not success:
            raise RuntimeError("未找到点赞按钮")

        # 验证状态变化
        await asyncio.sleep(1)
        new_liked, _ = await _get_interact_state(page, feed_id)

        if action == "like":
            msg = "点赞成功" if new_liked else "点赞操作已执行（状态未变化）"
        else:
            msg = "取消点赞成功" if not new_liked else "取消点赞已执行（状态未变化）"

        logger.info(msg)
        return {"success": True, "action": action, "message": msg}

    except Exception as e:
        logger.error(f"点赞操作失败: {e}")
        raise


async def favorite_feed(
    feed_id: str,
    xsec_token: str = "",
) -> dict:
    """
    收藏帖子（如果已收藏则取消收藏）
    对应 Go: FavoriteAction()

    :param feed_id: 帖子 ID
    :param xsec_token: 安全 token
    :return: {"success": True, "action": "collect"/"uncollect", "message": "..."}
    """
    try:
        page = await get_browser_page()

        await _navigate_to_feed(page, feed_id, xsec_token)
        await asyncio.sleep(1)

        # 获取当前状态
        _, collected = await _get_interact_state(page, feed_id)
        action = "uncollect" if collected else "collect"

        logger.info(f"帖子 {feed_id} 当前收藏状态: {collected}, 执行操作: {action}")

        # 点击收藏按钮
        success = await _click_interact_btn(page, COLLECT_BTN_SELECTOR, COLLECT_BTN_FALLBACKS)

        if not success:
            raise RuntimeError("未找到收藏按钮")

        # 验证状态变化
        await asyncio.sleep(1)
        _, new_collected = await _get_interact_state(page, feed_id)

        if action == "collect":
            msg = "收藏成功" if new_collected else "收藏操作已执行（状态未变化）"
        else:
            msg = "取消收藏成功" if not new_collected else "取消收藏已执行（状态未变化）"

        logger.info(msg)
        return {"success": True, "action": action, "message": msg}

    except Exception as e:
        logger.error(f"收藏操作失败: {e}")
        raise
