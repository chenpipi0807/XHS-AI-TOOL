"""
调试脚本: 检查发布页面上传图片后的标题/正文输入区 DOM 结构
以及小红书帖子详情页的评论区 DOM
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from browser.browser import get_browser_page, close_browser
from cookies.cookies import load_cookies


async def debug_publish_content():
    """调试发布页面 - 图片上传后的内容输入区 DOM"""
    print("=" * 60)
    print("调试: 发布页面内容输入区 DOM")
    print("=" * 60)

    page = await get_browser_page()

    # 加载 cookies (同步函数，不需要 await)
    cookies = load_cookies()
    if cookies:
        await page.context.add_cookies(cookies)

    # 导航到发布页
    await page.goto(
        "https://creator.xiaohongshu.com/publish/publish?source=official",
        wait_until="domcontentloaded",
        timeout=30000,
    )
    await asyncio.sleep(4)

    # 先点击"上传图文"
    clicked = await page.evaluate("""
        () => {
            const tabs = document.querySelectorAll('div.creator-tab');
            for (const tab of tabs) {
                const text = (tab.innerText || tab.textContent || '').trim();
                if (text === '上传图文' || text.includes('上传图文')) {
                    tab.click();
                    return { clicked: true, text: text };
                }
            }
            return { clicked: false };
        }
    """)
    print(f"点击上传图文标签: {clicked}")
    await asyncio.sleep(2)

    # 上传测试图片
    test_img = os.path.join(os.path.dirname(__file__), "test_image.png")
    if os.path.exists(test_img):
        await page.wait_for_selector("input.upload-input", state="attached", timeout=10000)
        file_locator = page.locator("input.upload-input")
        await file_locator.set_input_files(test_img)
        print("图片已上传，等待上传完成...")
        await asyncio.sleep(5)
    else:
        print("警告: test_image.png 不存在，跳过图片上传")
        await asyncio.sleep(2)

    # 截图
    await page.screenshot(path="debug_publish_content.png")
    print("已截图: debug_publish_content.png")

    # 查询所有 input 和 textarea 和 contenteditable
    dom_info = await page.evaluate("""
        () => {
            const result = {
                inputs: [],
                textareas: [],
                contentEditables: [],
                allVisible: []
            };

            // 所有 input
            document.querySelectorAll('input').forEach(el => {
                result.inputs.push({
                    id: el.id,
                    name: el.name,
                    type: el.type,
                    placeholder: el.placeholder,
                    className: el.className,
                    visible: el.offsetParent !== null,
                    value: el.value.substring(0, 50)
                });
            });

            // 所有 textarea
            document.querySelectorAll('textarea').forEach(el => {
                result.textareas.push({
                    id: el.id,
                    name: el.name,
                    placeholder: el.placeholder,
                    className: el.className,
                    visible: el.offsetParent !== null,
                    value: el.value.substring(0, 50)
                });
            });

            // 所有 contenteditable
            document.querySelectorAll('[contenteditable]').forEach(el => {
                const rect = el.getBoundingClientRect();
                result.contentEditables.push({
                    tag: el.tagName,
                    id: el.id,
                    className: el.className,
                    contenteditable: el.getAttribute('contenteditable'),
                    placeholder: el.getAttribute('placeholder') || el.dataset.placeholder || '',
                    visible: el.offsetParent !== null,
                    inViewport: rect.width > 0 && rect.height > 0,
                    rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                    textContent: (el.textContent || '').substring(0, 50)
                });
            });

            return result;
        }
    """)

    print("\n=== INPUT 元素 ===")
    for inp in dom_info.get('inputs', []):
        if inp.get('type') != 'hidden' or inp.get('id') or inp.get('placeholder'):
            print(f"  <input type='{inp.get('type')}' id='{inp.get('id')}' placeholder='{inp.get('placeholder')}' class='{inp.get('className', '')[:60]}' visible={inp.get('visible')}>")

    print("\n=== TEXTAREA 元素 ===")
    for ta in dom_info.get('textareas', []):
        print(f"  <textarea id='{ta.get('id')}' placeholder='{ta.get('placeholder')}' class='{ta.get('className', '')[:60]}' visible={ta.get('visible')}>")

    print("\n=== CONTENTEDITABLE 元素 ===")
    for ce in dom_info.get('contentEditables', []):
        print(f"  <{ce.get('tag')} contenteditable='{ce.get('contenteditable')}' id='{ce.get('id')}' "
              f"placeholder='{ce.get('placeholder')}' class='{ce.get('className', '')[:60]}' "
              f"visible={ce.get('visible')} inViewport={ce.get('inViewport')} "
              f"rect={ce.get('rect')} text='{ce.get('textContent', '')[:30]}'>")

    # 保存 HTML 片段
    html = await page.evaluate("""
        () => {
            // 找到内容编辑区域的父容器
            const selectors = [
                '.post-detail', '.editor-container', '.content-container',
                '.publish-content', '#publish-detail', '.detail-wrapper',
                'main', '.main-content'
            ];
            for (const s of selectors) {
                const el = document.querySelector(s);
                if (el) return { selector: s, html: el.innerHTML.substring(0, 3000) };
            }
            // 备用：body 的前3000字符
            return { selector: 'body', html: document.body.innerHTML.substring(0, 3000) };
        }
    """)
    with open("debug_publish_content.html", "w", encoding="utf-8") as f:
        f.write(f"<!-- selector: {html.get('selector')} -->\n")
        f.write(html.get('html', ''))
    print(f"\n已保存 HTML 片段到: debug_publish_content.html (容器: {html.get('selector')})")

    await close_browser()
    print("\n调试完成!")


async def debug_comment_page():
    """调试帖子详情页评论区 DOM"""
    print("=" * 60)
    print("调试: 帖子详情页评论区 DOM")
    print("=" * 60)

    page = await get_browser_page()

    # 加载 cookies (同步函数，不需要 await)
    cookies = load_cookies()
    if cookies:
        await page.context.add_cookies(cookies)

    # 先去首页获取一个有效的帖子 URL（带 xsec_token）
    await page.goto("https://www.xiaohongshu.com/", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(4)

    # 从 DOM 提取第一个帖子链接（包含 xsec_token）
    feed_info = await page.evaluate("""
        () => {
            const links = document.querySelectorAll('a[href*="/explore/"]');
            for (const a of links) {
                const href = a.href;
                if (href.includes('xsec_token=')) {
                    const match = href.match(/\\/explore\\/([a-zA-Z0-9]+)/);
                    const tokenMatch = href.match(/xsec_token=([^&]+)/);
                    if (match && tokenMatch) {
                        return {
                            href: href,
                            feedId: match[1],
                            xsecToken: decodeURIComponent(tokenMatch[1])
                        };
                    }
                }
                // 尝试没有 token 的
                if (href.includes('/explore/')) {
                    const match = href.match(/\\/explore\\/([a-zA-Z0-9]+)/);
                    if (match) {
                        return { href: href, feedId: match[1], xsecToken: '' };
                    }
                }
            }
            return null;
        }
    """)
    print(f"找到帖子链接: {feed_info}")

    if not feed_info:
        print("未找到帖子链接，退出")
        await close_browser()
        return

    # 导航到帖子详情页（用 URL 直接访问）
    feed_url = feed_info.get('href', '')
    print(f"导航到: {feed_url}")

    await page.goto(feed_url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(4)

    # 截图
    await page.screenshot(path="debug_comment_page2.png")
    print("已截图: debug_comment_page2.png")
    print(f"当前 URL: {page.url}")

    # 查询评论区 DOM
    comment_info = await page.evaluate("""
        () => {
            const result = {
                url: window.location.href,
                contentEditables: [],
                inputs: [],
                commentRelated: []
            };

            // contenteditable 元素
            document.querySelectorAll('[contenteditable]').forEach(el => {
                const rect = el.getBoundingClientRect();
                result.contentEditables.push({
                    tag: el.tagName,
                    id: el.id,
                    className: el.className,
                    contenteditable: el.getAttribute('contenteditable'),
                    placeholder: el.getAttribute('placeholder') || el.dataset.placeholder || '',
                    visible: el.offsetParent !== null,
                    rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                    textContent: (el.textContent || '').substring(0, 50)
                });
            });

            // input 元素（非 hidden）
            document.querySelectorAll('input:not([type="hidden"])').forEach(el => {
                result.inputs.push({
                    id: el.id,
                    type: el.type,
                    placeholder: el.placeholder,
                    className: el.className,
                    visible: el.offsetParent !== null
                });
            });

            // 含 comment 关键字的元素
            const allEls = document.querySelectorAll('*');
            allEls.forEach(el => {
                const cls = (el.className || '').toLowerCase();
                const id = (el.id || '').toLowerCase();
                if ((cls.includes('comment') || id.includes('comment')) && el.tagName !== 'SCRIPT') {
                    const rect = el.getBoundingClientRect();
                    result.commentRelated.push({
                        tag: el.tagName,
                        id: el.id,
                        className: (el.className || '').substring(0, 80),
                        rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                        textContent: (el.textContent || '').substring(0, 50)
                    });
                }
            });

            return result;
        }
    """)

    print(f"\n当前 URL: {comment_info.get('url')}")

    print("\n=== CONTENTEDITABLE 元素 ===")
    for ce in comment_info.get('contentEditables', []):
        print(f"  <{ce.get('tag')} id='{ce.get('id')}' placeholder='{ce.get('placeholder')}' "
              f"class='{ce.get('className', '')[:80]}' visible={ce.get('visible')} "
              f"rect={ce.get('rect')} text='{ce.get('textContent', '')[:30]}'>")

    print("\n=== INPUT 元素 (非hidden) ===")
    for inp in comment_info.get('inputs', []):
        print(f"  <input type='{inp.get('type')}' id='{inp.get('id')}' "
              f"placeholder='{inp.get('placeholder')}' class='{inp.get('className', '')[:60]}' "
              f"visible={inp.get('visible')}>")

    print(f"\n=== COMMENT 相关元素 (前10个) ===")
    comment_related = comment_info.get('commentRelated', [])
    for el in comment_related[:10]:
        print(f"  <{el.get('tag')} id='{el.get('id')}' class='{el.get('className')}' "
              f"rect={el.get('rect')} text='{el.get('textContent', '')[:30]}'>")

    # 尝试点击评论区（触发评论输入框显示）
    print("\n尝试点击评论区...")
    await page.evaluate("""
        () => {
            // 尝试点击评论区域
            const commentAreas = document.querySelectorAll('.comment-input, .comment-box, [class*="comment-input"], [class*="input-box"]');
            for (const area of commentAreas) {
                area.click();
            }
        }
    """)
    await asyncio.sleep(2)
    await page.screenshot(path="debug_comment_after_click.png")
    print("已截图（点击后）: debug_comment_after_click.png")

    # 再次查询
    after_click = await page.evaluate("""
        () => {
            const result = [];
            document.querySelectorAll('[contenteditable]').forEach(el => {
                const rect = el.getBoundingClientRect();
                result.push({
                    tag: el.tagName,
                    id: el.id,
                    className: el.className,
                    placeholder: el.getAttribute('placeholder') || el.dataset.placeholder || '',
                    visible: el.offsetParent !== null,
                    rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) }
                });
            });
            return result;
        }
    """)
    print("\n=== 点击后的 CONTENTEDITABLE 元素 ===")
    for ce in after_click:
        print(f"  <{ce.get('tag')} id='{ce.get('id')}' placeholder='{ce.get('placeholder')}' "
              f"class='{ce.get('className', '')[:80]}' visible={ce.get('visible')} "
              f"rect={ce.get('rect')}>")

    # 保存 HTML
    html = await page.evaluate("""
        () => {
            const selectors = ['.comment-input', '.note-comment', '.comments', '#comments', '.interaction-container', '.comment-container'];
            for (const s of selectors) {
                const el = document.querySelector(s);
                if (el) return { selector: s, html: el.outerHTML.substring(0, 5000) };
            }
            return { selector: 'body_partial', html: document.body.innerHTML.substring(5000, 10000) };
        }
    """)
    with open("debug_comment_dom.html", "w", encoding="utf-8") as f:
        f.write(f"<!-- selector: {html.get('selector')} -->\n")
        f.write(html.get('html', ''))
    print(f"\n已保存评论区 HTML 到: debug_comment_dom.html (容器: {html.get('selector')})")

    await close_browser()
    print("\n调试完成!")


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "comment"
    if mode == "publish":
        await debug_publish_content()
    elif mode == "comment":
        await debug_comment_page()
    else:
        print(f"未知模式: {mode}")
        print("用法: python debug_publish_content.py [publish|comment]")


if __name__ == "__main__":
    asyncio.run(main())
