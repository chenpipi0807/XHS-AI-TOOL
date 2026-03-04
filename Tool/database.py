"""
SQLite 数据库初始化与查询封装
所有表结构定义、初始化逻辑和常用查询封装
"""
import sqlite3
import json
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# 数据库路径（从环境变量读取，默认 ./data/xhs_tool.db）
def get_db_path() -> str:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
    db_path = os.getenv("DB_PATH", "./data/xhs_tool.db")
    # 转为绝对路径
    if not os.path.isabs(db_path):
        db_path = str(Path(__file__).parent / db_path)
    return db_path


# ── DDL ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    topic       TEXT,
    style_guide TEXT,
    status      TEXT    NOT NULL DEFAULT 'active',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    title        TEXT    NOT NULL,
    content      TEXT,
    tags         TEXT,           -- JSON 数组
    image_paths  TEXT,           -- JSON 数组，本地图片路径
    cover_image  TEXT,
    status       TEXT    NOT NULL DEFAULT 'draft',  -- draft/scheduled/published/failed
    xhs_post_id  TEXT,
    xhs_url      TEXT,
    xsec_token   TEXT,
    scheduled_at DATETIME,
    published_at DATETIME,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS post_analytics (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id            INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    snapshot_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    hours_after_publish REAL,
    likes_count        INTEGER DEFAULT 0,
    comments_count     INTEGER DEFAULT 0,
    favorites_count    INTEGER DEFAULT 0,
    shares_count       INTEGER DEFAULT 0,
    views_count        INTEGER DEFAULT 0,
    heat_score         REAL    DEFAULT 0,
    top_comments       TEXT,   -- JSON，热门评论摘要
    comment_insights   TEXT    -- Kimi 分析的评论洞察
);

CREATE TABLE IF NOT EXISTS images (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    post_id         INTEGER REFERENCES posts(id) ON DELETE SET NULL,
    model           TEXT    NOT NULL,   -- gemini/seedream
    model_version   TEXT,
    prompt          TEXT    NOT NULL,
    ref_image_paths TEXT,               -- JSON 数组
    output_paths    TEXT,               -- JSON 数组
    aspect_ratio    TEXT,
    image_size      TEXT,
    local_dir       TEXT,
    status          TEXT    NOT NULL DEFAULT 'generated',  -- generated/used/deleted
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    task_type    TEXT    NOT NULL,  -- publish/comment/analyze/generate/reply
    title        TEXT    NOT NULL,
    description  TEXT,
    status       TEXT    NOT NULL DEFAULT 'pending',  -- pending/running/done/failed/cancelled
    todo_list    TEXT,              -- JSON 数组
    result       TEXT,              -- JSON
    error_msg    TEXT,
    triggered_by TEXT    NOT NULL DEFAULT 'user',  -- user/scheduler/agent
    scheduled_at DATETIME,
    started_at   DATETIME,
    finished_at  DATETIME,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER REFERENCES agent_tasks(id) ON DELETE CASCADE,
    role        TEXT    NOT NULL,   -- user/assistant/tool
    content     TEXT    NOT NULL,
    tool_name   TEXT,
    tool_result TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    job_type    TEXT    NOT NULL,   -- publish/analyze/comment_check
    cron_expr   TEXT,
    config      TEXT,               -- JSON
    is_enabled  INTEGER NOT NULL DEFAULT 1,
    last_run_at DATETIME,
    next_run_at DATETIME,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mcp_tools (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name     TEXT    NOT NULL UNIQUE,
    display_name  TEXT,
    description   TEXT,
    server        TEXT    NOT NULL,   -- xhs/sqlite
    input_schema  TEXT,               -- JSON schema
    is_enabled    INTEGER NOT NULL DEFAULT 1,
    call_count    INTEGER NOT NULL DEFAULT 0,
    last_called_at DATETIME,
    last_result   TEXT
);

CREATE TABLE IF NOT EXISTS mcp_call_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name      TEXT    NOT NULL,
    server         TEXT    NOT NULL DEFAULT 'xhs',
    arguments_json TEXT,               -- JSON
    result_text    TEXT,
    status         TEXT    NOT NULL DEFAULT 'success',  -- success/error/pending
    duration_ms    INTEGER,
    task_id        INTEGER REFERENCES agent_tasks(id) ON DELETE SET NULL,
    called_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS todos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    task_id     INTEGER REFERENCES agent_tasks(id) ON DELETE SET NULL,
    title       TEXT    NOT NULL,
    description TEXT,
    status      TEXT    NOT NULL DEFAULT 'pending',  -- pending/in_progress/done/cancelled
    priority    INTEGER NOT NULL DEFAULT 0,           -- 0=normal, 1=high, 2=urgent
    due_date    TEXT,
    tags        TEXT,   -- 逗号分隔
    created_by  TEXT    NOT NULL DEFAULT 'user',      -- user/kimi_agent
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 触发器：自动更新 posts.updated_at
CREATE TRIGGER IF NOT EXISTS posts_updated_at
    AFTER UPDATE ON posts
BEGIN
    UPDATE posts SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- 触发器：自动更新 todos.updated_at
CREATE TRIGGER IF NOT EXISTS todos_updated_at
    AFTER UPDATE ON todos
BEGIN
    UPDATE todos SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
"""

# 预置 MCP 工具数据
MCP_TOOLS_SEED = [
    # 小红书 MCP 工具
    ("xhs_check_login",       "检查登录状态",   "检查小红书账号是否已登录",                     "xhs"),
    ("xhs_get_qrcode",        "获取登录二维码", "获取小红书登录二维码",                          "xhs"),
    ("xhs_publish_content",   "发布图文",       "发布图文笔记到小红书",                          "xhs"),
    ("xhs_publish_video",     "发布视频",       "发布视频笔记到小红书",                          "xhs"),
    ("xhs_list_feeds",        "获取推荐流",     "获取小红书首页推荐内容",                        "xhs"),
    ("xhs_search",            "搜索内容",       "搜索小红书内容",                                "xhs"),
    ("xhs_get_feed_detail",   "获取帖子详情",   "获取指定帖子的详细信息（含点赞/评论数）",       "xhs"),
    ("xhs_post_comment",      "发表评论",       "对指定帖子发表评论",                            "xhs"),
    ("xhs_reply_comment",     "回复评论",       "回复指定评论",                                  "xhs"),
    ("xhs_like_feed",         "点赞帖子",       "对帖子执行点赞操作",                            "xhs"),
    ("xhs_favorite_feed",     "收藏帖子",       "对帖子执行收藏操作",                            "xhs"),
    ("xhs_get_user_profile",  "获取用户主页",   "获取指定用户的主页信息",                        "xhs"),
    ("xhs_get_my_profile",    "获取我的主页",   "获取当前登录账号的主页信息（粉丝数/获赞数）",   "xhs"),
    # SQLite MCP 工具
    ("db_get_project_context","获取项目上下文", "获取项目历史帖子和数据快照",                   "sqlite"),
    ("db_save_task",          "保存任务",       "保存 Agent 任务和 Todo 列表",                  "sqlite"),
    ("db_update_task_status", "更新任务状态",   "更新任务执行状态和结果",                       "sqlite"),
    ("db_save_post",          "保存帖子草稿",   "保存帖子草稿到数据库",                         "sqlite"),
    ("db_update_post_analytics","更新数据快照", "更新帖子点赞/评论/收藏数据快照",              "sqlite"),
    ("db_get_content_insights","获取内容洞察",  "获取项目评论洞察摘要",                         "sqlite"),
    ("db_query",              "通用查询",       "执行只读 SQL 查询（仅允许 SELECT）",            "sqlite"),
]


# ── 连接管理 ───────────────────────────────────────────────────────────────

@contextmanager
def get_db():
    """获取数据库连接（上下文管理器）"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db_conn() -> sqlite3.Connection:
    """获取数据库连接（非上下文，需手动关闭）"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── 初始化 ─────────────────────────────────────────────────────────────────

def init_db():
    """初始化数据库（建表 + 预置数据）"""
    db_path = get_db_path()
    # 确保目录存在
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    with get_db() as conn:
        conn.executescript(SCHEMA_SQL)
        logger.info(f"数据库初始化完成: {db_path}")

        # 预置 MCP 工具数据
        existing = {row[0] for row in conn.execute("SELECT tool_name FROM mcp_tools")}
        for tool_name, display_name, description, server in MCP_TOOLS_SEED:
            if tool_name not in existing:
                conn.execute(
                    "INSERT INTO mcp_tools (tool_name, display_name, description, server) VALUES (?,?,?,?)",
                    (tool_name, display_name, description, server)
                )
        logger.info(f"MCP 工具预置完成，共 {len(MCP_TOOLS_SEED)} 个")

        # 预置默认项目
        default_proj = conn.execute(
            "SELECT id FROM projects WHERE name = ?", ("英雄联盟AI漫画",)
        ).fetchone()
        if not default_proj:
            conn.execute(
                """INSERT INTO projects (name, description, topic, style_guide)
                   VALUES (?,?,?,?)""",
                (
                    "英雄联盟AI漫画",
                    "以英雄联盟角色为主题的 AI 漫画创作项目",
                    "AI漫画",
                    "风格：卡通漫画风，色彩鲜艳，人物表情夸张有趣。每期围绕一个英雄的日常趣事展开，结合时下热点。文案要有梗，接地气，适合18-30岁游戏玩家。",
                )
            )
            logger.info("创建默认项目：英雄联盟AI漫画")


# ── 通用查询封装 ────────────────────────────────────────────────────────────

def query_one(sql: str, params: tuple = ()) -> Optional[Dict]:
    """查询单行"""
    with get_db() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def query_all(sql: str, params: tuple = ()) -> List[Dict]:
    """查询多行"""
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


def execute(sql: str, params: tuple = ()) -> int:
    """执行 INSERT/UPDATE/DELETE，返回 lastrowid 或 rowcount"""
    with get_db() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid or cur.rowcount


# ── Projects ───────────────────────────────────────────────────────────────

def get_all_projects() -> List[Dict]:
    return query_all(
        "SELECT * FROM projects ORDER BY created_at DESC"
    )


def get_project(project_id: int) -> Optional[Dict]:
    return query_one("SELECT * FROM projects WHERE id = ?", (project_id,))


def create_project(name: str, description: str = "", topic: str = "", style_guide: str = "") -> int:
    return execute(
        "INSERT INTO projects (name, description, topic, style_guide) VALUES (?,?,?,?)",
        (name, description, topic, style_guide)
    )


# ── Posts ──────────────────────────────────────────────────────────────────

def get_posts(project_id: Optional[int] = None, status: Optional[str] = None,
               limit: int = 50, offset: int = 0) -> List[Dict]:
    conditions = []
    params: List[Any] = []
    if project_id is not None:
        conditions.append("p.project_id = ?")
        params.append(project_id)
    if status:
        conditions.append("p.status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT p.*, proj.name AS project_name
        FROM posts p
        LEFT JOIN projects proj ON p.project_id = proj.id
        {where}
        ORDER BY p.created_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    rows = query_all(sql, tuple(params))
    # 反序列化 JSON 字段
    for row in rows:
        row["tags"] = json.loads(row["tags"] or "[]")
        row["image_paths"] = json.loads(row["image_paths"] or "[]")
    return rows


def get_post(post_id: int) -> Optional[Dict]:
    row = query_one("SELECT * FROM posts WHERE id = ?", (post_id,))
    if row:
        row["tags"] = json.loads(row["tags"] or "[]")
        row["image_paths"] = json.loads(row["image_paths"] or "[]")
    return row


def create_post(project_id: int, title: str, content: str = "",
                tags: List[str] = None, image_paths: List[str] = None,
                cover_image: str = "") -> int:
    return execute(
        """INSERT INTO posts (project_id, title, content, tags, image_paths, cover_image)
           VALUES (?,?,?,?,?,?)""",
        (project_id, title, content,
         json.dumps(tags or [], ensure_ascii=False),
         json.dumps(image_paths or [], ensure_ascii=False),
         cover_image)
    )


def update_post(post_id: int, **kwargs) -> None:
    """动态更新帖子字段"""
    json_fields = {"tags", "image_paths"}
    sets = []
    params = []
    for k, v in kwargs.items():
        if k in json_fields and isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        sets.append(f"{k} = ?")
        params.append(v)
    if not sets:
        return
    params.append(post_id)
    execute(f"UPDATE posts SET {', '.join(sets)} WHERE id = ?", tuple(params))


def mark_post_published(post_id: int, xhs_post_id: str, xhs_url: str, xsec_token: str = "") -> None:
    execute(
        """UPDATE posts SET status='published', xhs_post_id=?, xhs_url=?, xsec_token=?,
           published_at=CURRENT_TIMESTAMP WHERE id=?""",
        (xhs_post_id, xhs_url, xsec_token, post_id)
    )


# ── Post Analytics ─────────────────────────────────────────────────────────

def save_post_analytics(post_id: int, likes: int = 0, comments: int = 0,
                        favorites: int = 0, shares: int = 0, views: int = 0,
                        top_comments: List[Dict] = None,
                        comment_insights: str = "") -> int:
    """保存数据快照，自动计算热度评分"""
    heat_score = likes * 1 + comments * 3 + favorites * 2 + shares * 5

    # 计算发布后多少小时
    post = query_one("SELECT published_at FROM posts WHERE id=?", (post_id,))
    hours_after = None
    if post and post.get("published_at"):
        try:
            pub_time = datetime.fromisoformat(str(post["published_at"]))
            hours_after = (datetime.utcnow() - pub_time).total_seconds() / 3600
        except Exception:
            pass

    return execute(
        """INSERT INTO post_analytics
           (post_id, hours_after_publish, likes_count, comments_count,
            favorites_count, shares_count, views_count, heat_score,
            top_comments, comment_insights)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (post_id, hours_after, likes, comments, favorites, shares, views,
         heat_score,
         json.dumps(top_comments or [], ensure_ascii=False),
         comment_insights)
    )


def get_post_analytics(post_id: int) -> List[Dict]:
    rows = query_all(
        "SELECT * FROM post_analytics WHERE post_id=? ORDER BY snapshot_at",
        (post_id,)
    )
    for row in rows:
        row["top_comments"] = json.loads(row["top_comments"] or "[]")
    return rows


def get_latest_analytics(post_id: int) -> Optional[Dict]:
    row = query_one(
        "SELECT * FROM post_analytics WHERE post_id=? ORDER BY snapshot_at DESC LIMIT 1",
        (post_id,)
    )
    if row:
        row["top_comments"] = json.loads(row["top_comments"] or "[]")
    return row


# ── Images ─────────────────────────────────────────────────────────────────

def save_image(project_id: int, model: str, prompt: str,
               output_paths: List[str], model_version: str = "",
               ref_image_paths: List[str] = None, aspect_ratio: str = "",
               image_size: str = "", local_dir: str = "",
               post_id: Optional[int] = None) -> int:
    return execute(
        """INSERT INTO images
           (project_id, post_id, model, model_version, prompt,
            ref_image_paths, output_paths, aspect_ratio, image_size, local_dir)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (project_id, post_id, model, model_version, prompt,
         json.dumps(ref_image_paths or [], ensure_ascii=False),
         json.dumps(output_paths, ensure_ascii=False),
         aspect_ratio, image_size, local_dir)
    )


def get_images(project_id: Optional[int] = None, post_id: Optional[int] = None,
               status: str = None, limit: int = 100) -> List[Dict]:
    conditions = []
    params: List[Any] = []
    if project_id is not None:
        conditions.append("project_id = ?")
        params.append(project_id)
    if post_id is not None:
        conditions.append("post_id = ?")
        params.append(post_id)
    if status:
        conditions.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = query_all(f"SELECT * FROM images {where} ORDER BY created_at DESC LIMIT ?",
                     tuple(params) + (limit,))
    for row in rows:
        row["output_paths"] = json.loads(row["output_paths"] or "[]")
        row["ref_image_paths"] = json.loads(row["ref_image_paths"] or "[]")
    return rows


# ── Agent Tasks ────────────────────────────────────────────────────────────

def create_agent_task(project_id: int, task_type: str, title: str,
                      description: str = "", todo_list: List[Dict] = None,
                      triggered_by: str = "user",
                      scheduled_at: Optional[str] = None) -> int:
    return execute(
        """INSERT INTO agent_tasks
           (project_id, task_type, title, description, todo_list, triggered_by, scheduled_at)
           VALUES (?,?,?,?,?,?,?)""",
        (project_id, task_type, title, description,
         json.dumps(todo_list or [], ensure_ascii=False),
         triggered_by, scheduled_at)
    )


def update_task_status(task_id: int, status: str,
                       result: Any = None, error_msg: str = None) -> None:
    now = datetime.utcnow().isoformat()
    if status == "running":
        execute("UPDATE agent_tasks SET status=?, started_at=? WHERE id=?",
                (status, now, task_id))
    elif status in ("done", "failed", "cancelled"):
        execute(
            """UPDATE agent_tasks SET status=?, finished_at=?,
               result=?, error_msg=? WHERE id=?""",
            (status, now,
             json.dumps(result, ensure_ascii=False) if result else None,
             error_msg, task_id)
        )
    else:
        execute("UPDATE agent_tasks SET status=? WHERE id=?", (status, task_id))


def update_task_todo(task_id: int, todo_list: List[Dict]) -> None:
    execute("UPDATE agent_tasks SET todo_list=? WHERE id=?",
            (json.dumps(todo_list, ensure_ascii=False), task_id))


def get_tasks(project_id: Optional[int] = None, status: Optional[str] = None,
              limit: int = 50) -> List[Dict]:
    conditions = []
    params: List[Any] = []
    if project_id is not None:
        conditions.append("t.project_id = ?")
        params.append(project_id)
    if status:
        conditions.append("t.status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT t.*, p.name AS project_name
        FROM agent_tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        {where}
        ORDER BY t.created_at DESC LIMIT ?
    """
    rows = query_all(sql, tuple(params) + (limit,))
    for row in rows:
        row["todo_list"] = json.loads(row["todo_list"] or "[]")
        row["result"] = json.loads(row["result"]) if row.get("result") else None
    return rows


def get_task(task_id: int) -> Optional[Dict]:
    row = query_one(
        """SELECT t.*, p.name AS project_name
           FROM agent_tasks t
           LEFT JOIN projects p ON t.project_id = p.id
           WHERE t.id=?""",
        (task_id,)
    )
    if row:
        row["todo_list"] = json.loads(row["todo_list"] or "[]")
        row["result"] = json.loads(row["result"]) if row.get("result") else None
    return row


# ── Agent Messages ─────────────────────────────────────────────────────────

def save_message(task_id: Optional[int], role: str, content: str,
                 tool_name: str = None, tool_result: str = None) -> int:
    return execute(
        """INSERT INTO agent_messages (task_id, role, content, tool_name, tool_result)
           VALUES (?,?,?,?,?)""",
        (task_id, role, content, tool_name, tool_result)
    )


def get_messages(task_id: int) -> List[Dict]:
    return query_all(
        "SELECT * FROM agent_messages WHERE task_id=? ORDER BY created_at",
        (task_id,)
    )


# ── MCP Tools ──────────────────────────────────────────────────────────────

def get_mcp_tools() -> List[Dict]:
    return query_all("SELECT * FROM mcp_tools ORDER BY server, tool_name")


def toggle_mcp_tool(tool_name: str, enabled: bool) -> None:
    execute("UPDATE mcp_tools SET is_enabled=? WHERE tool_name=?",
            (1 if enabled else 0, tool_name))


def record_mcp_call(tool_name: str, server: str = "xhs",
                    arguments: Dict = None, result: str = "",
                    status: str = "success", duration_ms: int = None,
                    task_id: int = None, result_summary: str = "") -> None:
    """记录 MCP 工具调用（同时更新统计 + 写入调用日志）"""
    summary = result_summary or (str(result)[:200] if result else "")
    # 更新工具统计
    execute(
        """UPDATE mcp_tools SET call_count=call_count+1,
           last_called_at=CURRENT_TIMESTAMP, last_result=?
           WHERE tool_name=?""",
        (summary, tool_name)
    )
    # 写入调用日志
    execute(
        """INSERT INTO mcp_call_log
           (tool_name, server, arguments_json, result_text, status, duration_ms, task_id)
           VALUES (?,?,?,?,?,?,?)""",
        (
            tool_name, server,
            json.dumps(arguments or {}, ensure_ascii=False),
            str(result)[:2000] if result else "",
            status, duration_ms, task_id,
        )
    )


def get_mcp_call_log(limit: int = 50, tool_name: str = None,
                     status: str = None) -> List[Dict]:
    """获取 MCP 调用日志"""
    conditions = []
    params: List[Any] = []
    if tool_name:
        conditions.append("tool_name = ?")
        params.append(tool_name)
    if status:
        conditions.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return query_all(
        f"SELECT * FROM mcp_call_log {where} ORDER BY called_at DESC LIMIT ?",
        tuple(params) + (limit,)
    )


def clear_mcp_call_log() -> None:
    """清除所有 MCP 调用日志"""
    execute("DELETE FROM mcp_call_log")


# ── Scheduled Jobs ─────────────────────────────────────────────────────────

def get_scheduled_jobs() -> List[Dict]:
    rows = query_all("SELECT * FROM scheduled_jobs ORDER BY created_at DESC")
    for row in rows:
        row["config"] = json.loads(row["config"] or "{}")
    return rows


def create_scheduled_job(name: str, job_type: str, cron_expr: str,
                          config: Dict = None) -> int:
    return execute(
        "INSERT INTO scheduled_jobs (name, job_type, cron_expr, config) VALUES (?,?,?,?)",
        (name, job_type, cron_expr, json.dumps(config or {}, ensure_ascii=False))
    )


def toggle_scheduled_job(job_id: int, enabled: bool) -> None:
    execute("UPDATE scheduled_jobs SET is_enabled=? WHERE id=?",
            (1 if enabled else 0, job_id))


# ── 数据分析查询 ────────────────────────────────────────────────────────────

def get_account_trend(days: int = 30) -> List[Dict]:
    """获取账号趋势数据（按天聚合）"""
    return query_all(
        """SELECT
               DATE(snapshot_at) AS date,
               SUM(likes_count) AS total_likes,
               SUM(comments_count) AS total_comments,
               SUM(favorites_count) AS total_favorites,
               AVG(heat_score) AS avg_heat
           FROM post_analytics
           WHERE snapshot_at >= DATE('now', ? || ' days')
           GROUP BY DATE(snapshot_at)
           ORDER BY date""",
        (f"-{days}",)
    )


def get_project_context(project_id: int, limit: int = 20) -> Dict:
    """获取项目上下文：近期帖子 + 最新数据快照（供 Kimi 使用）"""
    project = get_project(project_id)
    posts = get_posts(project_id=project_id, limit=limit)
    # 为每篇帖子附上最新数据
    for post in posts:
        analytics = get_latest_analytics(post["id"])
        post["latest_analytics"] = analytics
    return {
        "project": project,
        "recent_posts": posts,
        "total_posts": query_one(
            "SELECT COUNT(*) AS cnt FROM posts WHERE project_id=?", (project_id,)
        )["cnt"] if project else 0,
    }


def get_content_insights(project_id: int) -> Dict:
    """获取项目评论洞察摘要"""
    rows = query_all(
        """SELECT pa.comment_insights, pa.heat_score, p.title
           FROM post_analytics pa
           JOIN posts p ON pa.post_id = p.id
           WHERE p.project_id = ? AND pa.comment_insights IS NOT NULL
           ORDER BY pa.snapshot_at DESC LIMIT 10""",
        (project_id,)
    )
    return {
        "insights": rows,
        "high_heat_posts": query_all(
            """SELECT p.id, p.title, MAX(pa.heat_score) AS max_heat
               FROM posts p JOIN post_analytics pa ON pa.post_id = p.id
               WHERE p.project_id = ?
               GROUP BY p.id ORDER BY max_heat DESC LIMIT 5""",
            (project_id,)
        )
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("✅ 数据库初始化完成")
    print(f"   路径: {get_db_path()}")
    print(f"   项目数: {len(get_all_projects())}")
    print(f"   MCP工具数: {len(get_mcp_tools())}")
