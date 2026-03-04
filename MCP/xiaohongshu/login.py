"""
小红书登录模块
对应 Go 版本: xiaohongshu/login.go
实现: 检查登录状态、获取二维码、等待登录完成
"""
import asyncio
import base64
import logging
from typing import Optional

from playwright.async_api import Page

from browser.browser import get_browser_page, save_current_cookies

logger = logging.getLogger(__name__)

XHS_HOME_URL = "https://www.xiaohongshu.com"
XHS_LOGIN_URL = "https://www.xiaohongshu.com/login"
XHS_CREATOR_URL = "https://creator.xiaohongshu.com"

# CSS 选择器（多选择器兼容不同版本 DOM）
LOGIN_CHECK_SELECTORS = [
    # 旧版
    ".main-container .user .link-wrapper .channel",
    # 新版常见
    ".user-avatar",
    ".nav-user-info",
    "[class*='user-avatar']",
    "[class*='avatar-wrapper']",
    # creator 端
    ".creator-home",
    ".upload-entry",
    ".home-container",
]
QRCODE_SELECTOR = ".login-container .qrcode-img"
LOGIN_CONTAINER_SELECTOR = ".login-container"


async def check_login_status() -> bool:
    """
    检查当前登录状态（多策略）
    1. 先从 cookies 文件判断 web_session 是否存在
    2. 再尝试多个 DOM 选择器
    3. 检查页面是否被重定向到登录页
    对应 Go: CheckLoginStatus()
    """
    import time as _time

    # ── 策略1: 检查本地 cookies 文件中是否有有效的 web_session ──────────────
    # 注意：此处不触发浏览器启动，避免冷启动超时（Playwright 启动 Chromium 需要30-60秒）
    try:
        from cookies.cookies import load_cookies
        cookies = load_cookies()
        now = _time.time()
        for c in cookies:
            if c.get("name") == "web_session":
                exp = c.get("expires", -1)
                if exp == -1 or exp > now:
                    logger.info("登录状态检查（cookie）: web_session 有效 → 已登录（跳过浏览器检查）")
                    return True
                else:
                    logger.warning(f"web_session cookie 已过期 (expires={exp:.0f}, now={now:.0f})")
    except Exception as e:
        logger.debug(f"读取 cookies 失败: {e}")

    # ── 策略2: 浏览器 DOM 检查 ──────────────────────────────────────────────
    try:
        page = await get_browser_page()

        # 导航到小红书首页
        current_url = page.url
        if "xiaohongshu.com" not in current_url:
            await page.goto(XHS_HOME_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
            current_url = page.url

        # 被重定向到登录页 → 未登录
        if "/login" in current_url:
            logger.info("登录状态检查: 被重定向到登录页 → 未登录")
            return False

        # 尝试多个选择器
        for sel in LOGIN_CHECK_SELECTORS:
            try:
                element = await page.wait_for_selector(sel, timeout=2000, state="visible")
                if element:
                    logger.info(f"登录状态检查: 已登录（选择器: {sel}）")
                    return True
            except Exception:
                continue

        # 用 JS 检查页面是否有用户信息相关内容
        logged_via_js = await page.evaluate("""
            () => {
                // 检查 cookie 中是否有 web_session
                const hasCookie = document.cookie.includes('web_session') ||
                                  document.cookie.includes('customer-sso-sid');
                if (hasCookie) return { logged: true, reason: 'cookie in browser' };

                // 检查 localStorage / sessionStorage
                const keys = ['userInfo', 'user_info', 'userId', 'loginInfo'];
                for (const k of keys) {
                    if (localStorage.getItem(k) || sessionStorage.getItem(k)) {
                        return { logged: true, reason: 'storage: ' + k };
                    }
                }

                // 检查 DOM 中是否有用户信息类元素
                const userEls = document.querySelectorAll(
                    '[class*="user"], [class*="avatar"], [class*="profile"]'
                );
                for (const el of userEls) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 20 && rect.height > 20) {
                        return { logged: true, reason: 'user element: ' + el.className };
                    }
                }

                return { logged: false };
            }
        """)
        if logged_via_js and logged_via_js.get("logged"):
            logger.info(f"登录状态检查（JS）: 已登录 → {logged_via_js.get('reason')}")
            return True

        logger.info("登录状态检查: 未登录")
        return False

    except Exception as e:
        logger.error(f"检查登录状态失败: {e}")
        return False


async def get_login_qrcode() -> Optional[str]:
    """
    获取登录二维码图片（Base64 编码）
    对应 Go: FetchQrcodeImage()
    返回 Base64 编码的图片字符串，失败返回 None
    """
    try:
        page = await get_browser_page()

        # 导航到小红书首页触发登录弹窗
        await page.goto(XHS_HOME_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        # 检查是否已经登录
        already_logged = await check_login_status()
        if already_logged:
            logger.info("已登录，无需获取二维码")
            return None

        # 等待登录弹窗出现
        try:
            await page.wait_for_selector(
                LOGIN_CONTAINER_SELECTOR,
                timeout=10000,
                state="visible"
            )
        except Exception:
            # 尝试点击登录按钮
            login_btn = await page.query_selector(".login-btn, [class*='login']")
            if login_btn:
                await login_btn.click()
                await asyncio.sleep(1)

        # 获取二维码元素
        qrcode_element = await page.wait_for_selector(
            QRCODE_SELECTOR,
            timeout=10000,
            state="visible"
        )

        if not qrcode_element:
            logger.error("未找到二维码元素")
            return None

        # 截取二维码图片
        qrcode_bytes = await qrcode_element.screenshot()
        qrcode_base64 = base64.b64encode(qrcode_bytes).decode("utf-8")

        logger.info("成功获取登录二维码")
        return qrcode_base64

    except Exception as e:
        logger.error(f"获取登录二维码失败: {e}")
        return None


async def wait_for_login(timeout_seconds: int = 120) -> bool:
    """
    等待用户扫码登录完成
    对应 Go: WaitForLogin()
    :param timeout_seconds: 等待超时时间（秒）
    :return: 登录成功返回 True，超时返回 False
    """
    logger.info(f"等待登录，超时时间: {timeout_seconds}秒")
    page = await get_browser_page()

    start_time = asyncio.get_event_loop().time()

    while True:
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > timeout_seconds:
            logger.warning("等待登录超时")
            return False

        try:
            # 检查登录成功标志
            element = await page.query_selector(LOGIN_CHECK_SELECTOR)
            if element:
                visible = await element.is_visible()
                if visible:
                    logger.info("登录成功！")
                    # 保存 cookies
                    await save_current_cookies()
                    return True
        except Exception as e:
            logger.debug(f"等待登录检查出错: {e}")

        await asyncio.sleep(2)


async def ensure_logged_in() -> bool:
    """
    确保用户已登录，如果未登录则等待登录
    返回是否已登录
    """
    is_logged = await check_login_status()
    if is_logged:
        return True

    logger.info("用户未登录，请扫描二维码登录")
    return False


async def login_with_qrcode(display_callback=None) -> bool:
    """
    完整的二维码登录流程
    :param display_callback: 可选的回调函数，用于显示二维码（接收 base64 字符串）
    :return: 登录成功返回 True
    """
    # 获取二维码
    qrcode_base64 = await get_login_qrcode()
    if not qrcode_base64:
        # 可能已经登录
        return await check_login_status()

    # 调用回调显示二维码
    if display_callback:
        await display_callback(qrcode_base64)
    else:
        logger.info("请使用小红书 App 扫描二维码登录")
        # 保存二维码到临时文件
        try:
            import tempfile
            import os
            qrcode_bytes = base64.b64decode(qrcode_base64)
            with tempfile.NamedTemporaryFile(
                suffix=".png", delete=False, prefix="xhs_qrcode_"
            ) as f:
                f.write(qrcode_bytes)
                logger.info(f"二维码已保存到: {f.name}")
        except Exception as e:
            logger.error(f"保存二维码失败: {e}")

    # 等待登录
    success = await wait_for_login(timeout_seconds=120)
    return success
