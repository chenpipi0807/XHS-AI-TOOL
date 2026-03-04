"""
专门调试发布页面的 tab 结构和点击"上传图文"后的变化
运行方式: cd D:\XHS-AI-Tool\MCP; python debug_publish_tabs.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')

from browser.browser import get_browser_page
from playwright.async_api import Page

XHS_PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"


async def debug_tabs():
    page = await get_browser_page()
    
    print("正在打开发布页面...")
    await page.goto(XHS_PUBLISH_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)
    
    print("\n=== 初始页面分析 ===")
    
    # 查找所有包含"上传"文字的元素
    tab_info = await page.evaluate("""
        () => {
            const allElements = document.querySelectorAll('*');
            const tabElements = [];
            for (const el of allElements) {
                const text = el.innerText || el.textContent || '';
                if (text.includes('上传图文') || text.includes('上传视频') || text.includes('写长文')) {
                    if (el.children.length <= 2) {  // 只取叶节点或接近叶节点
                        tabElements.push({
                            tag: el.tagName,
                            className: el.className || '',
                            text: text.trim().substring(0, 30),
                            id: el.id || '',
                            role: el.getAttribute('role') || '',
                            clickable: el.onclick !== null || el.tagName === 'BUTTON' || el.tagName === 'A',
                        });
                    }
                }
            }
            return tabElements.slice(0, 20);
        }
    """)
    print("包含'上传图文'等文字的元素:")
    for el in tab_info:
        print(f"  <{el['tag']}> class='{el['className']}' text='{el['text']}' id='{el['id']}'")
    
    # 查找所有 div.tab-item 元素
    tab_items = await page.evaluate("""
        () => {
            const items = document.querySelectorAll('.tab-item, [class*="tab-item"], [class*="tab_item"]');
            return Array.from(items).map(el => ({
                tag: el.tagName,
                className: el.className,
                text: (el.innerText || '').trim(),
                html: el.outerHTML.substring(0, 200),
            }));
        }
    """)
    print(f"\n.tab-item 元素 ({len(tab_items)} 个):")
    for item in tab_items:
        print(f"  <{item['tag']}> class='{item['className']}' text='{item['text']}'")
    
    # 查找 publish-tab 相关
    publish_tabs = await page.evaluate("""
        () => {
            const items = document.querySelectorAll(
                '.publish-tab, [class*="publish-tab"], .tab, [class*="tabs"], [role="tab"]'
            );
            return Array.from(items).slice(0, 10).map(el => ({
                tag: el.tagName,
                className: el.className,
                text: (el.innerText || '').trim().substring(0, 50),
                html: el.outerHTML.substring(0, 300),
            }));
        }
    """)
    print(f"\n.publish-tab / [role=tab] 元素 ({len(publish_tabs)} 个):")
    for item in publish_tabs:
        print(f"  <{item['tag']}> class='{item['className']}' text='{item['text']}'")
        print(f"    html: {item['html'][:150]}")
    
    # 截图
    await page.screenshot(path="debug_tabs_before_click.png", full_page=False)
    print("\n截图已保存: debug_tabs_before_click.png")
    
    # 尝试不同方式点击"上传图文"
    print("\n=== 尝试点击'上传图文' ===")
    
    clicked = False
    
    # 方法1: Playwright text selector
    try:
        await page.click("text=上传图文", timeout=5000)
        print("✅ 方法1成功: page.click('text=上传图文')")
        clicked = True
    except Exception as e:
        print(f"❌ 方法1失败: {e}")
    
    if not clicked:
        # 方法2: evaluate 点击
        try:
            result = await page.evaluate("""
                () => {
                    const allElements = document.querySelectorAll('*');
                    for (const el of allElements) {
                        const text = (el.innerText || el.textContent || '').trim();
                        if (text === '上传图文') {
                            el.click();
                            return { clicked: true, tag: el.tagName, class: el.className };
                        }
                    }
                    return { clicked: false };
                }
            """)
            if result.get('clicked'):
                print(f"✅ 方法2成功: JS click on <{result['tag']}> class='{result['class']}'")
                clicked = True
            else:
                print("❌ 方法2失败: 找不到'上传图文'元素")
        except Exception as e:
            print(f"❌ 方法2失败: {e}")
    
    if clicked:
        await asyncio.sleep(2)
        print("\n=== 点击后的 file input 状态 ===")
        
        file_inputs = await page.evaluate("""
            () => {
                const inputs = document.querySelectorAll('input[type="file"], input.upload-input');
                return Array.from(inputs).map(el => ({
                    accept: el.accept,
                    className: el.className,
                    id: el.id,
                    name: el.name,
                    style: el.style.cssText,
                    parentClass: el.parentElement ? el.parentElement.className : '',
                }));
            }
        """)
        print(f"file input 列表 ({len(file_inputs)} 个):")
        for inp in file_inputs:
            print(f"  accept='{inp['accept']}' class='{inp['className']}' parent='{inp['parentClass']}'")
        
        # 截图
        await page.screenshot(path="debug_tabs_after_click.png", full_page=False)
        print("\n点击后截图已保存: debug_tabs_after_click.png")
    
    # 保存完整 HTML
    html = await page.content()
    # 只保存包含 tab 相关的部分
    with open("debug_tabs_html.txt", "w", encoding="utf-8") as f:
        f.write(html[:50000])  # 前50KB
    print("HTML 已保存: debug_tabs_html.txt")
    
    input("\n按 Enter 关闭浏览器...")
    

asyncio.run(debug_tabs())
