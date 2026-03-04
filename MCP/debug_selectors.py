"""
调试脚本：截图并获取发布页面和首页的实际 DOM 结构
用于修正 CSS 选择器
运行方式: python debug_selectors.py
"""
import asyncio
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.async_api import async_playwright
from cookies.cookies import load_cookies


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,   # 有头模式，可以看到浏览器
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        # 加载 cookies
        cookies = load_cookies()
        if cookies:
            await context.add_cookies(cookies)
            print(f"已加载 {len(cookies)} 个 cookies")

        page = await context.new_page()

        # ============================================================
        # 调试1: 小红书首页 - 分析 window.__INITIAL_STATE__
        # ============================================================
        print("\n=== 调试首页 Feed 数据 ===")
        await page.goto("https://www.xiaohongshu.com", wait_until="networkidle")
        await asyncio.sleep(3)

        # 截图
        await page.screenshot(path="debug_home.png", full_page=False)
        print("首页截图已保存: debug_home.png")

        # 获取 __INITIAL_STATE__ 的顶层 key
        state_keys = await page.evaluate("""
            () => {
                const state = window.__INITIAL_STATE__;
                if (!state) return 'NO_STATE';
                return Object.keys(state);
            }
        """)
        print(f"__INITIAL_STATE__ 顶层 keys: {state_keys}")

        # 尝试 feed 相关路径
        feed_paths = [
            "window.__INITIAL_STATE__?.feed?.feeds",
            "window.__INITIAL_STATE__?.feed?.homefeed",
            "window.__INITIAL_STATE__?.home?.feeds",
            "window.__INITIAL_STATE__?.note?.feeds",
        ]
        for path in feed_paths:
            result = await page.evaluate(f"() => {{ try {{ const d = {path}; return d ? JSON.stringify(d).slice(0,200) : 'EMPTY'; }} catch(e) {{ return 'ERROR:'+e; }} }}")
            print(f"  {path}: {result}")

        # ============================================================
        # 调试2: 创作者发布页 - 分析实际 DOM 结构
        # ============================================================
        print("\n=== 调试发布页 DOM 结构 ===")
        await page.goto(
            "https://creator.xiaohongshu.com/publish/publish?source=official",
            wait_until="networkidle"
        )
        await asyncio.sleep(4)

        # 截图
        await page.screenshot(path="debug_publish.png", full_page=False)
        print("发布页截图已保存: debug_publish.png")

        # 找所有 input[type=file]
        file_inputs = await page.evaluate("""
            () => {
                const inputs = document.querySelectorAll("input[type='file']");
                return Array.from(inputs).map(el => ({
                    accept: el.accept,
                    className: el.className,
                    id: el.id,
                    name: el.name,
                    parentClass: el.parentElement?.className,
                    grandParentClass: el.parentElement?.parentElement?.className,
                }));
            }
        """)
        print(f"file input 元素列表: {json.dumps(file_inputs, ensure_ascii=False, indent=2)}")

        # 找所有按钮
        buttons = await page.evaluate("""
            () => {
                const btns = document.querySelectorAll("button, [role='button'], .btn, [class*='btn']");
                return Array.from(btns).slice(0, 20).map(el => ({
                    text: el.textContent.trim().slice(0, 30),
                    className: el.className.slice(0, 60),
                    tagName: el.tagName,
                }));
            }
        """)
        print(f"\n按钮列表 (前20个):")
        for b in buttons:
            print(f"  [{b['tagName']}] class='{b['className']}' text='{b['text']}'")

        # 找标题输入框
        title_inputs = await page.evaluate("""
            () => {
                const inputs = document.querySelectorAll("input, textarea, [contenteditable]");
                return Array.from(inputs).slice(0, 15).map(el => ({
                    tagName: el.tagName,
                    type: el.type || el.getAttribute('contenteditable'),
                    placeholder: el.placeholder || el.getAttribute('placeholder') || '',
                    className: el.className.slice(0, 60),
                    id: el.id,
                }));
            }
        """)
        print(f"\n输入框列表 (前15个):")
        for inp in title_inputs:
            print(f"  [{inp['tagName']}] placeholder='{inp['placeholder']}' class='{inp['className']}'")

        # 截图发布页的完整 HTML（前3000字符）
        html_snippet = await page.evaluate("""
            () => document.body.innerHTML.slice(0, 3000)
        """)
        with open("debug_publish_html.txt", "w", encoding="utf-8") as f:
            f.write(html_snippet)
        print("\n发布页 HTML 片段已保存到: debug_publish_html.txt")

        print("\n请查看截图文件: debug_home.png 和 debug_publish.png")
        print("按 Enter 关闭浏览器...")
        input()

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
