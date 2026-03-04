# 小红书 MCP Python 实现

基于 Python 的小红书 MCP (Model Context Protocol) 工具，使用 Playwright 浏览器自动化技术，完整实现 13 个 MCP 工具。

## 功能特性

- ✅ **登录管理**: 二维码扫码登录、登录状态检测、Cookies 持久化
- ✅ **内容发布**: 图文发布（含话题标签、定时发布、原创声明）、视频发布
- ✅ **内容搜索**: 关键词搜索 + 多维度筛选器（排序/类型/时间/范围）
- ✅ **推荐列表**: 获取个性化首页推荐流
- ✅ **帖子详情**: 获取完整帖子内容 + 自动加载所有评论
- ✅ **互动操作**: 发表评论、回复评论、点赞/取消点赞、收藏/取消收藏
- ✅ **用户资料**: 获取用户基本信息、粉丝数据、笔记列表

## 项目结构

```
MCP/
├── main.py                    # 主入口，启动 MCP HTTP 服务器
├── login_tool.py              # 独立登录工具
├── mcp_server.py              # MCP 工具注册（13个工具）
├── service.py                 # 业务服务层
├── requirements.txt           # Python 依赖
├── browser/
│   ├── __init__.py
│   └── browser.py             # Playwright 浏览器管理
├── cookies/
│   ├── __init__.py
│   └── cookies.py             # Cookie 持久化管理
├── xiaohongshu/
│   ├── __init__.py
│   ├── types.py               # Pydantic 数据模型
│   ├── login.py               # 登录模块
│   ├── feeds.py               # 首页推荐列表
│   ├── search.py              # 搜索模块
│   ├── feed_detail.py         # 帖子详情（含评论加载）
│   ├── publish.py             # 图文发布
│   ├── publish_video.py       # 视
│   ├── comment_feed.py        # 评论模块
│   ├── like_favorite.py       # 点赞/收藏
│   └── user_profile.py        # 用户资料
└── pkg/
    ├── __init__.py
    └── downloader.py          # HTTP 图片下载器
```

## 安装

### 1. 安装 Python 依赖

```bash
cd MCP
pip install -r requirements.txt
```

### 2. 安装 Playwright 浏览器

```bash
playwright install chromium
```

## 使用方式

### 第一步：登录

首次使用或 Cookies 过期时，运行登录工具：

```bash
cd MCP
python login_tool.py
```

程序会：
1. 打开浏览器显示二维码
2. 将二维码图片保存到临时文件并自动打开
3. 等待您使用小红书 App 扫码登录
4. 登录成功后自动保存 Cookies

### 第二步：启动 MCP 服务

```bash
python main.py
```

服务启动后监听 `http://localhost:18060/mcp`

### 命令行参数

```bash
python main.py --help

选项:
  --host        监听地址（默认: 0.0.0.0）
  --port        监听端口（默认: 18060）
  --headless    无头浏览器模式（适合服务器环境）
  --proxy       代理服务器地址（例如: http://127.0.0.1:7890）
  --cookies-path  cookies 文件路径
  --log-level   日志级别（DEBUG/INFO/WARNING/ERROR）
```

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `HEADLESS` | 无头模式 | `false` |
| `XHS_PROXY` | 代理地址 | 空 |
| `COOKIES_PATH` | cookies 文件路径 | 自动检测 |
| `MCP_HOST` | 服务监听地址 | `0.0.0.0` |
| `MCP_PORT` | 服务监听端口 | `18060` |

## MCP 工具列表

### 1. `check_login_status`
检查当前登录状态

### 2. `get_login_qrcode`
获取登录二维码（Base64 图片）

### 3. `delete_cookies`
删除登录 cookies（用于强制重新登录）

### 4. `publish_content`
发布图文笔记

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | ✅ | 标题（最多20字） |
| `content` | string | ✅ | 正文内容 |
| `image_paths` | array | ✅ | 图片路径列表（支持本地路径和URL，最多9张） |
| `tags` | array | ❌ | 话题标签列表（不含#号） |
| `is_private` | boolean | ❌ | 是否私密（默认false） |
| `scheduled_time` | string | ❌ | 定时发布时间，格式: "2024-01-01 12:00" |
| `is_original` | boolean | ❌ | 是否原创声明（默认true） |

### 5. `publish_with_video`
发布视频笔记

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | ✅ | 标题 |
| `content` | string | ✅ | 描述内容 |
| `video_path` | string | ✅ | 视频文件本地路径 |
| `cover_path` | string | ❌ | 封面图片路径 |
| `tags` | array | ❌ | 话题标签 |
| `is_private` | boolean | ❌ | 是否私密 |
| `is_original` | boolean | ❌ | 是否原创 |

### 6. `list_feeds`
获取首页推荐内容列表

### 7. `search_feeds`
搜索笔记

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `keyword` | string | ✅ | 搜索关键词 |
| `sort_by` | string | ❌ | 排序: `general`/`popularity_descending`/`time_descending` |
| `note_type` | string | ❌ | 类型: `0`(全部)/`1`(视频)/`2`(图文) |
| `publish_time` | string | ❌ | 时间: `不限`/`一天内`/`一周内`/`半年内` |
| `search_scope` | string | ❌ | 范围: `全部`/`已关注`/`同城` |

### 8. `get_feed_detail`
获取帖子详情（含评论）

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `feed_id` | string | ✅ | 帖子 ID |
| `xsec_token` | string | ❌ | 安全 token |
| `load_comments` | boolean | ❌ | 是否加载评论（默认true） |
| `max_comments` | integer | ❌ | 最大评论数（默认100） |

### 9. `post_comment_to_feed`
发表评论

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `feed_id` | string | ✅ | 帖子 ID |
| `content` | string | ✅ | 评论内容 |
| `xsec_token` | string | ❌ | 安全 token |

### 10. `reply_comment_in_feed`
回复评论

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `feed_id` | string | ✅ | 帖子 ID |
| `comment_id` | string | ✅ | 要回复的评论 ID |
| `comment_user_id` | string | ✅ | 评论作者用户 ID |
| `content` | string | ✅ | 回复内容 |

### 11. `like_feed`
点赞/取消点赞

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `feed_id` | string | ✅ | 帖子 ID |
| `xsec_token` | string | ❌ | 安全 token |

### 12. `favorite_feed`
收藏/取消收藏

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `feed_id` | string | ✅ | 帖子 ID |
| `xsec_token` | string | ❌ | 安全 token |

### 13. `user_profile`
获取用户资料

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `user_id` | string | ✅ | 用户 ID |
| `xsec_token` | string | ❌ | 安全 token |

## 在 AI 工具中配置

### Cursor / VSCode (MCP 配置)

在 `.cursor/mcp.json` 或 `.vscode/mcp.json` 中添加：

```json
{
  "mcpServers": {
    "xiaohongshu-python": {
      "url": "http://localhost:18060/mcp"
    }
  }
}
```

### Claude Desktop

在 `claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "xiaohongshu": {
      "command": "python",
      "args": ["D:/XHS-AI-Tool/MCP/main.py"],
      "env": {
        "HEADLESS": "false"
      }
    }
  }
}
```

## 技术栈

| 组件 | 库 | 说明 |
|------|----|------|
| 浏览器自动化 | `playwright-python` | 替代 Go 版本的 `go-rod` |
| MCP 协议 | `mcp` | 官方 Python MCP SDK |
| HTTP 服务 | `uvicorn` + `starlette` | ASGI 服务器 |
| 数据验证 | `pydantic v2` | 数据模型定义 |
| HTTP 客户端 | `httpx` | 异步 HTTP 请求（图片下载） |

## 注意事项

1. **首次使用**：必须先运行 `python login_tool.py` 进行扫码登录
2. **Cookies 保存路径**：优先使用 `/tmp/cookies.json`，Windows 下为 `%TEMP%/cookies.json`
3. **无头模式**：服务器环境建议开启 `--headless` 模式
4. **代理支持**：中国大陆以外地区可能需要设置 `XHS_PROXY`
5. **帖子详情**：加载评论较多时可能需要较长时间，建议合理设置 `max_comments`

## Cookies 文件路径优先级

1. `/tmp/cookies.json`（Linux/Mac 临时目录）
2. `COOKIES_PATH` 环境变量指定路径
3. `%TEMP%/cookies.json`（Windows 临时目录）
4. `MCP/cookies.json`（当前目录）
