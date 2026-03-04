"""
调试: 点击"点击评论"后评论输入框的 DOM 变化
直接使用用户提供的帖子 URL
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.async_api import async_playwright
from cookies.cookies import load_cookies

POST_URL = "https://www.xiaohongshu.com/explore/69a69aaf000000001a01ff11?source=webshare&xhsshare=pc_web&xsec_token=AB2sj26U0tr8aeKFmkLvTTEULvfsnOJZ_w6M0zIlRlA8M=&xsec_source=pc_share"


async def get_dom_info(page, label=""):
    """获取当前页面的评论相关 DOM 信息"""
    result = await page.evaluate("""
        () => {
            const info = {
                contentEditables: [],
                inputBoxes: [],
                commentBtns: []
            };

            // 所有 contenteditable
            document.querySelectorAll('[contenteditable]').forEach(el => {
                try {
                    const rect = el.getBoundingClientRect();
                    info.contentEditables.push({
                        tag: el.tagName,
                        id: el.id || '',
                        cls: String(el.className || '').substring(0, 80),
                        ce: el.getAttribute('contenteditable'),
                        ph: el.getAttribute('placeholder') || el.dataset.placeholder || '',
                        visible: el.offsetParent !== null,
                        rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                        text: (el.textContent || '').substring(0, 50)
                    });
                } catch(e) {}
            });

            // input-box 相关
            document.querySelectorAll('[class*="input-box"], [class*="comment-input"], [class*="comment-box"]').forEach(el => {
                try {
                    const rect = el.getBoundingClientRect();
                    info.inputBoxes.push({
                        tag: el.tagName,
                        cls: String(el.className || '').substring(0, 80),
                        rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                        children: el.children.length,
                        html: el.outerHTML.substring(0, 300)
                    });
                } catch(e) {}
            });

            // 评论/发送按钮
            document.querySelectorAll('button, [class*="send"], [class*="comment-btn"], .comment-action').forEach(el => {
                try {
                    const text = (el.textContent || el.innerText || '').trim().substring(0, 30);
                    const cls = String(el.className || '').substring(0, 60);
                    if (text || cls.includes('send') || cls.includes('comment')) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 || rect.height > 0) {
                            info.commentBtns.push({
                                tag: el.tagName,
                                cls: cls,
                                text: text,
                                rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) }
                            });
                        }
                    }
                } catch(e) {}
            });

            return info;
        }
    """)
    print(f"\n{'='*50}")
    print(f"[{label}] DOM 状态")
    print(f"{'='*50}")
    print(f"ContentEditable 元素:")
    for ce in result['contentEditables']:
        print(f"  <{ce['tag']} id='{ce['id']}' cls='{ce['cls'][:60]}' visible={ce['visible']} rect={ce['rect']} text='{ce['text'][:30]}'>")
    print(f"\nInput Box 元素:")
    for box in result['inputBoxes'][:5]:
        print(f"  <{box['tag']} cls='{box['cls'][:60]}' rect={box['rect']} children={box['children']}>")
        print(f"    HTML: {box['html'][:200]}")
    print(f"\n评论按钮:")
    for btn in result['commentBtns'][:10]:
        print(f"  <{btn['tag']} cls='{btn['cls'][:50]}' text='{btn['text']}' rect={btn['rect']}>")
    return result


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )

        cookies = load_cookies()
        if cookies:
            await context.add_cookies(cookies)
            print(f"加载了 {len(cookies)} 个 cookies")

        page = await context.new_page()

        print(f"访问帖子: {POST_URL}")
        await page.goto(POST_URL, wait_until="domcontentloaded")
        await asyncio.sleep(4)

        print(f"当前 URL: {page.url}")
        await page.screenshot(path="debug_click_before.png")

        # 查看初始 DOM
        before = await get_dom_info(page, "初始状态")

        # 策略1: 点击"点击评论"链接
        print("\n尝试点击'点击评论'链接...")
        click_result = await page.evaluate("""
            () => {
                // 找包含"点击评论"文字的元素
                const allEls = document.querySelectorAll('*');
                for (const el of allEls) {
                    const text = (el.textContent || '').trim();
                    if (text === '点击评论' || text.includes('点击评论')) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0) {
                            el.click();
                            return { clicked: true, tag: el.tagName, cls: String(el.className || ''), text: text, rect: {x: Math.round(rect.x), y: Math.round(rect.y)} };
                        }
                    }
                }
                return { clicked: false };
            }
        """)
        print(f"点击'点击评论': {click_result}")
        await asyncio.sleep(2)

        await page.screenshot(path="debug_click_after_comment_link.png")
        after1 = await get_dom_info(page, "点击'点击评论'后")

        # 策略2: 点击底部"评论"按钮（图标按钮）
        print("\n尝试点击底部评论图标按钮...")
        click_result2 = await page.evaluate("""
            () => {
                // 底部操作栏的"评论"按钮（通常是 .comment 或 .icon-comment）
                const selectors = [
                    '.comment-btn', '.icon-comment', '[class*="comment-icon"]',
                    '.interact-container .comment', '.action-bar .comment',
                    'span.comment', '.footer-action [class*="comment"]'
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        el.click();
                        return { clicked: true, sel: sel };
                    }
                }
                // 找文字为"评论"的按钮区域（底部操作栏）
                const spans = document.querySelectorAll('span');
                for (const span of spans) {
                    if ((span.textContent || '').trim() === '评论') {
                        const rect = span.getBoundingClientRect();
                        if (rect.y > 700) {  // 底部区域
                            span.click();
                            const parent = span.parentElement;
                            if (parent) parent.click();
                            return { clicked: true, text: '评论', rect: {x: Math.round(rect.x), y: Math.round(rect.y)} };
                        }
                    }
                }
                return { clicked: false };
            }
        """)
        print(f"点击底部评论按钮: {click_result2}")
        await asyncio.sleep(2)

        await page.screenshot(path="debug_click_after_icon.png")
        after2 = await get_dom_info(page, "点击底部评论图标后")

        # 策略3: 直接点击评论输入框区域（右侧底部 input-box）
        print("\n尝试直接点击评论输入框区域...")
        click_result3 = await page.evaluate("""
            () => {
                const inputBox = document.querySelector('.input-box');
                if (inputBox) {
                    const rect = inputBox.getBoundingClientRect();
                    inputBox.click();
                    return { clicked: true, rect: {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)} };
                }
                return { clicked: false };
            }
        """)
        print(f"点击 input-box: {click_result3}")
        await asyncio.sleep(2)

        await page.screenshot(path="debug_click_after_inputbox.png")
        after3 = await get_dom_info(page, "点击 input-box 后")

        # 如果找到了评论输入框，尝试输入
        ce_elements = after3['contentEditables']
        if ce_elements:
            print(f"\n找到 {len(ce_elements)} 个 contenteditable 元素，尝试输入...")
            best_el = ce_elements[0]
            print(f"使用: id='{best_el['id']}' cls='{best_el['cls']}' rect={best_el['rect']}")

            # 确定选择器
            if best_el['id']:
                sel = f"#{best_el['id']}"
            else:
                sel = f"{best_el['tag'].lower()}.{best_el['cls'].split()[0]}" if best_el['cls'] else best_el['tag'].lower()

            try:
                el = await page.wait_for_selector(sel, timeout=3000, state="attached")
                if el:
                    await el.click()
                    await asyncio.sleep(0.3)
                    await page.keyboard.type("真好玩", delay=100)
                    await asyncio.sleep(1)
                    await page.screenshot(path="debug_typed_comment.png")
                    print("已输入'真好玩'，截图: debug_typed_comment.png")

                    # 查看发送按钮
                    send_info = await page.evaluate("""
                        () => {
                            const inputBox = document.querySelector('.input-box');
                            if (!inputBox) return null;
                            return { html: inputBox.outerHTML.substring(0, 1000) };
                        }
                    """)
                    print(f"\ninput-box 完整 HTML:\n{send_info}")
            except Exception as e:
                print(f"输入失败: {e}")

        print("\n等待 20 秒供查看...")
        await asyncio.sleep(20)
        await browser.close()
        print("完成!")


if __name__ == "__main__":
    asyncio.run(main())
