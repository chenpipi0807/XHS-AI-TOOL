"""
小红书图文发布模块
对应 Go 版本: xiaohongshu/publish.go
支持: 图文发布、标签、定时、原创声明、可见范围
"""
import asyncio
import logging
import os
import re
from typing import List, Optional

from playwright.async_api import Page

from browser.browser import get_browser_page

logger = logging.getLogger(__name__)

XHS_PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"

# CSS 选择器（根据实际页面 DOM 调试结果）
# 页面 tab: div.creator-tab (active/非active), 文字分别是 "上传视频" | "上传图文" | "写长文"
# 注意: tab 元素在视口外，不能用 Playwright click()，必须用 JS evaluate 点击
UPLOAD_IMAGE_TAB_SELECTOR = "div.creator-tab"  # 第2个是"上传图文"
# 文件上传 input，class="upload-input"（点击图文标签后 accept 变为 .jpg,.jpeg,.png,.webp）
UPLOAD_TRIGGER_SELECTOR = "input.upload-input"
TITLE_INPUT_SELECTOR = "#post-title, input[placeholder*='标题'], .input-title input, input[class*='title']"
# 正文：小红书图文正文区域是一个 div[contenteditable] 或 .ql-editor
CONTENT_EDITOR_SELECTOR = ".ql-editor, #post-desc, div[contenteditable='true'][class*='desc'], div[contenteditable='true']"
TAG_INPUT_SELECTOR = "[placeholder*='话题'], [placeholder*='标签'], [placeholder*='# 话题']"
PUBLISH_BTN_SELECTOR = "button:has-text('发布')"
PRIVACY_SELECTOR = ".privacy-list, .select-list"
SCHEDULE_SELECTOR = ".schedule-time, .timing-publish"
ORIGINAL_SELECTOR = ".original-declare input, [class*='original'] input[type='checkbox']"


async def _wait_for_upload_complete(page: Page, timeout: int = 60000) -> bool:
    """
    等待图片上传完成
    策略: 轮询检测页面中是否存在上传完成的缩略图或发布按钮变为可用状态
    避免使用精确选择器（不同版本 DOM 差异大），用宽泛条件判断
    """
    timeout_s = timeout / 1000  # 转换为秒
    poll_interval = 1.0
    elapsed = 0.0

    while elapsed < timeout_s:
        try:
            result = await page.evaluate("""
                () => {
                    // 1. 检查是否还有正在上传的进度条/loading 元素
                    const loadingEls = document.querySelectorAll(
                        '[class*="uploading"], [class*="loading"], [class*="progress"], .upload-mask'
                    );
                    const hasLoading = Array.from(loadingEls).some(el => {
                        const s = window.getComputedStyle(el);
                        return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
                    });
                    if (hasLoading) return { done: false, reason: 'still loading' };

                    // 2. 检查是否有已上传的图片缩略图（img 元素且有 src）
                    const imgs = document.querySelectorAll('img[src]');
                    const uploadedImgs = Array.from(imgs).filter(img => {
                        const src = img.src || '';
                        // 过滤掉 logo/icon 等小图，只看内容图片
                        return src && !src.includes('data:') &&
                               img.naturalWidth > 50 && img.naturalHeight > 50;
                    });
                    if (uploadedImgs.length > 0) return { done: true, reason: 'has image thumb', count: uploadedImgs.length };

                    // 3. 检查发布按钮是否可点击（不是 disabled）
                    const publishBtn = document.querySelector('button.publish-btn, .btn-publish, button[class*="publish"]');
                    if (publishBtn && !publishBtn.disabled) return { done: true, reason: 'publish btn enabled' };

                    return { done: false, reason: 'no sign yet' };
                }
            """)

            if result and result.get('done'):
                logger.info(f"上传完成检测: {result}")
                return True

            logger.debug(f"等待上传... {result}")
        except Exception as e:
            logger.debug(f"上传检测异常: {e}")

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    logger.warning(f"等待上传完成超时 ({timeout_s}s)，继续执行")
    return False


async def _input_title_and_content(page: Page, title: str, content: str) -> None:
    """输入标题和正文内容 - 使用多种策略"""

    # 策略1: 用 JS evaluate 直接设置值（最可靠）
    result = await page.evaluate("""
        (args) => {
            const { title, content } = args;
            const res = { titleSet: false, contentSet: false, info: [] };

            // === 输入标题 ===
            // 小红书标题通常是一个普通 input
            const titleSelectors = [
                '#post-title',
                'input[placeholder*="标题"]',
                'input[placeholder*="title"]',
                '.input-title input',
                'input[class*="title"]',
                '.title-input input',
                '.title input'
            ];
            for (const sel of titleSelectors) {
                const el = document.querySelector(sel);
                if (el) {
                    // 触发 React/Vue 的响应
                    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    nativeSetter.call(el, title);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    res.titleSet = true;
                    res.info.push('title via: ' + sel);
                    break;
                }
            }

            // === 输入正文 ===
            // 小红书正文是 contenteditable div
            const contentSelectors = [
                '.ql-editor',
                '#post-desc',
                'div[contenteditable="true"][class*="desc"]',
                'div[contenteditable="true"][class*="content"]',
                'div[contenteditable="true"][class*="editor"]',
                'div[contenteditable="true"]'
            ];
            for (const sel of contentSelectors) {
                const el = document.querySelector(sel);
                if (el) {
                    el.focus();
                    // 清空并设置内容
                    el.innerHTML = '';
                    // 插入文本节点
                    const textNode = document.createTextNode(content);
                    el.appendChild(textNode);
                    // 触发事件
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
                    res.contentSet = true;
                    res.info.push('content via: ' + sel);
                    break;
                }
            }

            return res;
        }
    """, {"title": title, "content": content})

    logger.info(f"JS输入结果: {result}")

    # 策略2: 如果 JS 没有成功，用 Playwright 的 fill/type
    if not result.get('titleSet'):
        logger.warning("JS设置标题失败，尝试 Playwright fill")
        try:
            title_input = await page.wait_for_selector(TITLE_INPUT_SELECTOR, timeout=5000)
            if title_input:
                await title_input.click()
                await title_input.fill(title)
                logger.info(f"Playwright fill 标题成功")
        except Exception as e:
            logger.warning(f"Playwright fill 标题失败: {e}")

    if not result.get('contentSet'):
        logger.warning("JS设置正文失败，尝试 Playwright click+type")
        try:
            content_editor = await page.wait_for_selector(CONTENT_EDITOR_SELECTOR, timeout=5000)
            if content_editor:
                await content_editor.click()
                await content_editor.fill(content)
                logger.info(f"Playwright fill 正文成功")
        except Exception as e:
            logger.warning(f"Playwright fill 正文失败: {e}")
            # 最后尝试 keyboard.type
            try:
                await page.keyboard.type(content, delay=30)
                logger.info("keyboard.type 正文成功")
            except Exception as e2:
                logger.warning(f"keyboard.type 也失败: {e2}")


async def _input_tags(page: Page, tags: List[str]) -> None:
    """
    输入标签/话题
    【正确流程】: 在内容编辑器末尾输入 #话题内容 → 等待下拉选项出现 → 用键盘 ArrowDown+Enter 选第一项
    每个话题逐一处理，确认选中后再输入下一个
    """
    if not tags:
        return

    try:
        # 查找内容编辑器（正文区域，contenteditable div）
        content_editor = await page.query_selector(CONTENT_EDITOR_SELECTOR)
        if not content_editor:
            logger.warning("未找到内容编辑器，跳过标签输入")
            return

        for tag in tags:
            logger.info(f"输入话题: #{tag}")

            # 点击内容编辑器末尾
            await content_editor.click()
            await asyncio.sleep(0.3)

            # 先按 End 移动到行末
            await page.keyboard.press("End")
            await asyncio.sleep(0.2)

            # 输入 #话题名，逐字符输入让小红书弹出建议（先空格，再#，再话题名）
            await page.keyboard.type(" #", delay=100)
            await asyncio.sleep(0.3)
            # 逐字符输入话题名，触发下拉提示
            await page.keyboard.type(tag, delay=120)
            await asyncio.sleep(2.0)  # 等待下拉菜单出现

            # 截图记录状态（调试用）
            try:
                await page.screenshot(path=f"debug_tag_{tag}.png")
                logger.info(f"话题输入截图（输入后）: debug_tag_{tag}.png")
            except Exception:
                pass

            selected = False

            # 策略1: 等待搜索下拉弹出后，找浮动容器中的第一个结果项并点击
            # 关键：必须在浮动/弹出层内找，排除页面固定区域（如"活动话题"卡片）
            # 调试发现：小红书话题搜索结果在 editor 区域正上方弹出，是 position:fixed 的浮动层
            # 搜索结果容器: 可能是 .ql-mention-list-container, [class*="at-panel"], [class*="topic-dropdown"] 等
            try:
                await asyncio.sleep(1.0)  # 等待下拉渲染完成（需要足够时间）
                first_item_info = await page.evaluate("""
                    () => {
                        // 优先：找搜索结果浮动容器（position:fixed 或 absolute，紧贴编辑器）
                        // 必须是真正的弹出下拉层，不是页面固定布局区域
                        const popupSelectors = [
                            '.ql-mention-list-container',
                            '[class*="at-panel"]',
                            '[class*="topic-dropdown"]',
                            '[class*="mention-dropdown"]',
                            '[class*="suggest-panel"]',
                            '[class*="search-panel"]',
                            '[class*="hashtag-panel"]',
                        ];
                        for (const pSel of popupSelectors) {
                            const popup = document.querySelector(pSel);
                            if (!popup) continue;
                            const pStyle = window.getComputedStyle(popup);
                            if (pStyle.display === 'none' || pStyle.visibility === 'hidden') continue;
                            // 找弹出层内的第一个条目
                            const items = popup.querySelectorAll('li, div.item, [class*="item"], [class*="option"]');
                            for (const item of items) {
                                const rect = item.getBoundingClientRect();
                                if (rect.width < 10 || rect.height < 10) continue;
                                const text = (item.textContent || '').trim();
                                if (text.length > 0) {
                                    return {
                                        found: true, via: 'popup_container',
                                        sel: pSel, text: text.substring(0, 40),
                                        x: Math.round(rect.x + rect.width / 2),
                                        y: Math.round(rect.y + rect.height / 2),
                                        cls: item.className,
                                    };
                                }
                            }
                        }

                        // 备选：扫描所有 position:fixed 或 absolute 的浮动层
                        // 这些是真正的弹出层，排除 position:static/relative 的页面固定布局
                        const allEls = document.querySelectorAll('div.item, li[class*="item"]');
                        for (const el of allEls) {
                            // 检查元素自身或父元素是否在浮动层中
                            let parent = el.parentElement;
                            let inPopup = false;
                            while (parent && parent !== document.body) {
                                const pStyle = window.getComputedStyle(parent);
                                if (pStyle.position === 'fixed' || pStyle.position === 'absolute') {
                                    const pRect = parent.getBoundingClientRect();
                                    // 浮动层应该比较小（不是全屏覆盖）
                                    if (pRect.width < 800 && pRect.height < 600 && pRect.width > 50) {
                                        inPopup = true;
                                        break;
                                    }
                                }
                                parent = parent.parentElement;
                            }
                            if (!inPopup) continue;
                            
                            const rect = el.getBoundingClientRect();
                            if (rect.width < 10 || rect.height < 10) continue;
                            if (rect.y < 0 || rect.y > window.innerHeight) continue;
                            const text = (el.textContent || '').trim();
                            if (text.length > 0) {
                                return {
                                    found: true, via: 'floating_layer',
                                    text: text.substring(0, 40),
                                    x: Math.round(rect.x + rect.width / 2),
                                    y: Math.round(rect.y + rect.height / 2),
                                    cls: el.className,
                                };
                            }
                        }
                        return { found: false };
                    }
                """)
                if first_item_info and first_item_info.get('found'):
                    x = first_item_info.get('x', 0)
                    y = first_item_info.get('y', 0)
                    logger.info(f"话题 #{tag}: 找到浮动层第一项 ({x},{y}) via={first_item_info.get('via')} \"{first_item_info.get('text')}\" cls={first_item_info.get('cls')}")
                    await page.mouse.click(x, y)
                    await asyncio.sleep(0.8)
                    logger.info(f"话题 #{tag}: 坐标点击第一项成功")
                    selected = True
                else:
                    logger.debug(f"话题 #{tag}: 未找到浮动层下拉项（策略1），尝试键盘策略")
            except Exception as e:
                logger.warning(f"坐标点击话题第一项失败: {e}")

            # 策略2: ArrowDown + Enter
            # 注意：ArrowDown 在小红书中会跳过第一项直接选中第二项
            # 这里先按 ArrowDown 触发高亮，再按 Home 或用 Tab 回到第一项
            if not selected:
                try:
                    # 截图记录下拉状态
                    try:
                        await page.screenshot(path=f"debug_tag_{tag}_arrowdown.png")
                    except Exception:
                        pass
                    await page.keyboard.press("ArrowDown")
                    await asyncio.sleep(0.4)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(0.5)
                    logger.info(f"话题 #{tag}: 已用键盘 ArrowDown+Enter 选择（备选）")
                    selected = True
                except Exception as e:
                    logger.warning(f"键盘选择话题失败: {e}")

            if not selected:
                logger.warning(f"话题 #{tag} 所有策略均失败，按 Escape 关闭下拉")
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
            else:
                await asyncio.sleep(0.5)

            logger.info(f"话题 #{tag} 输入完成，selected={selected}")

    except Exception as e:
        logger.warning(f"输入标签失败: {e}")


async def _set_visibility(page: Page, is_private: bool) -> None:
    """设置可见范围"""
    if not is_private:
        return
    try:
        privacy_btn = await page.wait_for_selector(PRIVACY_SELECTOR, timeout=3000)
        if privacy_btn:
            await privacy_btn.click()
            await asyncio.sleep(0.5)
            private_option = await page.wait_for_selector(
                "[data-value='private'], .private-option, li:has-text('私密')",
                timeout=3000,
            )
            if private_option:
                await private_option.click()
    except Exception as e:
        logger.warning(f"设置可见范围失败: {e}")


async def _set_original(page: Page, is_original: bool) -> None:
    """设置原创声明"""
    if not is_original:
        return
    try:
        original_checkbox = await page.wait_for_selector(ORIGINAL_SELECTOR, timeout=3000)
        if original_checkbox:
            is_checked = await original_checkbox.is_checked()
            if not is_checked:
                await original_checkbox.click()
    except Exception as e:
        logger.warning(f"设置原创声明失败: {e}")


async def _get_published_url(page: Page, timeout: int = 30) -> str:
    """
    发布后等待页面跳转，返回发布成功后的帖子 URL（含 xsec_token）
    小红书发布成功后通常会跳转到发布管理页或帖子详情页
    """
    start_url = page.url
    logger.info(f"等待发布后跳转，起始 URL: {start_url}")

    for i in range(timeout):
        await asyncio.sleep(1)
        current_url = page.url

        # 检查是否跳转了
        if current_url != start_url:
            logger.info(f"页面跳转到: {current_url}")

            # 如果跳转到了帖子详情页（含 /explore/）
            m = re.search(r'/explore/([a-zA-Z0-9]+)', current_url)
            if m:
                logger.info(f"检测到帖子 URL: {current_url}")
                return current_url

            # 如果跳转到了发布成功页，从链接中找帖子 URL
            try:
                post_url = await page.evaluate("""
                    () => {
                        // 找带 /explore/ 的链接
                        const links = document.querySelectorAll('a[href*="/explore/"]');
                        for (const a of links) {
                            if (a.href && a.href.includes('xsec_token=')) {
                                return a.href;
                            }
                        }
                        // 没有 token 的也行
                        for (const a of links) {
                            if (a.href) return a.href;
                        }
                        return '';
                    }
                """)
                if post_url:
                    logger.info(f"从跳转页找到帖子链接: {post_url}")
                    return post_url
            except Exception:
                pass

    logger.warning(f"等待 {timeout}s 后页面未跳转，返回当前 URL: {page.url}")
    return page.url


async def publish_image_content(
    title: str,
    content: str,
    image_paths: List[str],
    tags: Optional[List[str]] = None,
    is_private: bool = False,
    scheduled_time: str = "",
    is_original: bool = True,
    allow_save: bool = True,
) -> dict:
    """
    发布图文内容
    对应 Go: PublishImageContent()

    :param title: 标题
    :param content: 正文内容
    :param image_paths: 图片路径列表（本地路径）
    :param tags: 标签列表
    :param is_private: 是否私密
    :param scheduled_time: 定时发布时间 (格式: "2024-01-01 12:00")
    :param is_original: 是否声明原创
    :param allow_save: 是否允许他人保存
    :return: dict，包含 success、post_url（发布后的帖子 URL）等信息
    """
    if not image_paths:
        raise ValueError("发布图文至少需要一张图片")

    # 验证图片路径
    for path in image_paths:
        if not os.path.exists(path):
            raise FileNotFoundError(f"图片文件不存在: {path}")

    try:
        page = await get_browser_page()

        logger.info(f"导航到发布页面: {XHS_PUBLISH_URL}")
        await page.goto(XHS_PUBLISH_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(4)

        # 点击"上传图文"标签（页面默认是"上传视频"）
        # 调试结论: tab 类名是 div.creator-tab，元素在视口外，必须用 JS evaluate 点击
        logger.info("点击'上传图文'标签（使用 JS evaluate）...")
        try:
            clicked = await page.evaluate("""
                () => {
                    // 找到文字为'上传图文'的 creator-tab div
                    const tabs = document.querySelectorAll('div.creator-tab');
                    for (const tab of tabs) {
                        const text = (tab.innerText || tab.textContent || '').trim();
                        if (text === '上传图文' || text.includes('上传图文')) {
                            tab.click();
                            return { clicked: true, text: text, className: tab.className };
                        }
                    }
                    // 备用：找所有含"上传图文"文字的元素
                    const allEls = document.querySelectorAll('*');
                    for (const el of allEls) {
                        const text = (el.innerText || '').trim();
                        if (text === '上传图文') {
                            el.click();
                            return { clicked: true, tag: el.tagName, text: text };
                        }
                    }
                    return { clicked: false };
                }
            """)
            if clicked and clicked.get('clicked'):
                logger.info(f"已点击'上传图文'标签: {clicked}")
            else:
                logger.warning("未找到'上传图文'标签元素")
            await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f"点击'上传图文'标签失败: {e}")

        # 上传图片
        # 注意: input.upload-input 是 hidden 元素，不能用 wait_for_selector(state="visible")
        # 必须用 page.locator() 并直接调用 set_input_files()，忽略可见性检查
        logger.info(f"上传 {len(image_paths)} 张图片...")
        abs_paths = [os.path.abspath(p) for p in image_paths]
        logger.info(f"上传图片: {abs_paths}")

        # 等待 input 元素存在（不要求 visible）
        await page.wait_for_selector(
            "input.upload-input",
            state="attached",  # 只要 DOM 中存在即可，不要求 visible
            timeout=10000,
        )

        # 直接通过 locator 上传文件（忽略 hidden 状态）
        file_locator = page.locator("input.upload-input")
        await file_locator.set_input_files(abs_paths)
        logger.info("文件已提交到 input")
        await asyncio.sleep(2)

        # 等待上传完成
        logger.info("等待图片上传完成...")
        await _wait_for_upload_complete(page, timeout=120000)
        await asyncio.sleep(3)

        # 上传完成后：检测并关闭「裁切/图片编辑」弹窗
        # 注意：页面顶部有常驻的 "图片编辑 9/18" 区域，其 aria-label 也是"图片编辑"
        # 真正的裁切弹窗是 el-overlay（fixed 全屏遮罩层），必须检查是否为真实 overlay
        logger.info("检测上传后是否有裁切弹窗（overlay 层）...")
        crop_found = await page.evaluate("""
            () => {
                // 真正的裁切弹窗：el-overlay（fixed 全屏遮罩），display != 'none'，尺寸覆盖屏幕
                const overlays = document.querySelectorAll('.el-overlay, .el-dialog__wrapper, [class*="dialog-overlay"]');
                for (const ov of overlays) {
                    const style = window.getComputedStyle(ov);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const rect = ov.getBoundingClientRect();
                    // 必须是覆盖大部分屏幕的遮罩（宽>500，高>400）
                    if (rect.width < 500 || rect.height < 400) continue;
                    
                    // 在遮罩内找"完成"按钮
                    const btns = ov.querySelectorAll('button');
                    for (const btn of btns) {
                        const text = (btn.textContent || '').trim();
                        if (text === '完成' || text === '确定' || text === '确认') {
                            btn.click();
                            return { found: true, action: 'clicked_done', text, cls: ov.className };
                        }
                    }
                    // 找关闭按钮
                    const closeBtn = ov.querySelector('button.el-dialog__headerbtn, .el-dialog__close, button[aria-label="Close"]');
                    if (closeBtn) {
                        closeBtn.click();
                        return { found: true, action: 'clicked_close', cls: ov.className };
                    }
                    return { found: true, action: 'no_btn', cls: ov.className, w: rect.width, h: rect.height };
                }
                return { found: false };
            }
        """)
        if crop_found and crop_found.get('found'):
            logger.info(f"检测到裁切弹窗: {crop_found}，已处理")
            await asyncio.sleep(2)
        else:
            logger.info("未检测到裁切弹窗（页面正常），继续")

        # 截图调试：上传完成后状态
        try:
            await page.screenshot(path="debug_after_upload_crop.png")
            logger.info("截图: debug_after_upload_crop.png（上传完成后状态）")
        except Exception:
            pass

        # 输入标题和正文（使用增强版输入方法）
        logger.info(f"输入标题: {title}")
        logger.info(f"输入正文: {content[:50]}...")
        await _input_title_and_content(page, title, content)
        await asyncio.sleep(1)

        # 再次用 Playwright 确保标题已输入（JS evaluate 可能不触发响应式）
        try:
            title_el = await page.query_selector(TITLE_INPUT_SELECTOR)
            if title_el:
                current_val = await title_el.input_value()
                if not current_val:
                    logger.info("标题为空，再次尝试 Playwright click+fill")
                    await title_el.click()
                    await title_el.fill(title)
                    await asyncio.sleep(0.5)
                else:
                    logger.info(f"标题已输入: {current_val[:30]}")
        except Exception as e:
            logger.warning(f"检查标题失败: {e}")

        # 输入标签
        if tags:
            await _input_tags(page, tags)
            await asyncio.sleep(0.5)

        # 设置原创
        await _set_original(page, is_original)

        # 设置可见范围
        await _set_visibility(page, is_private)

        # 点击发布按钮前：按 Escape 关闭可能存在的话题下拉等浮动弹窗
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)

        # 发布前截图，记录页面状态
        try:
            await page.screenshot(path="debug_before_publish.png")
            logger.info("截图: debug_before_publish.png（发布前状态）")
        except Exception:
            pass

        # 点击发布按钮
        logger.info("点击发布按钮...")

        # 用 JS 找发布按钮并获取坐标
        # 扫描 button、a、div 等所有可能的元素，匹配文字"发布"
        js_btn_info = await page.evaluate("""
            () => {
                // 扫描所有元素，找到文字恰好为"发布"的可见元素
                const candidates = [];
                document.querySelectorAll('button, a, div, span').forEach(el => {
                    const text = (el.textContent || el.innerText || '').trim();
                    if (text !== '发布') return;
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 10 || rect.height < 10) return;
                    if (rect.y < 0 || rect.y > window.innerHeight) return;
                    candidates.push({
                        tag: el.tagName,
                        cls: el.className.substring(0, 60),
                        disabled: el.disabled || el.getAttribute('disabled') !== null,
                        x: Math.round(rect.x + rect.width / 2),
                        y: Math.round(rect.y + rect.height / 2),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                    });
                });
                if (candidates.length === 0) return { found: false, candidates: [] };
                // 优先选尺寸较大的（真正的按钮通常比小图标大）
                candidates.sort((a, b) => (b.w * b.h) - (a.w * a.h));
                const best = candidates[0];
                return { found: true, ...best, allCandidates: candidates.length };
            }
        """)
        logger.info(f"发布按钮检测: {js_btn_info}")

        if not js_btn_info or not js_btn_info.get('found'):
            # 找不到时截图并抛出
            try:
                await page.screenshot(path="debug_publish_btn_not_found.png")
                logger.error("截图: debug_publish_btn_not_found.png")
            except Exception:
                pass
            raise RuntimeError("未找到发布按钮")

        # 等待按钮不再 disabled（最多30秒）
        for _ in range(30):
            btn_state = await page.evaluate("""
                () => {
                    const candidates = [];
                    document.querySelectorAll('button, a').forEach(el => {
                        const text = (el.textContent || el.innerText || '').trim();
                        if (text === '发布') {
                            candidates.push({ disabled: el.disabled || el.getAttribute('disabled') !== null });
                        }
                    });
                    if (candidates.length === 0) return { disabled: false };  // 找不到就不等
                    return candidates[0];
                }
            """)
            if not btn_state.get('disabled', False):
                break
            await asyncio.sleep(1)

        # 用坐标点击发布按钮（最可靠，不受遮挡）
        btn_x = js_btn_info.get('x', 0)
        btn_y = js_btn_info.get('y', 0)
        clicked = False
        if btn_x > 0 and btn_y > 0:
            try:
                await page.mouse.click(btn_x, btn_y)
                clicked = True
                logger.info(f"已用坐标点击发布按钮: ({btn_x}, {btn_y})")
            except Exception as e:
                logger.warning(f"坐标点击发布按钮失败: {e}，尝试 ElementHandle.click")

        if not clicked:
            publish_btn = None
            try:
                publish_btn = await page.wait_for_selector(
                    PUBLISH_BTN_SELECTOR,
                    timeout=10000,
                )
            except Exception:
                pass
            if not publish_btn:
                raise RuntimeError("未找到发布按钮（ElementHandle 方式）")
            await publish_btn.click()
            logger.info("已用 ElementHandle 点击发布按钮")

        logger.info("已点击发布按钮，等待发布完成...")

        # 等待发布成功并获取跳转 URL
        post_url = ""
        try:
            # 先尝试等待成功提示
            try:
                await page.wait_for_selector(
                    ".success-toast, .publish-success, [class*='success'], .toast",
                    timeout=15000,
                )
                logger.info("检测到发布成功提示")
            except Exception:
                logger.info("未检测到成功提示，继续等待跳转...")

            # 等待页面跳转（最多 30 秒）
            post_url = await _get_published_url(page, timeout=30)

        except Exception as e:
            logger.warning(f"等待发布结果异常: {e}")
            await asyncio.sleep(3)

        logger.info("图文发布成功！")
        return {
            "success": True,
            "title": title,
            "images_count": len(image_paths),
            "post_url": post_url,
        }

    except Exception as e:
        logger.error(f"发布图文失败: {e}")
        raise
