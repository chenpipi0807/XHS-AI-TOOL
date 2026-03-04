"""
小红书独立登录工具
对应 Go 版本: cmd/login/main.go
用于首次登录或登录过期时重新获取 cookies
运行方式: python login_tool.py
"""
import asyncio
import base64
import logging
import os
import sys
import tempfile

# 确保 MCP 目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def save_qrcode_to_file(qrcode_b64: str) -> str:
    """将 Base64 二维码保存为本地图片文件"""
    qrcode_bytes = base64.b64decode(qrcode_b64)
    fd, path = tempfile.mkstemp(suffix=".png", prefix="xhs_qrcode_")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(qrcode_bytes)
    return path


async def open_qrcode_image(path: str) -> None:
    """尝试使用系统默认程序打开二维码图片"""
    import subprocess
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        logger.info(f"二维码图片已打开: {path}")
    except Exception as e:
        logger.warning(f"无法自动打开图片: {e}")
        logger.info(f"请手动打开二维码图片: {path}")


async def main():
    """主登录流程"""
    print("=" * 60)
    print("  小红书登录工具")
    print("  Xiaohongshu Login Tool")
    print("=" * 60)
    print()

    from xiaohongshu.login import check_login_status, get_login_qrcode, wait_for_login
    from browser.browser import save_current_cookies

    # 1. 检查是否已登录
    print("检查登录状态...")
    is_logged = await check_login_status()

    if is_logged:
        print("✓ 已登录！无需重新扫码。")
        print()
        await save_current_cookies()
        print("✓ cookies 已保存")
        return

    print("✗ 未登录，开始扫码登录流程...")
    print()

    # 2. 获取二维码
    print("获取登录二维码...")
    qrcode_b64 = await get_login_qrcode()

    if not qrcode_b64:
        print("✗ 无法获取登录二维码，请检查网络连接")
        return

    # 3. 保存并打开二维码
    qrcode_path = await save_qrcode_to_file(qrcode_b64)
    print(f"✓ 二维码已保存到: {qrcode_path}")
    print()

    # 尝试自动打开
    await open_qrcode_image(qrcode_path)

    print("请使用小红书 App 扫描二维码登录：")
    print("  1. 打开小红书 App")
    print("  2. 点击右上角相机图标或扫一扫")
    print("  3. 扫描上方二维码")
    print("  4. 在 App 中确认登录")
    print()
    print("等待扫码登录（120秒超时）...")

    # 4. 等待登录完成
    success = await wait_for_login(timeout_seconds=120)

    if success:
        print()
        print("✓ 登录成功！")
        await save_current_cookies()
        print("✓ cookies 已保存，下次启动无需重新登录")
    else:
        print()
        print("✗ 登录超时，请重新运行此工具")

    # 清理临时文件
    try:
        os.remove(qrcode_path)
    except Exception:
        pass

    print()
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
