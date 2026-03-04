"""
调试评论页面的 DOM 结构
运行方式: cd D:\XHS-AI-Tool\MCP; python debug_comment_page.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')

from browser.browser import get_browser_page

# 用之前 feeds 找到的 feed_id 来测试
TEST_FEED_ID = "69a44c18000000000e00e803"
XHS_EXPLORE_URL = f"https://www.xiaohongshu.com/explore/{TEST_FEED_ID}"


async def debug_comment():
    page = await get_browser_page()

    print(f"正在打开帖子页面: {XHS_EXPLORE_URL}")
    await page.goto(XHS_EXPLORE_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(4)

    print("\n=== 查找评论相关元素 ===")

    # 查找所有 contenteditable 元素
    editable = await page.evaluate("""
        () => {
            const els = document.querySelectorAll('[contenteditable]');
            return Array.from(els).map(el => ({
                tag: el.tagName,
                className: el.className || '',
                id: el.id || '',
                contenteditable: el.getAttribute('contenteditable'),
                placeholder: el.getAttribute('placeholder') || el.dataset.placeholder || '',
                parentClass: el.parentElement ? el.parentElement.className : '',
                visible: el.offsetWidth > 0 && el.offsetHeight > 0,
                text: (el.innerText || '').substring(0, 50),
            }));
        }
    """)
    print(f"contenteditable 元素 ({len(editable)} 个):")
    for el in editable:
        print(f"  <{el['tag']}> class='{el['className']}' id='{el['id']}' visible={el['visible']} placeholder='{el['placeholder']}' parentClass='{el['parentClass']}'")

    # 查找包含"评论"的文字区域
    comment_area = await page.evaluate("""
        () => {
            const result = [];
            // 查找 input-box 相关
            const inputBoxes = document.querySelectorAll('.input-box, [class*="input-box"], [class*="comment-input"], [class*="comment_input"]');
            for (const el of inputBoxes) {
                result.push({
                    tag: el.tagName,
                    className: el.className,
                    id: el.id || '',
                    html: el.outerHTML.substring(0, 300),
                });
            }
            return result;
        }
    """)
    print(f"\n评论输入框相关元素 ({len(comment_area)} 个):")
    for el in comment_area:
        print(f"  <{el['tag']}> class='{el['className']}'")
        print(f"    html: {el['html'][:200]}")

    # 查找包含"说点什么"或"评论"placeholder 的元素
    placeholders = await page.evaluate("""
        () => {
            const result = [];
            const all = document.querySelectorAll('*');
            for (const el of all) {
                const ph = el.getAttribute('placeholder') || el.dataset.placeholder || '';
                if (ph && (ph.includes('评论') || ph.includes('说点') || ph.includes('输入'))) {
                    result.push({
                        tag: el.tagName,
                        className: el.className || '',
                        placeholder: ph,
                        visible: el.offsetWidth > 0,
                    });
                }
            }
            return result.slice(0, 10);
        }
    """)
    print(f"\n含评论 placeholder 的元素 ({len(placeholders)} 个):")
    for el in placeholders:
        print(f"  <{el['tag']}> class='{el['className']}' placeholder='{el['placeholder']}'")

    # 截图
    await page.screenshot(path="debug_comment_page.png", full_page=False)
    print("\n截图已保存: debug_comment_page.png")

    # 保存 HTML
    html = await page.content()
    with open("debug_comment_html.txt", "w", encoding="utf-8") as f:
        f.write(html[:80000])
    print("HTML 已保存: debug_comment_html.txt (前80KB)")

    # 关闭浏览器
    from browser.browser import _browser_instance
    if _browser_instance and _browser_instance.browser:
        await _browser_instance.browser.close()
    print("\n调试完成")


asyncio.run(debug_comment())
