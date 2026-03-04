"""
Tool/agent/task_planner.py
任务规划 & Todo 管理

功能：
  - 将用户意图解析为结构化 Todo 列表
  - 管理任务状态（pending/running/done/failed）
  - 与 SQLite agent_tasks 表双向同步
  - 支持任务依赖和优先级
  - 提供任务执行进度推流
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ── 路径初始化 ─────────────────────────────────────────────────────────────
_tool_dir = Path(__file__).resolve().parent.parent
if str(_tool_dir) not in sys.path:
    sys.path.insert(0, str(_tool_dir))

import database as db

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
# Todo 数据类
# ════════════════════════════════════════════════════════════════════════════

class Todo:
    """单个 Todo 项"""

    STATUS_PENDING  = "pending"
    STATUS_RUNNING  = "running"
    STATUS_DONE     = "done"
    STATUS_FAILED   = "failed"
    STATUS_SKIPPED  = "skipped"

    PRIORITY_HIGH   = "high"
    PRIORITY_MEDIUM = "medium"
    PRIORITY_LOW    = "low"

    def __init__(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        todo_id: Optional[str] = None,
        depends_on: Optional[list[str]] = None,
    ):
        import uuid
        self.id = todo_id or str(uuid.uuid4())[:8]
        self.title = title
        self.description = description
        self.priority = priority
        self.status = self.STATUS_PENDING
        self.depends_on = depends_on or []
        self.result: Optional[str] = None
        self.error: Optional[str] = None
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "status": self.status,
            "depends_on": self.depends_on,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Todo":
        todo = cls(
            title=data["title"],
            description=data.get("description", ""),
            priority=data.get("priority", "medium"),
            todo_id=data.get("id"),
            depends_on=data.get("depends_on", []),
        )
        todo.status = data.get("status", "pending")
        todo.result = data.get("result")
        todo.error = data.get("error")
        return todo

    def mark_running(self):
        self.status = self.STATUS_RUNNING
        self.started_at = datetime.now()

    def mark_done(self, result: str = ""):
        self.status = self.STATUS_DONE
        self.result = result
        self.finished_at = datetime.now()

    def mark_failed(self, error: str = ""):
        self.status = self.STATUS_FAILED
        self.error = error
        self.finished_at = datetime.now()

    def mark_skipped(self):
        self.status = self.STATUS_SKIPPED
        self.finished_at = datetime.now()

    @property
    def is_done(self) -> bool:
        return self.status in (self.STATUS_DONE, self.STATUS_SKIPPED)

    @property
    def is_failed(self) -> bool:
        return self.status == self.STATUS_FAILED

    @property
    def is_pending(self) -> bool:
        return self.status == self.STATUS_PENDING

    @property
    def is_running(self) -> bool:
        return self.status == self.STATUS_RUNNING


# ════════════════════════════════════════════════════════════════════════════
# 任务规划器
# ════════════════════════════════════════════════════════════════════════════

class TaskPlanner:
    """
    任务规划器：将高层任务拆解为 Todo 列表并执行。

    使用方式：
        planner = TaskPlanner(project_id=1, task_id=42)
        planner.add_todo("分析账号数据")
        planner.add_todo("生成内容建议")
        for event in planner.execute_with_agent(agent):
            yield event
    """

    def __init__(
        self,
        project_id: Optional[int] = None,
        task_id: Optional[int] = None,
        task_title: str = "AI 任务",
    ):
        self.project_id = project_id
        self.task_id = task_id
        self.task_title = task_title
        self.todos: list[Todo] = []
        self._db_task_id: Optional[int] = None

    # ── Todo 管理 ─────────────────────────────────────────────────────────

    def add_todo(
        self,
        title: str,
        description: str = "",
        priority: str = Todo.PRIORITY_MEDIUM,
        depends_on: Optional[list[str]] = None,
    ) -> Todo:
        """添加一个 Todo 项"""
        todo = Todo(
            title=title,
            description=description,
            priority=priority,
            depends_on=depends_on,
        )
        self.todos.append(todo)
        return todo

    def get_todo(self, todo_id: str) -> Optional[Todo]:
        for t in self.todos:
            if t.id == todo_id:
                return t
        return None

    def get_next_executable(self) -> Optional[Todo]:
        """获取下一个可执行的 Todo（依赖已满足且状态 pending）"""
        done_ids = {t.id for t in self.todos if t.is_done}
        for todo in self.todos:
            if not todo.is_pending:
                continue
            # 检查依赖
            if all(dep in done_ids for dep in todo.depends_on):
                return todo
        return None

    def load_from_json(self, json_str: str):
        """从 Kimi 返回的 JSON 字符串加载 Todo 列表"""
        try:
            data = json.loads(json_str)
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("todos", data.get("tasks", []))
            else:
                return

            self.todos = []
            for item in items:
                if isinstance(item, str):
                    self.todos.append(Todo(title=item))
                elif isinstance(item, dict):
                    self.todos.append(Todo.from_dict(item))
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"解析 Todo JSON 失败: {e}")

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "task_id": self._db_task_id,
            "title": self.task_title,
            "todos": [t.to_dict() for t in self.todos],
            "progress": self.progress,
        }

    @property
    def progress(self) -> dict:
        total = len(self.todos)
        done = sum(1 for t in self.todos if t.is_done)
        failed = sum(1 for t in self.todos if t.is_failed)
        running = sum(1 for t in self.todos if t.is_running)
        return {
            "total": total,
            "done": done,
            "failed": failed,
            "running": running,
            "pending": total - done - failed - running,
            "percent": int(done / total * 100) if total > 0 else 0,
        }

    # ── 数据库同步 ────────────────────────────────────────────────────────

    def ensure_db_task(self, task_type: str = "plan") -> int:
        """确保数据库中存在对应任务记录，返回 task_id"""
        if self.task_id:
            self._db_task_id = self.task_id
            return self.task_id

        if self._db_task_id:
            return self._db_task_id

        task_id = db.create_agent_task(
            project_id=self.project_id,
            task_type=task_type,
            title=self.task_title,
            description=json.dumps([t.title for t in self.todos], ensure_ascii=False),
            triggered_by="agent",
        )
        self._db_task_id = task_id
        self.task_id = task_id
        return task_id

    def sync_to_db(self):
        """将当前 todos 状态同步到数据库（存储在 agent_tasks.result 字段）"""
        if not self._db_task_id:
            return
        try:
            progress = self.progress
            result_json = json.dumps({
                "todos": [t.to_dict() for t in self.todos],
                "progress": progress,
            }, ensure_ascii=False, default=str)
            db.update_task_status(
                task_id=self._db_task_id,
                status="running" if progress["running"] > 0 else (
                    "done" if progress["pending"] == 0 and progress["failed"] == 0 else "running"
                ),
                result=result_json,
            )
        except Exception as e:
            logger.warning(f"同步任务状态到 DB 失败: {e}")

    # ── 解析用户意图 ──────────────────────────────────────────────────────

    @staticmethod
    def parse_intent(message: str) -> dict:
        """
        快速解析用户意图（无需 LLM），返回建议的任务类型和描述。
        用于在 Agent 回复前预判任务类型。
        """
        msg = message.lower()

        intent_map = [
            (["分析", "数据", "表现", "趋势"],            "analyze",  "数据分析"),
            (["写", "创作", "生成", "文案", "帖子"],       "create",   "内容创作"),
            (["发布", "publish", "发帖"],                   "publish",  "发布内容"),
            (["计划", "规划", "排期", "策略"],              "plan",     "内容规划"),
            (["评论", "回复", "互动"],                      "engage",   "互动运营"),
            (["图片", "生图", "图像"],                      "image",    "图片生成"),
            (["搜索", "找", "查", "search"],                "search",   "内容搜索"),
        ]

        for keywords, task_type, label in intent_map:
            if any(k in msg for k in keywords):
                return {"type": task_type, "label": label}

        return {"type": "chat", "label": "对话"}

    # ── 执行控制 ──────────────────────────────────────────────────────────

    def execute_with_agent(self, agent, stop_on_failure: bool = False):
        """
        使用 KimiAgent 依次执行 Todo 列表。
        yield 事件字典供 SSE 推送。

        事件类型：
          todo_update  — Todo 状态变更
          progress     — 总体进度
          text         — Agent 文本输出
          tool_start   — 工具调用开始
          tool_result  — 工具调用结果
          error        — 错误
          done         — 全部完成
        """
        self.ensure_db_task()

        yield {
            "type": "progress",
            "todos": [t.to_dict() for t in self.todos],
            **self.progress,
        }

        for todo in self.todos:
            if not todo.is_pending:
                continue

            # 检查依赖
            done_ids = {t.id for t in self.todos if t.is_done}
            if not all(dep in done_ids for dep in todo.depends_on):
                todo.mark_skipped()
                yield {"type": "todo_update", "todo": todo.to_dict()}
                continue

            # 开始执行
            todo.mark_running()
            yield {"type": "todo_update", "todo": todo.to_dict()}

            # 构建执行提示词
            prompt = f"【当前任务】{todo.title}"
            if todo.description:
                prompt += f"\n{todo.description}"

            # 执行 Agent 对话
            accumulated_text = ""
            try:
                for chunk in agent.chat_stream(message=prompt):
                    chunk_type = chunk.get("type")
                    if chunk_type == "text":
                        accumulated_text = chunk.get("content", "")
                        yield chunk
                    elif chunk_type in ("tool_start", "tool_result"):
                        yield chunk
                    elif chunk_type == "error":
                        yield chunk
                    elif chunk_type == "done":
                        accumulated_text = chunk.get("content", "")

                todo.mark_done(result=accumulated_text[:500] if accumulated_text else "完成")
            except Exception as e:
                todo.mark_failed(error=str(e))
                yield {"type": "error", "content": f"任务 [{todo.title}] 执行失败: {e}"}
                if stop_on_failure:
                    break

            yield {"type": "todo_update", "todo": todo.to_dict()}
            yield {"type": "progress", **self.progress}
            self.sync_to_db()

        # 最终状态
        all_done = all(t.is_done or t.is_failed or t.status == Todo.STATUS_SKIPPED for t in self.todos)
        if all_done:
            try:
                db.update_task_status(
                    task_id=self._db_task_id,
                    status="done",
                    result=json.dumps({"todos": [t.to_dict() for t in self.todos]}, ensure_ascii=False, default=str),
                )
            except Exception:
                pass

        yield {
            "type": "done",
            "todos": [t.to_dict() for t in self.todos],
            **self.progress,
        }


# ════════════════════════════════════════════════════════════════════════════
# 预设任务模板
# ════════════════════════════════════════════════════════════════════════════

TASK_TEMPLATES: dict[str, list[dict]] = {
    "weekly_plan": [
        {"title": "查询本周已发内容", "description": "使用 query_posts 获取近 7 天发布的笔记", "priority": "high"},
        {"title": "分析热度最高帖子", "description": "对比数据，找出表现最好的 3 篇", "priority": "high"},
        {"title": "查看草稿库存", "description": "获取所有草稿状态的帖子", "priority": "medium"},
        {"title": "规划本周发布计划", "description": "基于数据分析，制定 3-5 篇发布计划", "priority": "high"},
        {"title": "生成发布排期表", "description": "输出 Markdown 格式的排期表", "priority": "medium"},
    ],
    "content_analysis": [
        {"title": "获取账号整体数据", "description": "查询近 30 天趋势数据", "priority": "high"},
        {"title": "分析各帖互动率", "description": "计算平均互动率，找出高互动内容特征", "priority": "high"},
        {"title": "识别最佳发布时间", "description": "分析互动数据，找出最活跃时间段", "priority": "medium"},
        {"title": "生成分析报告", "description": "输出结构化分析报告 + 改进建议", "priority": "high"},
    ],
    "post_creation": [
        {"title": "分析目标主题趋势", "description": "搜索相关内容，了解热门方向", "priority": "high"},
        {"title": "生成标题选项", "description": "创作 5 个不同风格的标题，选择最优", "priority": "high"},
        {"title": "撰写正文内容", "description": "基于最佳标题，创作完整正文", "priority": "high"},
        {"title": "生成配套标签", "description": "精准+热门标签组合，5-8 个", "priority": "medium"},
        {"title": "保存草稿", "description": "将内容保存到数据库草稿", "priority": "medium"},
    ],
    "engagement_boost": [
        {"title": "识别待互动帖子", "description": "找出近 48h 内有新评论的帖子", "priority": "high"},
        {"title": "分析评论情感", "description": "对评论进行分类（正面/疑问/负面）", "priority": "medium"},
        {"title": "生成回复建议", "description": "为每类评论生成回复模板", "priority": "high"},
        {"title": "执行互动操作", "description": "通过 XHS MCP 回复和点赞", "priority": "medium"},
    ],
}


def create_plan_from_template(
    template_name: str,
    project_id: Optional[int] = None,
    task_title: Optional[str] = None,
) -> TaskPlanner:
    """从模板创建任务计划"""
    todos_config = TASK_TEMPLATES.get(template_name, [])
    title = task_title or {
        "weekly_plan": "每周内容规划",
        "content_analysis": "内容分析报告",
        "post_creation": "新帖子创作",
        "engagement_boost": "互动提升任务",
    }.get(template_name, "AI 任务")

    planner = TaskPlanner(project_id=project_id, task_title=title)
    for cfg in todos_config:
        planner.add_todo(**cfg)
    return planner


def create_custom_plan(
    todos: list[str | dict],
    project_id: Optional[int] = None,
    task_title: str = "自定义任务",
) -> TaskPlanner:
    """从自定义列表创建任务计划"""
    planner = TaskPlanner(project_id=project_id, task_title=task_title)
    for item in todos:
        if isinstance(item, str):
            planner.add_todo(title=item)
        elif isinstance(item, dict):
            planner.add_todo(**item)
    return planner
