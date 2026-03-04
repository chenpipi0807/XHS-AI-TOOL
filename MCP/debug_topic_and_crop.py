"""
调试脚本：
1. 连接到已有浏览器（复用 XHS MCP server 的 Playwright 实例）
2. 在发布页面输入话题 #梅尔，截图分析下拉第一项位置
3. 检查裁切弹窗（图片编辑）的 DOM 结构和位置

使用方法：
  在 MCP 服务器运行时，另开终端执行：
  cd D:\XHS-AI-Tool\MCP
  python debug_topic_and_crop.py
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.async_api import async_playwright


XHS_PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch"


async def main():
    async with async_playwright() as p:
        # 连接到已有的 Chromium 实例（CDP）
        # 注意：需要先用 --remote-debugging-port 启动浏览器，或直接 launch 新实例
        # 这里直接 launch 一个新的用于调试
        browser = await p.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        
        # 加载 cookies
        cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.json")
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        
        if os.path.exists(cookies_path):
            with open(cookies_path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            await context.add_cookies(cookies)
            print(f"✅ 已加载 {len(cookies)} 个 cookie")
        
        page = await context.new_page()
        
        # ============================================================
        # 步骤1: 导航到发布页面
        # ============================================================
        print("\n📍 步骤1: 导航到发布页面...")
        await page.goto(XHS_PUBLISH_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(4)
        
        # 点击"上传图文"标签
        await page.evaluate("""
            () => {
                const tabs = document.querySelectorAll('div.creator-tab');
                for (const tab of tabs) {
                    if ((tab.innerText || '').trim().includes('上传图文')) {
                        tab.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        await asyncio.sleep(2)
        
        # ============================================================
        # 步骤2: 上传一张测试图片（如果有的话）
        # ============================================================
        test_img = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "../Tool/data/projects/lol_comic/ep03/page_01_0.png"
        ))
        if os.path.exists(test_img):
            print(f"\n📍 步骤2: 上传测试封面图: {test_img}")
            try:
                await page.wait_for_selector("input.upload-input", state="attached", timeout=10000)
                file_locator = page.locator("input.upload-input")
                await file_locator.set_input_files([test_img])
                print("✅ 图片已提交")
                await asyncio.sleep(5)
                
                # 截图：上传后立即查看是否有裁切弹窗
                await page.screenshot(path="debug_after_upload_crop.png", full_page=False)
                print("📸 上传后截图: debug_after_upload_crop.png")
                
                # 分析裁切弹窗
                crop_info = await page.evaluate("""
                    () => {
                        const selectors = [
                            '[aria-label="图片编辑"]',
                            '.el-overlay-dialog[aria-label="图片编辑"]',
                            '[class*="image-editor"]',
                            '[class*="crop"]',
                            '[class*="edit-image"]',
                        ];
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el) {
                                const rect = el.getBoundingClientRect();
                                // 找所有按钮
                                const btns = [];
                                el.querySelectorAll('button, [class*="btn"]').forEach(btn => {
                                    const r = btn.getBoundingClientRect();
                                    btns.push({
                                        text: (btn.textContent || '').trim().substring(0, 30),
                                        x: Math.round(r.x + r.width/2),
                                        y: Math.round(r.y + r.height/2),
                                        visible: r.width > 0 && r.height > 0,
                                    });
                                });
                                return {
                                    found: true,
                                    sel,
                                    rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                                    tagName: el.tagName,
                                    className: el.className.substring(0, 100),
                                    buttons: btns,
                                    innerHTML_preview: el.innerHTML.substring(0, 500),
                                };
                            }
                        }
                        // 找所有弹窗
                        const overlays = document.querySelectorAll('.el-overlay, .el-dialog, [class*="dialog"], [class*="modal"]');
                        const found = [];
                        overlays.forEach(el => {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                found.push({
                                    tag: el.tagName,
                                    cls: el.className.substring(0, 80),
                                    aria: el.getAttribute('aria-label'),
                                    rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                                });
                            }
                        });
                        return { found: false, overlays: found };
                    }
                """)
                print(f"\n🔍 裁切弹窗分析:\n{json.dumps(crop_info, ensure_ascii=False, indent=2)}")
                
            except Exception as e:
                print(f"❌ 上传失败: {e}")
        else:
            print(f"⚠️ 测试图片不存在: {test_img}，跳过上传步骤")
        
        # ============================================================
        # 步骤3: 分析话题下拉（在内容编辑器中输入 #梅尔）
        # ============================================================
        print("\n📍 步骤3: 分析话题下拉...")
        
        # 找内容编辑器
        content_editor = await page.query_selector('div[contenteditable="true"]')
        if content_editor:
            await content_editor.click()
            await asyncio.sleep(0.3)
            await page.keyboard.press("End")
            await asyncio.sleep(0.2)
            await page.keyboard.type(" #梅尔", delay=100)
            await asyncio.sleep(3)  # 等待下拉出现
            
            # 截图：下拉出现后
            await page.screenshot(path="debug_topic_dropdown_before_click.png", full_page=False)
            print("📸 话题下拉截图: debug_topic_dropdown_before_click.png")
            
            # 分析下拉列表
            dropdown_info = await page.evaluate("""
                () => {
                    const selectors = [
                        '.publish-topic-item',
                        '[class*="topic-item"]',
                        '[class*="topicItem"]',
                        '.ql-mention-list-item',
                        '[class*="mention-list"] li',
                        '[class*="mention-item"]',
                        '[class*="suggest"] li',
                        '[class*="suggestion-item"]',
                        '[class*="dropdown"] li',
                        '[class*="popup"] li',
                        'li',
                    ];
                    
                    const results = [];
                    for (const sel of selectors) {
                        const items = document.querySelectorAll(sel);
                        const visible = [];
                        items.forEach((item, idx) => {
                            const rect = item.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0 && rect.y > 100 && rect.y < window.innerHeight - 50) {
                                const text = (item.textContent || '').trim();
                                if (text.includes('梅尔') || text.includes('浏览')) {
                                    visible.push({
                                        idx,
                                        text: text.substring(0, 60),
                                        x: Math.round(rect.x + rect.width/2),
                                        y: Math.round(rect.y + rect.height/2),
                                        rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                                        cls: item.className.substring(0, 60),
                                    });
                                }
                            }
                        });
                        if (visible.length > 0) {
                            results.push({ sel, items: visible });
                        }
                    }
                    return results;
                }
            """)
            print(f"\n🔍 话题下拉分析:")
            for r in dropdown_info:
                print(f"  选择器: {r['sel']}")
                for item in r['items'][:5]:
                    print(f"    [{item['idx']}] ({item['x']},{item['y']}) \"{item['text'][:40]}\" cls={item['cls'][:40]}")
            
            # 用坐标标注截图
            print("\n📍 步骤3b: 用 JS 在截图中标注第一项位置...")
            if dropdown_info and dropdown_info[0]['items']:
                first_item = dropdown_info[0]['items'][0]
                x, y = first_item['x'], first_item['y']
                print(f"  第一项坐标: ({x}, {y}) 文字: {first_item['text'][:40]}")
                
                # 在该坐标截图前标注
                await page.evaluate(f"""
                    () => {{
                        const marker = document.createElement('div');
                        marker.style.cssText = 'position:fixed;left:{x-20}px;top:{y-20}px;width:40px;height:40px;border:3px solid red;border-radius:50%;z-index:999999;pointer-events:none;background:rgba(255,0,0,0.2);';
                        marker.id = 'debug-marker';
                        document.body.appendChild(marker);
                    }}
                """)
                await page.screenshot(path="debug_topic_dropdown_marked.png", full_page=False)
                print("📸 标注截图: debug_topic_dropdown_marked.png")
                
                # 清除标注
                await page.evaluate("() => { const m = document.getElementById('debug-marker'); if(m) m.remove(); }")
                
                # 点击第一项
                print(f"\n📍 步骤3c: 点击第一项 ({x}, {y})...")
                await page.mouse.click(x, y)
                await asyncio.sleep(1)
                await page.screenshot(path="debug_topic_after_click.png", full_page=False)
                print("📸 点击后截图: debug_topic_after_click.png")
            else:
                print("  ❌ 未找到话题下拉项")
                
                # 尝试 ArrowDown 看下拉
                print("  尝试 ArrowDown 触发...")
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(0.5)
                await page.screenshot(path="debug_topic_arrowdown.png", full_page=False)
                print("  📸 ArrowDown后截图: debug_topic_arrowdown.png")
                
                # 再次分析 DOM
                all_visible = await page.evaluate("""
                    () => {
                        const result = [];
                        document.querySelectorAll('*').forEach(el => {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 50 && rect.height > 10 && rect.height < 80 && rect.y > 200 && rect.y < 800) {
                                const text = (el.textContent || '').trim();
                                if (text.includes('梅尔') && text.length < 50) {
                                    result.push({
                                        tag: el.tagName,
                                        cls: el.className.substring(0, 60),
                                        text: text.substring(0, 50),
                                        x: Math.round(rect.x + rect.width/2),
                                        y: Math.round(rect.y + rect.height/2),
                                    });
                                }
                            }
                        });
                        return result.slice(0, 20);
                    }
                """)
                print(f"  包含'梅尔'的可见元素: {json.dumps(all_visible, ensure_ascii=False, indent=2)}")
        else:
            print("❌ 未找到内容编辑器")
        
        print("\n✅ 调试完成！请查看以下截图:")
        print("  - debug_after_upload_crop.png  (上传后裁切弹窗)")
        print("  - debug_topic_dropdown_before_click.png  (话题下拉)")
        print("  - debug_topic_dropdown_marked.png  (标注第一项)")
        print("  - debug_topic_after_click.png  (点击后结果)")
        
        input("\n按 Enter 关闭浏览器...")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
