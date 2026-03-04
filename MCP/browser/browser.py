"""
Playwright 浏览器管理模块
对应 Go 版本: browser/browser.go
使用 playwright-python 替代 go-rod 进行浏览器自动化
"""
import asyncio
import logging
import os
from typing import Optional, List, Dict, Any

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)

from cookies.cookies import load_cookies, save_cookies, convert_playwright_cookies

logger = logging.getLogger(__name__)

# 全局浏览器实例
_playwright: Optional[Playwright] = None
_browser: Optional[Browser] = None
_context: Optional[BrowserContext] = None
_page: Optional[Page] = None
_lock = asyncio.Lock()


async def get_browser_page() -> Page:
    """
    获取浏览器页面实例（单例模式）
    如果浏览器未启动则自动初始化
    """
    global _playwright, _browser, _context, _page

    async with _lock:
        if _page is not None:
            try:
                # 检查页面是否仍然有效
                await _page.title()
                return _page
            except Exception:
                logger.warning("页面已失效，重新初始化浏览器")
                _page = None
                _context = None
                _browser = None
                _playwright = None

        _page = await _init_browser()
        return _page


async def _init_browser() -> Page:
    """
    初始化 Playwright 浏览器
    支持 headless 模式和代理配置
    """
    global _playwright, _browser, _context

    logger.info("初始化 Playwright 浏览器...")

    _playwright = await async_playwright().start()

    # 读取环境变量配置
    headless_env = os.environ.get("HEADLESS", "false").lower()
    headless = headless_env in ("true", "1", "yes")

    proxy_url = os.environ.get("XHS_PROXY", "")

    # 构建启动参数
    launch_args = {
        "headless": headless,
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-accelerated-2d-canvas",
            "--no-first-run",
            "--no-zygote",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
        ],
    }

    # 代理配置
    if proxy_url:
        logger.info(f"使用代理: {proxy_url}")
        launch_args["proxy"] = {"server": proxy_url}

    _browser = await _playwright.chromium.launch(**launch_args)

    # 浏览器上下文配置
    context_args: Dict[str, Any] = {
        "viewport": {"width": 1280, "height": 800},
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "extra_http_headers": {
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        # 拒绝地理位置权限，避免弹出授权弹窗影响自动化操作
        "permissions": [],
        "geolocation": None,
    }

    _context = await _browser.new_context(**context_args)

    # 明确拒绝地理位置权限
    await _context.grant_permissions([])

    # 注入反检测脚本
    await _context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en']
        });
        window.chrome = {
            runtime: {}
        };
    """)

    # 加载已保存的 cookies
    saved_cookies = load_cookies()
    if saved_cookies:
        try:
            await _context.add_cookies(saved_cookies)
            logger.info(f"已加载 {len(saved_cookies)} 个保存的 cookies")
        except Exception as e:
            logger.warning(f"加载 cookies 失败: {e}")

    page = await _context.new_page()

    # 设置页面超时
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)

    logger.info("浏览器初始化完成")
    return page


async def save_current_cookies() -> bool:
    """保存当前浏览器 cookies 到文件"""
    global _context
    if _context is None:
        logger.warning("浏览器上下文未初始化，无法保存 cookies")
        return False

    try:
        cookies = await _context.cookies()
        converted = convert_playwright_cookies(cookies)
        return save_cookies(converted)
    except Exception as e:
        logger.error(f"保存 cookies 失败: {e}")
        return False


async def close_browser() -> None:
    """关闭浏览器并清理资源"""
    global _playwright, _browser, _context, _page

    async with _lock:
        if _context:
            try:
                await save_current_cookies()
                await _context.close()
            except Exception as e:
                logger.error(f"关闭浏览器上下文失败: {e}")
            _context = None

        if _browser:
            try:
                await _browser.close()
            except Exception as e:
                logger.error(f"关闭浏览器失败: {e}")
            _browser = None

        if _playwright:
            try:
                await _playwright.stop()
            except Exception as e:
                logger.error(f"停止 Playwright 失败: {e}")
            _playwright = None

        _page = None
        logger.info("浏览器已关闭")


async def navigate_to(url: str, wait_until: str = "domcontentloaded") -> Page:
    """
    导航到指定 URL
    返回页面实例
    """
    page = await get_browser_page()
    try:
        await page.goto(url, wait_until=wait_until, timeout=30000)
        logger.info(f"导航到: {url}")
        return page
    except Exception as e:
        logger.error(f"导航失败 {url}: {e}")
        raise


async def wait_for_page_load(page: Page, timeout: int = 5000) -> None:
    """等待页面加载完成"""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        # 超时时不抛出异常，只是等待固定时间
        await asyncio.sleep(1)


async def scroll_page(page: Page, distance: int = 300, delay: float = 0.5) -> None:
    """
    滚动页面
    :param page: 页面实例
    :param distance: 滚动距离（像素）
    :param delay: 滚动后等待时间（秒）
    """
    await page.evaluate(f"window.scrollBy(0, {distance})")
    await asyncio.sleep(delay)


async def human_like_scroll(
    page: Page,
    total_scrolls: int = 5,
    speed: str = "normal"
) -> None:
    """
    模拟人类滚动行为
    :param page: 页面实例
    :param total_scrolls: 滚动次数
    :param speed: 滚动速度 slow/normal/fast
    """
    import random

    speed_config = {
        "slow": {"min_delay": 1.5, "max_delay": 3.0, "min_dist": 100, "max_dist": 200},
        "normal": {"min_delay": 0.5, "max_delay": 1.5, "min_dist": 200, "max_dist": 400},
        "fast": {"min_delay": 0.2, "max_delay": 0.5, "min_dist": 300, "max_dist": 600},
    }

    config = speed_config.get(speed, speed_config["normal"])

    for _ in range(total_scrolls):
        distance = random.randint(config["min_dist"], config["max_dist"])
        delay = random.uniform(config["min_delay"], config["max_delay"])
        await page.evaluate(f"window.scrollBy(0, {distance})")
        await asyncio.sleep(delay)
