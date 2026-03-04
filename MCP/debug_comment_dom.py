"""
调试脚本: 直接访问小红书帖子页面，检查评论区 DOM 结构
用法: python debug_comment_dom.py <帖子URL>
例如: python debug_comment_dom.py "https://www.xiaohongshu.com/explore/xxx?xsec_token=yyy"
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.async_api import async_playwright
from cookies.cookies import load_cookies


async def main():
    # 如果有命令行参数用参数，否则先去首页找有效链接
    target_url = sys.argv[1] if len(sys.argv) > 1 else None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )

        # 加载 cookies
        cookies = load_cookies()
        if cookies:
            await context.add_cookies(cookies)
            print(f"加载了 {len(cookies)} 个 cookies")

        page = await context.new_page()

        if not target_url:
            # 去首页找一个有效的帖子链接
            print("去首页找有效帖子链接...")
            await page.goto("https://www.xiaohongshu.com/", wait_until="domcontentloaded")
            await asyncio.sleep(5)

            # 从当前页面 DOM 找带 xsec_token 的链接
            links = await page.evaluate("""
                () => {
                    const result = [];
                    document.querySelectorAll('a').forEach(a => {
                        const href = a.href || '';
                        if (href.includes('/explore/') && href.includes('xsec_token=')) {
                            result.push(href);
                        }
                    });
                    return result.slice(0, 5);
                }
            """)
            print(f"找到的链接: {links}")

            if links:
                target_url = links[0]
            else:
                # 尝试拦截网络请求获取 xsec_token
                print("DOM 中未找到 xsec_token，尝试从个人主页获取...")
                await page.goto("https://www.xiaohongshu.com/user/profile", wait_until="domcontentloaded")
                await asyncio.sleep(4)
                links2 = await page.evaluate("""
                    () => {
                        const result = [];
                        document.querySelectorAll('a').forEach(a => {
                            const href = a.href || '';
                            if (href.includes('/explore/') && href.includes('xsec_token=')) {
                                result.push(href);
                            }
                        });
                        return result.slice(0, 5);
                    }
                """)
                print(f"个人主页找到的链接: {links2}")
                if links2:
                    target_url = links2[0]

        if not target_url:
            print("未找到有效帖子 URL，退出")
            await browser.close()
            return

        print(f"\n访问帖子: {target_url}")
        await page.goto(target_url, wait_until="domcontentloaded")
        await asyncio.sleep(5)

        print(f"当前 URL: {page.url}")

        # 截图
        await page.screenshot(path="debug_comment_detail.png")
        print("已截图: debug_comment_detail.png")

        # 查询所有交互元素
        result = await page.evaluate("""
            () => {
                const info = {
                    url: window.location.href,
                    contentEditables: [],
                    inputs: [],
                    commentDivs: []
                };

                // contenteditable
                document.querySelectorAll('[contenteditable]').forEach(el => {
                    try {
                        const rect = el.getBoundingClientRect();
                        info.contentEditables.push({
                            tag: el.tagName,
                            id: el.id || '',
                            cls: String(el.className || '').substring(0, 100),
                            ce: el.getAttribute('contenteditable'),
                            ph: el.getAttribute('placeholder') || el.dataset.placeholder || '',
                            visible: el.offsetParent !== null,
                            rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                            text: (el.textContent || '').substring(0, 50)
                        });
                    } catch(e) {}
                });

                // input (非hidden)
                document.querySelectorAll('input:not([type="hidden"])').forEach(el => {
                    try {
                        info.inputs.push({
                            id: el.id || '',
                            type: el.type,
                            ph: el.placeholder || '',
                            cls: String(el.className || '').substring(0, 100),
                            visible: el.offsetParent !== null
                        });
                    } catch(e) {}
                });

                // 与评论相关的 div (class/id 含 comment/input/send)
                document.querySelectorAll('div, section, form').forEach(el => {
                    try {
                        const cls = String(el.className || '');
                        const id = String(el.id || '');
                        const lower = (cls + id).toLowerCase();
                        if (lower.includes('comment') || lower.includes('input-box') || lower.includes('send')) {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 || rect.height > 0) {
                                info.commentDivs.push({
                                    tag: el.tagName,
                                    id: id,
                                    cls: cls.substring(0, 100),
                                    rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                                    children: el.children.length
                                });
                            }
                        }
                    } catch(e) {}
                });

                return info;
            }
        """)

        print(f"\n=== CONTENTEDITABLE 元素 ===")
        for ce in result.get('contentEditables', []):
            print(f"  <{ce['tag']} ce='{ce['ce']}' id='{ce['id']}' ph='{ce['ph']}' "
                  f"cls='{ce['cls'][:60]}' visible={ce['visible']} rect={ce['rect']} text='{ce['text'][:30]}'>")

        print(f"\n=== INPUT 元素 (非hidden) ===")
        for inp in result.get('inputs', []):
            print(f"  <input type='{inp['type']}' id='{inp['id']}' ph='{inp['ph']}' "
                  f"cls='{inp['cls'][:60]}' visible={inp['visible']}>")

        print(f"\n=== COMMENT/INPUT 相关 DIV (前20个) ===")
        for div in result.get('commentDivs', [])[:20]:
            print(f"  <{div['tag']} id='{div['id']}' cls='{div['cls'][:80]}' "
                  f"rect={div['rect']} children={div['children']}>")

        # 保存 body HTML 中间部分（评论区通常在后半段）
        html = await page.evaluate("""
            () => {
                const body = document.body.innerHTML;
                // 找评论区关键词
                const idx = body.toLowerCase().indexOf('comment');
                if (idx > 0) {
                    return body.substring(Math.max(0, idx - 200), idx + 3000);
                }
                return body.substring(body.length / 2, body.length / 2 + 3000);
            }
        """)
        with open("debug_comment_html.txt", "w", encoding="utf-8") as f:
            f.write(html)
        print("\n已保存评论区 HTML 到: debug_comment_html.txt")

        # 等用户手动查看
        print("\n保持浏览器开启 15 秒供查看...")
        await asyncio.sleep(15)

        await browser.close()
        print("完成!")


if __name__ == "__main__":
    asyncio.run(main())
