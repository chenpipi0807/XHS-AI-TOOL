"""
小红书评论模块
对应 Go 版本: xiaohongshu/comment_feed.go
支持: 发表评论、回复评论

【已调试确认的 DOM 结构 - 2026-03-03】
帖子详情页 URL: https://www.xiaohongshu.com/explore/<id>?xsec_token=...
  评论输入框: <p id="content-textarea" class="content-input" contenteditable="true">
              初始 w=107 (折叠状态)，点击底部"评论"按钮后 w=367 (展开)
  发送按钮:  <button class="btn submit gray">发送</button>
  取消按钮:  <button class="btn cancel">取消</button>
  容器:      <div class="input-box"><div class="content-edit"><p id="content-textarea">...

正确流程:
  1. 导航到帖子 URL（必须带 xsec_token，否则 404）
  2. 等待页面加载 (3-4秒)
  3. 点击底部"评论"文字按钮（触发输入框展开）
  4. 找到 p#content-textarea 并点击
  5. keyboard.type() 输入内容
  6. 点击 button.btn.submit 发送
"""
import asyncio
import logging
import re

from playwright.async_api import Page

from browser.browser import get_browser_page

logger = logging.getLogger(__name__)

XHS_EXPLORE_URL = "https://www.xiaohongshu.com/explore/{feed_id}"


async def _navigate_to_feed_by_url(page: Page, url: str) -> None:
    """
    直接用完整 URL 导航到帖子（支持 /discovery/item/ 和 /explore/）
    策略:
    - 若成功到达帖子页面（URL 含帖子 ID）则直接返回
    - 若被重定向到其他页面（notification / homefeed 等），继续尝试（小红书有时会有短暂重定向但帖子仍可评论）
    - 若 error_code=300031 (笔记刚发布审核中)，等待后重试，最多重试2次
    """
    # 提取帖子 ID
    feed_id_m = re.search(r'/(?:explore|discovery/item)/([a-zA-Z0-9]+)', url)
    feed_id = feed_id_m.group(1) if feed_id_m else ''

    if feed_id and feed_id in page.url:
        logger.debug("已在帖子页面，跳过导航")
        return

    logger.info(f"导航到帖子: {url}")

    # 等待间隔（秒）：第1次30s，第2次30s
    wait_times = [30, 30, 0]

    for attempt in range(3):
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(4)

        final_url = page.url
        logger.info(f"已到达 (第{attempt+1}次): {final_url}")

        # ⚠️ 注意：先检查错误状态，再检查 feed_id（因为 404 URL 也可能包含 feed_id 作为 redirectPath 参数）

        # error_code=300031: 笔记暂时无法浏览（刚发布审核中）
        if 'error_code=300031' in final_url:
            if attempt < 2:
                logger.warning(f"帖子暂时无法浏览 (300031)，等待 {wait_times[attempt]}s 后重试 (第{attempt+1}次)...")
                await asyncio.sleep(wait_times[attempt])
                continue
            else:
                raise RuntimeError(f"帖子页面多次重试仍无法访问 (300031): {url}")

        # 其他错误码
        if 'error_code=' in final_url:
            if attempt < 2:
                logger.warning(f"帖子访问出错 ({final_url})，等待 {wait_times[attempt]}s 后重试...")
                await asyncio.sleep(wait_times[attempt])
                continue
            raise RuntimeError(f"帖子页面访问失败 (error_code): {final_url}")

        # 404 页面
        if final_url.startswith('https://www.xiaohongshu.com/404') or '/404?' in final_url:
            if attempt < 2:
                logger.warning(f"帖子 404，等待 {wait_times[attempt]}s 后重试...")
                await asyncio.sleep(wait_times[attempt])
                continue
            raise RuntimeError(f"帖子页面 404: {final_url}")

        # 判断是否成功到达帖子页面（URL 含帖子 ID，且不是错误页）
        if feed_id and feed_id in final_url:
            logger.info(f"成功到达帖子页面: {final_url}")
            return

        # 被重定向到 homefeed 首页推荐流
        if 'channel_id=homefeed' in final_url:
            if attempt < 2:
                logger.warning(f"被重定向到首页推荐流 (第{attempt+1}次)，等待 {wait_times[attempt]}s 后重试...")
                await asyncio.sleep(wait_times[attempt])
                continue
            else:
                raise RuntimeError(f"帖子多次重试仍被重定向到首页: {url}")

        # 被重定向到 notification / 其他非帖子页 - 继续（小红书有时这样，但帖子内容仍可加载）
        if '/notification' in final_url or '/user/profile' in final_url:
            logger.warning(f"被重定向到 {final_url}，尝试直接继续（评论功能可能仍可用）")
            # 再次尝试直接导航
            if attempt < 2:
                await asyncio.sleep(wait_times[attempt])
                continue
            # 最后一次：放弃报错，直接继续（让评论步骤判断是否能用）
            logger.warning("仍被重定向，继续尝试评论")
            return

        # 成功（URL 没有出现已知异常，继续）
        return


async def _navigate_to_feed(page: Page, feed_id: str, xsec_token: str = "") -> None:
    """用 feed_id + xsec_token 导航到帖子"""
    if feed_id in page.url:
        logger.debug("已在帖子页面")
        return

    url = XHS_EXPLORE_URL.format(feed_id=feed_id)
    if xsec_token:
        url += f"?xsec_token={xsec_token}&xsec_source=pc_feed"

    logger.info(f"导航到帖子: {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(4)

    final_url = page.url
    if '404' in final_url or 'error_code=3' in final_url:
        raise RuntimeError(f"帖子页面访问失败 (404): {final_url}")
    logger.info(f"已到达: {final_url}")


async def _click_comment_trigger(page: Page) -> None:
    """
    点击底部'评论'触发按钮，使评论输入框展开
    【调试确认】点击文字为'评论'的 span（y坐标>700 底部区域）
    点击后 p#content-textarea 的宽度从 107 变为 367
    """
    clicked = await page.evaluate("""
        () => {
            // 找底部区域"评论"文字元素
            const spans = document.querySelectorAll('span, div, a');
            for (const el of spans) {
                const text = (el.textContent || el.innerText || '').trim();
                if (text === '评论') {
                    const rect = el.getBoundingClientRect();
                    if (rect.y > 600) {  // 底部区域
                        // 点击父元素（更大的点击区域）
                        const parent = el.parentElement;
                        if (parent) parent.click();
                        el.click();
                        return { clicked: true, tag: el.tagName, y: Math.round(rect.y) };
                    }
                }
            }
            // 备用：直接点击 p#content-textarea
            const input = document.querySelector('p#content-textarea');
            if (input) {
                input.click();
                input.focus();
                return { clicked: true, direct: true };
            }
            return { clicked: false };
        }
    """)
    logger.info(f"点击评论触发器: {clicked}")
    await asyncio.sleep(1.5)


async def _find_and_focus_comment_input(page: Page) -> bool:
    """
    找到评论输入框并使其获得焦点
    【已确认选择器】: p#content-textarea.content-input[contenteditable="true"]
    """
    # 先尝试 Playwright 的 click
    try:
        # 等待输入框（attached 状态即可，它一直存在）
        input_el = await page.wait_for_selector("p#content-textarea", state="attached", timeout=5000)
        if input_el:
            await input_el.scroll_into_view_if_needed()
            await asyncio.sleep(0.3)
            await input_el.click()
            await asyncio.sleep(0.3)
            logger.info("已点击 p#content-textarea")
            return True
    except Exception as e:
        logger.warning(f"click p#content-textarea 失败: {e}")

    # 备用：JS focus
    result = await page.evaluate("""
        () => {
            const el = document.querySelector('p#content-textarea') ||
                        document.querySelector('#content-textarea') ||
                        document.querySelector('p.content-input[contenteditable]');
            if (el) {
                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                el.focus();
                el.click();
                return true;
            }
            return false;
        }
    """)
    logger.info(f"JS focus 结果: {result}")
    return result


async def _type_comment_content(page: Page, content: str) -> None:
    """
    向评论输入框输入内容
    【注意】p#content-textarea 是 contenteditable，不能用 fill()
    必须用 keyboard.type()
    """
    # 先清空（Ctrl+A 全选后删除）
    await page.keyboard.press("Control+a")
    await asyncio.sleep(0.1)
    await page.keyboard.press("Delete")
    await asyncio.sleep(0.1)

    # 逐字输入
    await page.keyboard.type(content, delay=80)
    await asyncio.sleep(0.8)

    # 验证输入内容
    actual = await page.evaluate("""
        () => {
            const el = document.querySelector('p#content-textarea') ||
                        document.querySelector('#content-textarea');
            return el ? (el.textContent || el.innerText || '') : '';
        }
    """)
    logger.info(f"输入框实际内容: '{actual}'")

    if content not in actual:
        logger.warning(f"输入验证失败，期望包含 '{content}'，实际 '{actual}'")
        # 尝试 JS 设置
        await page.evaluate("""
            (text) => {
                const el = document.querySelector('p#content-textarea') ||
                            document.querySelector('#content-textarea');
                if (el) {
                    el.focus();
                    // 用 execCommand 输入（兼容 contenteditable）
                    document.execCommand('selectAll', false, null);
                    document.execCommand('insertText', false, text);
                }
            }
        """, content)
        await asyncio.sleep(0.5)


async def _click_send_button(page: Page) -> bool:
    """
    点击发送按钮
    【已确认】截图显示：红色圆角按钮，文字"发送"，位于右下角
    选择器: button.btn.submit 或 JS 找文字为"发送"的按钮
    """
    # 方式1: JS 找文字为"发送"的按钮（最可靠）
    js_result = await page.evaluate("""
        () => {
            const buttons = document.querySelectorAll('button');
            for (const btn of buttons) {
                const text = (btn.textContent || btn.innerText || '').trim();
                if (text === '发送') {
                    const rect = btn.getBoundingClientRect();
                    btn.click();
                    return { clicked: true, text: text, cls: String(btn.className || ''), rect: { x: Math.round(rect.x), y: Math.round(rect.y) } };
                }
            }
            return { clicked: false };
        }
    """)
    if js_result and js_result.get('clicked'):
        logger.info(f"JS 点击发送按钮: {js_result}")
        await asyncio.sleep(2)
        return True

    # 方式2: Playwright 选择器
    for selector in ["button.btn.submit", "button.submit", ".btn.submit", "button:has-text('发送')"]:
        try:
            btn = await page.wait_for_selector(selector, timeout=2000, state="attached")
            if btn:
                text = await btn.text_content()
                logger.info(f"找到发送按钮: {selector}, text='{text}'")
                await btn.click()
                await asyncio.sleep(2)
                return True
        except Exception:
            continue

    # 方式3: Enter 键（有些场景按 Enter 也能发送）
    try:
        await page.keyboard.press("Enter")
        await asyncio.sleep(1.5)
        logger.info("已按 Enter 发送")
        return True
    except Exception:
        pass

    logger.warning("未找到发送按钮")
    return False


async def post_comment(
    feed_id: str,
    content: str,
    xsec_token: str = "",
    feed_url: str = "",
) -> bool:
    """
    对帖子发表评论

    :param feed_id: 帖子 ID
    :param content: 评论内容
    :param xsec_token: 访问令牌（没有会 404）
    :param feed_url: 完整帖子 URL（优先使用，可直接包含 xsec_token）
    :return: 成功返回 True
    """
    logger.info(f"在帖子 {feed_id} 发表评论: {content}")

    try:
        page = await get_browser_page()

        # 1. 导航到帖子
        if feed_url:
            await _navigate_to_feed_by_url(page, feed_url)
        else:
            await _navigate_to_feed(page, feed_id, xsec_token)

        # 2. 点击底部"评论"按钮（使输入框展开到 w=367）
        logger.info("点击评论触发按钮...")
        await _click_comment_trigger(page)

        # 3. 点击输入框并获得焦点
        logger.info("定位评论输入框...")
        focused = await _find_and_focus_comment_input(page)
        if not focused:
            raise RuntimeError("无法定位评论输入框")

        # 4. 输入评论内容
        logger.info(f"输入评论: {content}")
        await _type_comment_content(page, content)

        # 5. 点击发送
        logger.info("点击发送...")
        sent = await _click_send_button(page)

        if sent:
            await asyncio.sleep(2)
            logger.info("评论发送成功！")
            return True
        else:
            raise RuntimeError("未能成功点击发送按钮")

    except Exception as e:
        logger.error(f"发表评论失败: {e}")
        raise


async def reply_comment(
    feed_id: str,
    comment_id: str,
    content: str,
    xsec_token: str = "",
    feed_url: str = "",
) -> bool:
    """
    回复帖子中的某条评论

    :param feed_id: 帖子 ID
    :param comment_id: 要回复的评论 ID
    :param content: 回复内容
    :param xsec_token: 访问令牌
    :param feed_url: 完整帖子 URL（优先使用）
    :return: 成功返回 True
    """
    logger.info(f"在帖子 {feed_id} 回复评论 {comment_id}: {content}")

    try:
        page = await get_browser_page()

        # 1. 导航到帖子
        if feed_url:
            await _navigate_to_feed_by_url(page, feed_url)
        else:
            await _navigate_to_feed(page, feed_id, xsec_token)

        # 2. 找到目标评论并点击"回复"
        logger.info(f"查找评论 {comment_id}...")
        clicked_reply = await page.evaluate("""
            (commentId) => {
                // 尝试找 data-id 匹配的评论
                const els = document.querySelectorAll(`[data-id="${commentId}"], #comment-${commentId}`);
                for (const el of els) {
                    const replyBtn = el.querySelector('.reply-btn, [class*="reply"]');
                    if (replyBtn) {
                        replyBtn.click();
                        return { clicked: true, method: 'data-id' };
                    }
                }
                return { clicked: false };
            }
        """, comment_id)
        logger.info(f"点击回复按钮: {clicked_reply}")
        await asyncio.sleep(1)

        # 3. 点击评论输入框
        await _click_comment_trigger(page)
        focused = await _find_and_focus_comment_input(page)
        if not focused:
            raise RuntimeError("无法定位评论输入框")

        # 4. 输入回复内容
        await _type_comment_content(page, content)

        # 5. 发送
        sent = await _click_send_button(page)
        if sent:
            await asyncio.sleep(2)
            logger.info("回复发送成功！")
            return True
        else:
            raise RuntimeError("发送回复失败")

    except Exception as e:
        logger.error(f"回复评论失败: {e}")
        raise
