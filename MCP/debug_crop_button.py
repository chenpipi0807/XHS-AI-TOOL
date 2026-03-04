"""
调试脚本：专门分析上传图片后出现的裁切/完成按钮位置
找到"完成"按钮的精确坐标，以便在 publish.py 中正确点击

使用方法：
  cd D:\XHS-AI-Tool\MCP
  python debug_crop_button.py
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
        browser = await p.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )

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

        print("\n📍 导航到发布页面...")
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
            }
        """)
        await asyncio.sleep(2)

        # 上传封面图（9:16 比例，会触发裁切）
        test_img = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "../Tool/data/projects/lol_comic/ep03/page_01_0.png"
        ))
        if not os.path.exists(test_img):
            print(f"❌ 测试图片不存在: {test_img}")
            await browser.close()
            return

        print(f"\n📍 上传封面图（9:16，会触发裁切）: {test_img}")
        await page.wait_for_selector("input.upload-input", state="attached", timeout=10000)
        file_locator = page.locator("input.upload-input")
        await file_locator.set_input_files([test_img])
        print("✅ 图片已提交")

        # 等待页面变化
        print("⏳ 等待 6 秒让裁切弹窗出现...")
        await asyncio.sleep(6)

        # 截图
        await page.screenshot(path="debug_crop_full.png", full_page=False)
        print("📸 截图: debug_crop_full.png")

        # 扫描全页面所有可见按钮
        all_btns = await page.evaluate("""
            () => {
                const result = [];
                document.querySelectorAll('button, [class*="btn"], [class*="confirm"], [class*="done"], [class*="finish"]').forEach(el => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0 && rect.y >= 0 && rect.y < window.innerHeight) {
                        const text = (el.textContent || el.innerText || '').trim();
                        result.push({
                            tag: el.tagName,
                            text: text.substring(0, 30),
                            cls: el.className.substring(0, 60),
                            x: Math.round(rect.x + rect.width / 2),
                            y: Math.round(rect.y + rect.height / 2),
                            w: Math.round(rect.width),
                            h: Math.round(rect.height),
                        });
                    }
                });
                return result;
            }
        """)
        print(f"\n🔍 全页面可见按钮（共 {len(all_btns)} 个）:")
        for btn in all_btns:
            flag = " ⭐" if any(kw in btn['text'] for kw in ['完成', '确认', '确定', '裁剪', 'Done', '保存', '取消']) else ""
            print(f"  [{btn['tag']}] ({btn['x']},{btn['y']}) \"{btn['text']}\"{flag}  cls={btn['cls'][:40]}")

        # 专门找"完成"按钮并标注
        done_btns = [b for b in all_btns if any(kw in b['text'] for kw in ['完成', '确认', '确定', 'Done', '保存'])]
        if done_btns:
            print(f"\n⭐ 找到 {len(done_btns)} 个完成/确认类按钮:")
            for btn in done_btns:
                print(f"   ({btn['x']},{btn['y']}) \"{btn['text']}\" cls={btn['cls'][:50]}")
            
            # 在截图中标注所有完成按钮
            markers_js = ""
            for i, btn in enumerate(done_btns):
                markers_js += f"""
                    const m{i} = document.createElement('div');
                    m{i}.style.cssText = 'position:fixed;left:{btn['x']-25}px;top:{btn['y']-25}px;width:50px;height:50px;border:3px solid red;border-radius:50%;z-index:999999;pointer-events:none;background:rgba(255,0,0,0.2);';
                    m{i}.title = '{btn['text']}';
                    document.body.appendChild(m{i});
                """
            await page.evaluate(f"() => {{ {markers_js} }}")
            await page.screenshot(path="debug_crop_buttons_marked.png", full_page=False)
            print("📸 标注截图: debug_crop_buttons_marked.png")

        # 扫描所有 overlay/dialog
        print("\n🔍 扫描所有弹窗/遮罩层:")
        dialogs = await page.evaluate("""
            () => {
                const result = [];
                const sels = [
                    '.el-overlay', '.el-dialog', '[class*="dialog"]',
                    '[class*="modal"]', '[class*="overlay"]',
                    '[aria-label="图片编辑"]', '[class*="image-edit"]',
                    '[class*="crop"]', '[class*="editor"]',
                ];
                const seen = new Set();
                for (const sel of sels) {
                    document.querySelectorAll(sel).forEach(el => {
                        if (seen.has(el)) return;
                        seen.add(el);
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        result.push({
                            sel,
                            tag: el.tagName,
                            cls: el.className.substring(0, 80),
                            aria: el.getAttribute('aria-label'),
                            display: style.display,
                            visibility: style.visibility,
                            zIndex: style.zIndex,
                            rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                            childCount: el.children.length,
                        });
                    });
                }
                return result;
            }
        """)
        print(f"  共 {len(dialogs)} 个弹窗/遮罩元素:")
        for d in dialogs:
            visible = d['rect']['w'] > 0 and d['rect']['h'] > 0
            print(f"  {'🟢' if visible else '⚫'} [{d['tag']}] aria={d['aria']} display={d['display']} vis={d['visibility']} z={d['zIndex']}")
            print(f"      cls={d['cls'][:60]}")
            print(f"      rect=({d['rect']['x']},{d['rect']['y']},{d['rect']['w']},{d['rect']['h']}) children={d['childCount']}")

        # 查找页面中所有包含"完成"文字的元素（不限按钮）
        print("\n🔍 所有包含'完成'文字的可见元素:")
        done_els = await page.evaluate("""
            () => {
                const result = [];
                document.querySelectorAll('*').forEach(el => {
                    const text = (el.childNodes.length === 1 && el.childNodes[0].nodeType === 3)
                        ? el.textContent.trim()
                        : (el.innerText || '').trim();
                    if (text === '完成' || text === '确认') {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            result.push({
                                tag: el.tagName,
                                cls: el.className.substring(0, 60),
                                text,
                                x: Math.round(rect.x + rect.width / 2),
                                y: Math.round(rect.y + rect.height / 2),
                                rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                            });
                        }
                    }
                });
                return result;
            }
        """)
        if done_els:
            print(f"  找到 {len(done_els)} 个:")
            for el in done_els:
                print(f"  [{el['tag']}] ({el['x']},{el['y']}) \"{el['text']}\" cls={el['cls']}")
        else:
            print("  未找到「完成」/「确认」文字元素")

        print("\n✅ 调试完成！截图文件:")
        print("  - debug_crop_full.png (上传后全页)")
        print("  - debug_crop_buttons_marked.png (标注完成按钮)")
        
        input("\n按 Enter 关闭浏览器（关闭前可手动查看页面）...")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
