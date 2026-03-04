"""
Tool/scheduler/task_scheduler.py
APScheduler 定时任务封装

功能：
  - 基于 APScheduler BackgroundScheduler（非阻塞，与 Flask 共进程运行）
  - 从数据库 scheduled_jobs 表加载/同步任务
  - 内置任务类型：
      snapshot_all  — 批量采集所有项目帖子快照
      analyze_all   — 批量分析所有项目内容
      weekly_report — 每周生成内容报告
  - 支持 cron / interval 两种触发器
  - 任务执行结果写回 scheduled_jobs 表
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

# ── 路径初始化 ─────────────────────────────────────────────────────────────
_tool_dir = Path(__file__).resolve().parent.parent
if str(_tool_dir) not in sys.path:
    sys.path.insert(0, str(_tool_dir))

from dotenv import load_dotenv
load_dotenv(_tool_dir / ".env", override=False)

import database as db

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
# APScheduler 初始化
# ════════════════════════════════════════════════════════════════════════════

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
    _APScheduler_available = True
except ImportError:
    _APScheduler_available = False
    logger.warning("APScheduler 未安装，定时任务功能将不可用。安装：pip install apscheduler")


# ════════════════════════════════════════════════════════════════════════════
# 内置任务函数
# ════════════════════════════════════════════════════════════════════════════

def _job_snapshot_all():
    """定时快照：对所有项目的已发布帖子采集数据"""
    from agent.content_analyzer import quick_snapshot_all
    try:
        projects = db.get_all_projects()
        total = 0
        for proj in projects:
            count = quick_snapshot_all(proj["id"], snapshot_type="auto")
            total += count
        logger.info(f"[Scheduler] snapshot_all 完成，共 {total} 个快照")
        return {"success": True, "total_snapshots": total}
    except Exception as e:
        logger.error(f"[Scheduler] snapshot_all 失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def _job_analyze_all():
    """定时分析：生成所有项目的内容分析报告"""
    from agent.content_analyzer import analyze_project_content
    try:
        projects = db.get_all_projects()
        results = []
        for proj in projects:
            report = analyze_project_content(proj["id"])
            # 保存洞察到 DB
            if report.get("insights"):
                for insight in report["insights"]:
                    try:
                        db.execute(
                            "INSERT INTO agent_messages (task_id, role, content, created_at) "
                            "VALUES (?, ?, ?, ?)",
                            (None, "system", f"[自动分析] {insight}", datetime.now().isoformat())
                        )
                    except Exception:
                        pass
            results.append({"project_id": proj["id"], "post_count": report.get("post_count", 0)})
        logger.info(f"[Scheduler] analyze_all 完成，分析了 {len(results)} 个项目")
        return {"success": True, "projects": results}
    except Exception as e:
        logger.error(f"[Scheduler] analyze_all 失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def _job_weekly_report():
    """每周报告：生成综合内容报告"""
    from agent.content_analyzer import generate_full_report
    try:
        projects = db.get_all_projects()
        for proj in projects:
            report = generate_full_report(proj["id"])
            summary = report.get("summary", "")
            logger.info(f"[Scheduler] 项目 {proj['name']} 周报: {summary}")
        return {"success": True}
    except Exception as e:
        logger.error(f"[Scheduler] weekly_report 失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


# 任务类型注册表
JOB_REGISTRY: dict[str, Callable] = {
    "snapshot_all":  _job_snapshot_all,
    "analyze_all":   _job_analyze_all,
    "weekly_report": _job_weekly_report,
}


# ════════════════════════════════════════════════════════════════════════════
# XHSScheduler 主类
# ════════════════════════════════════════════════════════════════════════════

class XHSScheduler:
    """
    APScheduler 封装，与 Flask 应用共进程运行。

    使用方式（在 app.py 启动时）：
        scheduler = XHSScheduler()
        scheduler.start()
        # Flask 关闭时：
        scheduler.stop()
    """

    _instance: Optional["XHSScheduler"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._scheduler: Optional[Any] = None
        self._started = False

        if not _APScheduler_available:
            logger.warning("APScheduler 不可用，跳过初始化")
            return

        self._scheduler = BackgroundScheduler(
            timezone="Asia/Shanghai",
            job_defaults={
                "coalesce": True,          # 合并多次错过的执行
                "max_instances": 1,        # 同一任务只允许单实例
                "misfire_grace_time": 300, # 允许 5 分钟的延迟容忍
            },
        )

        # 注册事件监听
        self._scheduler.add_listener(
            self._on_job_executed,
            EVENT_JOB_EXECUTED | EVENT_JOB_ERROR
        )

    # ── 生命周期 ──────────────────────────────────────────────────────────

    def start(self):
        """启动调度器，从 DB 加载任务"""
        if not self._scheduler or self._started:
            return

        try:
            self._load_jobs_from_db()
            self._add_builtin_jobs()
            self._scheduler.start()
            self._started = True
            logger.info("[Scheduler] 调度器已启动")
        except Exception as e:
            logger.error(f"[Scheduler] 启动失败: {e}", exc_info=True)

    def stop(self):
        """停止调度器"""
        if self._scheduler and self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
            logger.info("[Scheduler] 调度器已停止")

    @property
    def is_running(self) -> bool:
        return self._started and self._scheduler is not None

    # ── 内置任务 ──────────────────────────────────────────────────────────

    def _add_builtin_jobs(self):
        """添加系统内置定时任务"""
        if not self._scheduler:
            return

        # 每小时采集快照（仅在工作时间）
        self._add_cron_job(
            job_id="builtin_snapshot_hourly",
            func=_job_snapshot_all,
            cron_expr="0 8-22 * * *",  # 每天 8-22 点，每小时整点
            name="每小时数据快照",
            replace_existing=True,
        )

        # 每天早 9 点分析
        self._add_cron_job(
            job_id="builtin_daily_analyze",
            func=_job_analyze_all,
            cron_expr="0 9 * * *",
            name="每日内容分析",
            replace_existing=True,
        )

        # 每周一早 8 点周报
        self._add_cron_job(
            job_id="builtin_weekly_report",
            func=_job_weekly_report,
            cron_expr="0 8 * * MON",
            name="每周内容报告",
            replace_existing=True,
        )

    # ── 从 DB 加载任务 ────────────────────────────────────────────────────

    def _load_jobs_from_db(self):
        """从数据库 scheduled_jobs 表加载用户自定义任务"""
        try:
            jobs = db.get_scheduled_jobs()
            for job in jobs:
                if not job.get("enabled"):
                    continue
                job_type = job.get("job_type", "")
                func = JOB_REGISTRY.get(job_type)
                if not func:
                    logger.warning(f"[Scheduler] 未知任务类型: {job_type}，跳过")
                    continue

                cron_expr = job.get("cron_expr", "0 9 * * *")
                self._add_cron_job(
                    job_id=f"db_{job['id']}",
                    func=func,
                    cron_expr=cron_expr,
                    name=job.get("name", job_type),
                    replace_existing=True,
                )
        except Exception as e:
            logger.warning(f"[Scheduler] 从 DB 加载任务失败: {e}")

    # ── 任务操作 API ──────────────────────────────────────────────────────

    def _add_cron_job(
        self,
        job_id: str,
        func: Callable,
        cron_expr: str,
        name: str = "",
        replace_existing: bool = True,
    ) -> bool:
        """添加 cron 触发器任务"""
        if not self._scheduler:
            return False
        try:
            parts = cron_expr.split()
            if len(parts) == 5:
                minute, hour, day, month, day_of_week = parts
            else:
                minute, hour, day, month, day_of_week = "0", "9", "*", "*", "*"

            trigger = CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
                timezone="Asia/Shanghai",
            )
            self._scheduler.add_job(
                func,
                trigger=trigger,
                id=job_id,
                name=name or job_id,
                replace_existing=replace_existing,
            )
            logger.info(f"[Scheduler] 已添加任务: {name} ({cron_expr})")
            return True
        except Exception as e:
            logger.error(f"[Scheduler] 添加任务失败 [{job_id}]: {e}")
            return False

    def add_interval_job(
        self,
        job_id: str,
        func: Callable,
        seconds: int = 3600,
        name: str = "",
    ) -> bool:
        """添加 interval 触发器任务"""
        if not self._scheduler:
            return False
        try:
            trigger = IntervalTrigger(seconds=seconds, timezone="Asia/Shanghai")
            self._scheduler.add_job(
                func,
                trigger=trigger,
                id=job_id,
                name=name or job_id,
                replace_existing=True,
            )
            return True
        except Exception as e:
            logger.error(f"[Scheduler] 添加 interval 任务失败: {e}")
            return False

    def remove_job(self, job_id: str) -> bool:
        """移除任务"""
        if not self._scheduler:
            return False
        try:
            self._scheduler.remove_job(job_id)
            return True
        except Exception:
            return False

    def pause_job(self, job_id: str) -> bool:
        """暂停任务"""
        if not self._scheduler:
            return False
        try:
            self._scheduler.pause_job(job_id)
            return True
        except Exception:
            return False

    def resume_job(self, job_id: str) -> bool:
        """恢复任务"""
        if not self._scheduler:
            return False
        try:
            self._scheduler.resume_job(job_id)
            return True
        except Exception:
            return False

    def run_now(self, job_type: str) -> dict:
        """立即手动触发一个任务（同步执行，返回结果）"""
        func = JOB_REGISTRY.get(job_type)
        if not func:
            return {"success": False, "error": f"未知任务类型: {job_type}"}
        try:
            result = func()
            return result or {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_jobs(self) -> list[dict]:
        """获取所有任务列表"""
        if not self._scheduler:
            return []
        jobs = []
        for job in self._scheduler.get_jobs():
            next_run = job.next_run_time
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": next_run.isoformat() if next_run else None,
                "status": "paused" if job.next_run_time is None else "active",
            })
        return jobs

    # ── 事件回调 ──────────────────────────────────────────────────────────

    def _on_job_executed(self, event):
        """任务执行完毕的回调"""
        job_id = event.job_id
        if hasattr(event, "exception") and event.exception:
            logger.error(f"[Scheduler] 任务 {job_id} 执行失败: {event.exception}")
        else:
            logger.info(f"[Scheduler] 任务 {job_id} 执行成功")

        # 如果是 DB 任务，更新最后执行时间
        if job_id.startswith("db_"):
            try:
                db_id = int(job_id[3:])
                db.execute(
                    "UPDATE scheduled_jobs SET last_run_at=? WHERE id=?",
                    (datetime.now().isoformat(), db_id),
                )
            except Exception:
                pass

    # ── 单例模式 ──────────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> "XHSScheduler":
        """获取全局单例"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance


# ── 全局便捷函数 ───────────────────────────────────────────────────────────

def get_scheduler() -> XHSScheduler:
    """获取全局调度器实例"""
    return XHSScheduler.get_instance()


def start_scheduler():
    """启动全局调度器（在 Flask app 启动时调用）"""
    sched = get_scheduler()
    sched.start()
    return sched


def stop_scheduler():
    """停止全局调度器（在 Flask app 关闭时调用）"""
    sched = XHSScheduler._instance
    if sched:
        sched.stop()
