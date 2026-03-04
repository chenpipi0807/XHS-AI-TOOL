"""
小红书 MCP 服务主入口
对应 Go 版本: main.go
启动 MCP HTTP 服务器，监听 http://localhost:18060/mcp

使用方式:
    python main.py                    # 启动 MCP 服务
    python main.py --port 18060       # 指定端口
    python main.py --host 0.0.0.0    # 指定绑定地址
    python main.py --headless         # 无头模式（服务器环境）
"""
import argparse
import asyncio
import logging
import os
import sys

# 确保 MCP 目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# 降低部分库的日志级别
logging.getLogger("playwright").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="小红书 MCP 服务器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                         启动服务（默认端口 18060）
  python main.py --port 8080             使用端口 8080
  python main.py --headless              无头浏览器模式
  python main.py --proxy http://127.0.0.1:7890  使用代理
  python login_tool.py                   运行登录工具
        """,
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MCP_HOST", "0.0.0.0"),
        help="监听地址（默认: 0.0.0.0）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_PORT", "18060")),
        help="监听端口（默认: 18060）",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=os.environ.get("HEADLESS", "").lower() in ("true", "1", "yes"),
        help="使用无头浏览器模式",
    )
    parser.add_argument(
        "--proxy",
        default=os.environ.get("XHS_PROXY", ""),
        help="代理服务器地址（例如: http://127.0.0.1:7890）",
    )
    parser.add_argument(
        "--cookies-path",
        default=os.environ.get("COOKIES_PATH", ""),
        help="cookies 文件路径（默认自动检测）",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别（默认: INFO）",
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="使用 stdio 模式（用于 Claude Desktop 等工具）",
    )
    return parser.parse_args()


def setup_environment(args):
    """根据命令行参数设置环境变量"""
    if args.headless:
        os.environ["HEADLESS"] = "true"
    if args.proxy:
        os.environ["XHS_PROXY"] = args.proxy
    if args.cookies_path:
        os.environ["COOKIES_PATH"] = args.cookies_path

    # 设置日志级别
    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)


async def run_http_server(host: str, port: int):
    """
    启动 MCP HTTP Streamable 服务器
    使用 uvicorn + Starlette + StreamableHTTPSessionManager
    """
    import uvicorn
    from mcp_server import app as mcp_app, create_starlette_app

    MCP_PATH = "/mcp"

    logger.info("=" * 60)
    logger.info("  小红书 MCP 服务器 (Python)")
    logger.info("=" * 60)
    logger.info(f"  服务地址: http://{host}:{port}{MCP_PATH}")
    logger.info(f"  健康检查: http://{host}:{port}/health")
    logger.info(f"  无头模式: {os.environ.get('HEADLESS', 'false')}")
    logger.info(f"  代理设置: {os.environ.get('XHS_PROXY', '未设置')}")
    logger.info("=" * 60)
    logger.info("")
    logger.info("提示: 首次使用请先运行 'python login_tool.py' 进行登录")
    logger.info("")

    # 创建 Starlette ASGI 应用
    starlette_app = create_starlette_app(mcp_app, path=MCP_PATH)

    # 配置 uvicorn
    config = uvicorn.Config(
        app=starlette_app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    try:
        await server.serve()
    except KeyboardInterrupt:
        logger.info("服务器已停止")
    finally:
        # 关闭浏览器
        try:
            from browser.browser import close_browser
            await close_browser()
        except Exception:
            pass


async def run_stdio_server():
    """
    通过 stdio 运行 MCP 服务器
    用于与 Claude Desktop 等工具的 stdio 模式集成
    """
    from mcp.server.stdio import stdio_server
    from mcp_server import app as mcp_app

    logger.info("通过 stdio 启动 MCP 服务器...")

    async with stdio_server() as (read_stream, write_stream):
        await mcp_app.run(
            read_stream,
            write_stream,
            mcp_app.create_initialization_options(),
        )


def main():
    """主函数"""
    args = parse_args()
    setup_environment(args)

    if args.stdio:
        # stdio 模式
        asyncio.run(run_stdio_server())
    else:
        # HTTP 模式
        try:
            asyncio.run(run_http_server(args.host, args.port))
        except KeyboardInterrupt:
            logger.info("服务器已停止")
        except Exception as e:
            logger.error(f"服务器启动失败: {e}", exc_info=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
