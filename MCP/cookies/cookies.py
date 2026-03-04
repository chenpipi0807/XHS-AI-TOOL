"""
Cookie 持久化管理模块
对应 Go 版本: cookies/cookies.go
"""
import json
import os
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def get_cookies_file_path() -> str:
    """
    获取 cookies 文件路径
    优先级: /tmp/cookies.json > COOKIES_PATH 环境变量 > 当前目录 cookies.json
    """
    # Linux/Mac 临时目录
    tmp_path = "/tmp/cookies.json"
    if os.path.exists(tmp_path):
        return tmp_path

    # 环境变量指定路径
    env_path = os.environ.get("COOKIES_PATH", "")
    if env_path:
        return env_path

    # Windows 临时目录
    win_tmp = os.path.join(os.environ.get("TEMP", ""), "cookies.json")
    if os.path.exists(win_tmp):
        return win_tmp

    # 当前目录
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(current_dir, "cookies.json")


def load_cookies() -> List[Dict[str, Any]]:
    """
    从文件加载 cookies
    返回 cookies 列表，如果文件不存在则返回空列表
    """
    cookies_path = get_cookies_file_path()
    if not os.path.exists(cookies_path):
        logger.info(f"Cookies 文件不存在: {cookies_path}")
        return []

    try:
        with open(cookies_path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
            logger.info(f"成功加载 {len(cookies)} 个 cookies，路径: {cookies_path}")
            return cookies
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"加载 cookies 失败: {e}")
        return []


def save_cookies(cookies: List[Dict[str, Any]]) -> bool:
    """
    保存 cookies 到文件
    返回是否保存成功
    """
    cookies_path = get_cookies_file_path()

    # 确保目录存在
    cookies_dir = os.path.dirname(cookies_path)
    if cookies_dir and not os.path.exists(cookies_dir):
        try:
            os.makedirs(cookies_dir, exist_ok=True)
        except OSError as e:
            logger.error(f"创建 cookies 目录失败: {e}")
            return False

    try:
        with open(cookies_path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        logger.info(f"成功保存 {len(cookies)} 个 cookies，路径: {cookies_path}")
        return True
    except IOError as e:
        logger.error(f"保存 cookies 失败: {e}")
        return False


def delete_cookies() -> bool:
    """
    删除 cookies 文件
    返回是否删除成功
    """
    cookies_path = get_cookies_file_path()
    if not os.path.exists(cookies_path):
        logger.info(f"Cookies 文件不存在，无需删除: {cookies_path}")
        return True

    try:
        os.remove(cookies_path)
        logger.info(f"成功删除 cookies 文件: {cookies_path}")
        return True
    except OSError as e:
        logger.error(f"删除 cookies 文件失败: {e}")
        return False


def cookies_exist() -> bool:
    """检查 cookies 文件是否存在"""
    cookies_path = get_cookies_file_path()
    return os.path.exists(cookies_path)


def convert_playwright_cookies(cookies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    转换 Playwright cookies 格式为标准格式
    Playwright cookie 格式和标准 JSON 格式可能略有不同
    """
    converted = []
    for cookie in cookies:
        c = {
            "name": cookie.get("name", ""),
            "value": cookie.get("value", ""),
            "domain": cookie.get("domain", ""),
            "path": cookie.get("path", "/"),
            "expires": cookie.get("expires", -1),
            "httpOnly": cookie.get("httpOnly", False),
            "secure": cookie.get("secure", False),
            "sameSite": cookie.get("sameSite", "Lax"),
        }
        converted.append(c)
    return converted
