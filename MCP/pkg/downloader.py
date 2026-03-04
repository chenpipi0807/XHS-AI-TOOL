"""
HTTP 图片下载器
对应 Go 版本: configs/image.go 相关功能
支持从 URL 下载图片到本地文件，用于发布时的图片预处理
"""
import asyncio
import logging
import os
import tempfile
from typing import List, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# 默认请求头（模拟浏览器）
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.xiaohongshu.com/",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}


async def download_image(
    url: str,
    save_path: Optional[str] = None,
    timeout: int = 30,
) -> str:
    """
    下载单张图片到本地
    
    :param url: 图片 URL
    :param save_path: 保存路径，如果不提供则保存到临时文件
    :param timeout: 超时时间（秒）
    :return: 本地文件路径
    """
    if not url:
        raise ValueError("图片 URL 不能为空")

    # 从 URL 推断文件扩展名
    parsed = urlparse(url)
    path_part = parsed.path
    ext = os.path.splitext(path_part)[1]
    if not ext or ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"):
        ext = ".jpg"

    # 确定保存路径
    if not save_path:
        fd, save_path = tempfile.mkstemp(suffix=ext, prefix="xhs_img_")
        os.close(fd)

    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

            # 确保目录存在
            save_dir = os.path.dirname(save_path)
            if save_dir:
                os.makedirs(save_dir, exist_ok=True)

            with open(save_path, "wb") as f:
                f.write(response.content)

        logger.info(f"图片下载成功: {url} -> {save_path}")
        return save_path

    except httpx.HTTPError as e:
        logger.error(f"下载图片失败 {url}: {e}")
        raise
    except Exception as e:
        logger.error(f"保存图片失败 {save_path}: {e}")
        raise


async def download_images(
    urls: List[str],
    save_dir: Optional[str] = None,
    timeout: int = 30,
    max_concurrent: int = 5,
) -> List[str]:
    """
    批量下载图片
    
    :param urls: 图片 URL 列表
    :param save_dir: 保存目录，如果不提供则使用临时目录
    :param timeout: 超时时间（秒）
    :param max_concurrent: 最大并发下载数
    :return: 本地文件路径列表
    """
    if not urls:
        return []

    # 确定保存目录
    if not save_dir:
        save_dir = tempfile.mkdtemp(prefix="xhs_images_")
    else:
        os.makedirs(save_dir, exist_ok=True)

    semaphore = asyncio.Semaphore(max_concurrent)

    async def download_one(url: str, index: int) -> Optional[str]:
        async with semaphore:
            try:
                parsed = urlparse(url)
                ext = os.path.splitext(parsed.path)[1]
                if not ext or ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                    ext = ".jpg"
                save_path = os.path.join(save_dir, f"image_{index:03d}{ext}")
                return await download_image(url, save_path, timeout)
            except Exception as e:
                logger.error(f"下载图片 {index} 失败: {e}")
                return None

    tasks = [download_one(url, i) for i, url in enumerate(urls)]
    results = await asyncio.gather(*tasks)

    # 过滤掉下载失败的
    successful = [r for r in results if r is not None]
    logger.info(f"批量下载完成: {len(successful)}/{len(urls)} 成功")
    return successful


def is_url(path: str) -> bool:
    """判断字符串是否为 URL"""
    return path.startswith(("http://", "https://"))


# Tool/data/projects/ 目录（与 MCP 目录平行：../Tool/data/projects/）
# MCP 服务器位于 D:\XHS-AI-Tool\MCP，项目文件位于 D:\XHS-AI-Tool\Tool\data\projects\
_MCP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # D:\XHS-AI-Tool\MCP
_TOOL_DATA_PROJECTS = os.path.normpath(
    os.path.join(_MCP_DIR, "..", "Tool", "data", "projects")
)


def _fuzzy_match(candidate: str) -> str | None:
    """
    精确路径不存在时，在同目录下模糊匹配。
    规则：stem 相同且以 stem 开头（如 page_01.png → page_01_0.png / page_01_1.png）。
    返回按文件名排序的第一个匹配，找不到则返回 None。
    """
    if os.path.exists(candidate):
        return candidate
    dirpath = os.path.dirname(candidate)
    if not os.path.isdir(dirpath):
        return None
    stem, ext = os.path.splitext(os.path.basename(candidate))
    ext_lower = ext.lower()
    matches = sorted(
        f for f in os.listdir(dirpath)
        if f.lower().endswith(ext_lower) and os.path.splitext(f)[0].startswith(stem)
    )
    if matches:
        found = os.path.join(dirpath, matches[0])
        logger.info(f"模糊匹配: {os.path.basename(candidate)} → {matches[0]}")
        return found
    return None


def _resolve_local_path(path: str) -> str | None:
    """
    将各种形式的路径解析为绝对路径。
    支持：
      - 绝对路径（直接检查，失败则模糊匹配同目录）
      - projects/xxx   → Tool/data/projects/xxx
      - lol_comic/xxx  → Tool/data/projects/lol_comic/xxx（list_project_files 返回的相对路径）
      - 其他相对路径   → 依次尝试 MCP目录 / Tool/data/projects 目录
    精确路径不存在时，对每个候选目录执行模糊匹配（stem 前缀匹配），
    例如 page_01.png 会自动匹配 page_01_0.png。
    """
    if os.path.isabs(path):
        return _fuzzy_match(path)

    # projects/ 前缀：Kimi 有时会在 list_project_files 返回的路径前加 "projects/"
    if path.startswith("projects/") or path.startswith("projects\\"):
        rel = path[len("projects/"):].lstrip("\\/")
        candidate = os.path.normpath(os.path.join(_TOOL_DATA_PROJECTS, rel))
        result = _fuzzy_match(candidate)
        if result:
            return result

    # 直接拼到 Tool/data/projects/（list_project_files 返回的格式是相对于 projects/ 的路径）
    candidate = os.path.normpath(os.path.join(_TOOL_DATA_PROJECTS, path))
    result = _fuzzy_match(candidate)
    if result:
        return result

    # fallback: 相对于 MCP 目录
    candidate2 = os.path.normpath(os.path.join(_MCP_DIR, path))
    result = _fuzzy_match(candidate2)
    if result:
        return result

    return None


async def ensure_local_paths(paths: List[str]) -> List[str]:
    """
    确保所有路径都是本地文件。
    如果路径是 URL，则下载到本地；
    如果是相对路径，尝试从 Tool/data/projects/ 和 MCP 目录解析。

    :param paths: 文件路径或 URL 列表
    :return: 本地文件路径（绝对路径）列表
    """
    local_paths = []

    for path in paths:
        if is_url(path):
            try:
                local_path = await download_image(path)
                local_paths.append(local_path)
            except Exception as e:
                logger.error(f"下载图片失败，跳过: {path}, 错误: {e}")
        else:
            resolved = _resolve_local_path(path)
            if resolved:
                local_paths.append(resolved)
            else:
                logger.warning(f"文件不存在，跳过: {path}")

    return local_paths
