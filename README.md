# XHS-AI-Tool 🤖📱

> 基于 AI Agent + MCP 协议的小红书自动化工具，支持 AI 辅助内容创作、图像生成与自动发布图文笔记。

---

## ⚠️ 重要声明

1. **MCP 模块致谢**：小红书 MCP 代码借鉴自 [xpzouying/xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)，原项目为 Go 实现，本项目用 Python 重写并扩展。感谢原作者的开创性工作！

2. **验证状态**：目前仅验证了 **Kimi 模型**（`kimi-k2.5`）与**图文发布**相关功能，其他模型和功能仍在测试中，欢迎社区反馈。

3. **作者小红书主页**：[🔗 欢迎关注交流](https://xhslink.com/m/RbHvbKyXNw)，技术问题可在主页私信或在 GitHub Issues 中讨论。

4. **维护说明**：作者工作较忙，不定期更新。欢迎社区大佬们提 PR 共同维护！🙏

---

## 项目概览

```
XHS-AI-Tool/
├── Tool/                   # AI Agent 主程序（CLI 模式）
│   ├── cli.py              # 主入口，交互式命令行
│   ├── database.py         # SQLite 数据库操作
│   ├── .env.example        # 环境变量模板（复制为 .env 并填入 key）
│   ├── agent/              # AI Agent 模块
│   │   ├── kimi_agent.py   # Kimi 对话 Agent
│   │   ├── content_analyzer.py  # 内容分析
│   │   └── task_planner.py # 任务规划
│   ├── image/              # 图像生成模块
│   │   └── gemini_image.py # Gemini 图像生成
│   ├── mcp_sqlite/         # SQLite MCP 服务器
│   └── scheduler/          # 定时任务调度
│
└── MCP/                    # 小红书 MCP 服务器（浏览器自动化）
    ├── main.py             # MCP 服务器入口
    ├── login_tool.py       # 扫码登录工具
    ├── service.py          # 业务服务层
    └── xiaohongshu/        # 小红书各功能模块
```

---

## 🚀 部署与使用

### 环境要求

- Python >= 3.10
- 操作系统：Windows / macOS / Linux

---

### 第一步：克隆项目

```bash
git clone https://github.com/chenpipi0807/XHS-AI-TOOL.git
cd XHS-AI-TOOL
```

---

### 第二步：部署 MCP 小红书服务

```bash
cd MCP
pip install -r requirements.txt
playwright install chromium
```

**首次登录**（扫码登录小红书账号）：

```bash
python login_tool.py
```

程序会弹出浏览器显示二维码，使用小红书 App 扫码后自动保存 Cookie。

**启动 MCP 服务**：

```bash
python main.py
# 默认监听 http://localhost:18060/mcp
```

---

### 第三步：部署 AI Agent 工具

```bash
cd Tool
pip install -r requirements.txt
```

**配置 API Key**（复制模板并填入你自己的 key）：

```bash
cp .env .env.backup   # 可先备份
# 编辑 Tool/.env，填入以下内容：
```

```ini
# Kimi API（官方地址）
KIMI_API_KEY=your_kimi_api_key_here
KIMI_API_BASE_URL=https://api.moonshot.cn/v1
KIMI_MODEL=kimi-k2.5

# Gemini API（Google AI Studio）
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_API_BASE_URL=https://generativelanguage.googleapis.com
GEMINI_IMAGE_MODEL=gemini-3.1-flash-image-preview
```

> 📌 **如何获取 API Key**
> - Kimi：前往 [Moonshot 开放平台](https://platform.moonshot.cn/) 注册获取
> - Gemini：前往 [Google AI Studio](https://aistudio.google.com/) 获取免费 API Key

**启动交互式 CLI**：

```bash
python cli.py
```

---

### 在 AI 工具中配置 MCP（可选）

如果你使用 Cursor / Claude Desktop 等支持 MCP 的 AI 工具，可以将小红书 MCP 注册进去：

**Cursor / VSCode（`.cursor/mcp.json`）**：

```json
{
  "mcpServers": {
    "xiaohongshu": {
      "url": "http://localhost:18060/mcp"
    }
  }
}
```

**Claude Desktop（`claude_desktop_config.json`）**：

```json
{
  "mcpServers": {
    "xiaohongshu": {
      "command": "python",
      "args": ["/path/to/XHS-AI-TOOL/MCP/main.py"],
      "env": {
        "HEADLESS": "false"
      }
    }
  }
}
```

---

## 🔧 主要功能

| 功能模块 | 状态 | 说明 |
|----------|------|------|
| Kimi AI 对话 Agent | ✅ 已验证 | 基于 `kimi-k2.5` 模型的多轮对话 |
| Gemini 图像生成 | ✅ 已验证 | 使用 `gemini-3.1-flash-image-preview` 生成图片 |
| 小红书图文发布 | ✅ 已验证 | 通过 Playwright 自动填写并发布图文笔记 |
| 小红书视频发布 | 🧪 未完整验证 | 功能已实现，欢迎测试反馈 |
| 内容搜索 | 🧪 未完整验证 | 支持关键词搜索与多维度筛选 |
| 定时发布 | 🧪 未完整验证 | APScheduler 定时任务调度 |

---

## 📦 技术栈

| 组件 | 库 |
|------|----|
| AI 对话 | Moonshot Kimi (`kimi-k2.5`，OpenAI 兼容格式) |
| 图像生成 | Google Gemini (`google-genai` SDK) |
| 浏览器自动化 | `playwright-python` |
| MCP 协议 | 官方 `mcp` Python SDK |
| 数据存储 | SQLite + `python-dotenv` |
| 定时任务 | `APScheduler` |

---

## ⚙️ 常见问题

**Q: 登录后 Cookie 失效怎么办？**

```bash
cd MCP && python login_tool.py
```

重新扫码登录即可，Cookie 会自动覆盖。

**Q: 图文发布失败？**

- 确认 MCP 服务已启动（`python main.py`）
- 确认小红书 Cookie 有效（先运行 `login_tool.py`）
- 图片路径需为绝对路径或正确的相对路径

**Q: Gemini API 报错？**

- 检查 `GEMINI_API_KEY` 是否正确填写
- 免费额度可能有限，确认账号配额

---

## 🤝 贡献

欢迎提交 PR 和 Issue！项目还在早期阶段，有很多功能可以完善：

- [ ] 更多模型支持（DeepSeek、Qwen 等）
- [ ] 视频发布完整测试
- [ ] Web UI 界面
- [ ] 批量任务管理

---

## 📄 License

MIT License

---

> 本项目仅供学习研究使用，请遵守小红书平台使用条款，勿用于商业刷量等违规行为。
