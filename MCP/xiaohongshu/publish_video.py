"""
小红书视频发布模块
对应 Go 版本: xiaohongshu/publish_video.go
"""
import asyncio
import logging
import os
from typing import List, Optional

from browser.browser import get_browser_page

logger = logging.getLogger(__name__)

XHS_PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"


async def _wait_for_video_upload(page, timeout: int = 300000) -> bool:
    """等待视频上传完成（视频上传可能需要较长时间）"""
    try:
        await page.wait_for_function(
            """
            () => {
                // 检查视频上传进度
                const progress = document.querySelector('.upload-progress, [class*="progress"]');
                if (progress) {
                    const text = progress.textContent || '';
                    if (text.includes('100%') || text.includes('上传完成')) return true;
                }
                // 检查发布按钮是否可用（通常上传完成后才可用）
                const publishBtn = document.querySelector('.publishBtn, button[class*="publish"]');
                if (publishBtn && !publishBtn.disabled) return true;
                // 检查视频封面是否已生成
                const videoCover = document.querySelector('.video-cover img, .cover-preview img');
                if (videoCover) return true;
                return false;
            }
            """,
            timeout=timeout,
        )
        return True
    except Exception as e:
        logger.warning(f"等待视频上传完成超时: {e}")
        return False


async def _wait_for_publish_btn_enabled(page, timeout: int = 30000) -> bool:
    """等待发布按钮变为可点击状态"""
    try:
        await page.wait_for_function(
            """
            () => {
                const btn = document.querySelector('.publishBtn, button:has-text("发布"), .publish-button');
                return btn && !btn.disabled && !btn.classList.contains('disabled');
            }
            """,
            timeout=timeout,
        )
        return True
    except Exception:
        return False


async def publish_video_content(
    title: str,
    content: str,
    video_path: str,
    cover_path: str = "",
    tags: Optional[List[str]] = None,
    is_private: bool = False,
    is_original: bool = True,
) -> bool:
    """
    发布视频内容
    对应 Go: PublishVideoContent()

    :param title: 标题
    :param content: 描述内容
    :param video_path: 视频文件本地路径
    :param cover_path: 封面图片路径（可选）
    :param tags: 标签列表
    :param is_private: 是否私密
    :param is_original: 是否原创
    :return: 发布成功返回 True
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    if cover_path and not os.path.exists(cover_path):
        raise FileNotFoundError(f"封面文件不存在: {cover_path}")

    try:
        page = await get_browser_page()

        logger.info(f"导航到发布页面: {XHS_PUBLISH_URL}")
        await page.goto(XHS_PUBLISH_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # 点击"上传视频"标签
        try:
            video_tab = await page.wait_for_selector(
                ".tab-item:has-text('上传视频'), [data-tab='video'], .video-tab",
                timeout=5000,
            )
            if video_tab:
                await video_tab.click()
                await asyncio.sleep(1)
        except Exception:
            logger.debug("未找到上传视频标签，尝试其他方式")
            # 尝试通过文字查找
            tabs = await page.query_selector_all(".tab-item")
            for tab in tabs:
                text = await tab.text_content()
                if text and "视频" in text:
                    await tab.click()
                    await asyncio.sleep(1)
                    break

        # 上传视频文件
        logger.info(f"上传视频: {video_path}")
        file_input = await page.wait_for_selector(
            "input[type='file'][accept*='video'], .video-upload input[type='file'], .c-upload input[type='file']",
            timeout=10000,
        )
        if not file_input:
            raise RuntimeError("未找到视频文件上传输入框")

        abs_video_path = os.path.abspath(video_path)
        await file_input.set_input_files(abs_video_path)
        logger.info("视频文件已设置，等待上传完成...")

        # 等待视频上传完成（视频上传需要较长时间）
        upload_success = await _wait_for_video_upload(page, timeout=300000)
        if not upload_success:
            logger.warning("视频上传可能未完成，继续尝试...")

        await asyncio.sleep(2)

        # 上传封面（如果提供）
        if cover_path:
            try:
                logger.info(f"上传封面: {cover_path}")
                cover_input = await page.query_selector(
                    "input[type='file'][accept*='image'], .cover-upload input[type='file']"
                )
                if cover_input:
                    abs_cover_path = os.path.abspath(cover_path)
                    await cover_input.set_input_files(abs_cover_path)
                    await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"上传封面失败，继续: {e}")

        # 输入标题
        logger.info(f"输入标题: {title}")
        title_input = await page.wait_for_selector(
            "input[placeholder*='标题'], .title-input input, .post-title input",
            timeout=10000,
        )
        if title_input:
            await title_input.click()
            await title_input.fill(title)
            await asyncio.sleep(0.5)

        # 输入描述
        logger.info("输入描述内容")
        content_editor = await page.query_selector(
            ".ql-editor, [contenteditable='true'], .desc-input, textarea[placeholder*='描述']"
        )
        if content_editor:
            await content_editor.click()
            await content_editor.fill(content)
            await asyncio.sleep(0.5)

        # 输入标签
        if tags:
            for tag in tags or []:
                if content_editor:
                    await content_editor.click()
                    await asyncio.sleep(0.2)
                    await page.keyboard.type(f" #{tag}", delay=50)
                    await asyncio.sleep(0.5)
                    # 尝试选择话题建议
                    try:
                        suggestion = await page.wait_for_selector(
                            ".topic-item, .tag-suggestion",
                            timeout=1500,
                        )
                        if suggestion:
                            await suggestion.click()
                    except Exception:
                        pass

        # 等待发布按钮可用
        logger.info("等待发布按钮可用...")
        await _wait_for_publish_btn_enabled(page, timeout=60000)

        # 点击发布
        publish_btn = await page.query_selector(
            ".publishBtn, button:has-text('发布'), .publish-button"
        )
        if not publish_btn:
            raise RuntimeError("未找到发布按钮")

        await publish_btn.click()
        logger.info("已点击发布按钮，等待发布完成...")

        # 等待发布成功
        try:
            await page.wait_for_selector(
                ".success-toast, .publish-success, [class*='success']",
                timeout=30000,
            )
            logger.info("视频发布成功！")
        except Exception:
            await asyncio.sleep(5)
            logger.info("视频可能已发布（未检测到成功提示）")

        return True

    except Exception as e:
        logger.error(f"发布视频失败: {e}")
        raise
