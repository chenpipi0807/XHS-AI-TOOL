"""
Tool/agent/content_analyzer.py
内容热度分析器 — 多时间维度快照 + 趋势计算

功能：
  - 定时采集帖子数据快照（1h / 6h / 24h / 72h）
  - 计算热度分数（heat_score）
  - 计算增量变化率（delta）
  - 识别爆款趋势（飙升/下降/稳定）
  - 生成内容洞察报告
  - 存储到 post_analytics 表
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

# ── 路径初始化 ─────────────────────────────────────────────────────────────
_tool_dir = Path(__file__).resolve().parent.parent
if str(_tool_dir) not in sys.path:
    sys.path.insert(0, str(_tool_dir))

import database as db

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
# 热度分数计算
# ════════════════════════════════════════════════════════════════════════════

# 权重配置（与 database.py 中的 save_post_analytics 一致）
HEAT_WEIGHTS = {
    "likes":     0.4,
    "comments":  0.3,
    "favorites": 0.2,
    "shares":    0.1,
}

# 热度等级
HEAT_LEVELS = [
    (500, "🔥 爆款",   "viral"),
    (200, "⚡ 热门",   "hot"),
    (80,  "📈 上升",   "rising"),
    (30,  "💬 活跃",   "active"),
    (0,   "😴 普通",   "normal"),
]


def calc_heat_score(
    likes: int = 0,
    comments: int = 0,
    favorites: int = 0,
    shares: int = 0,
) -> float:
    """
    综合热度分 = likes*0.4 + comments*0.3 + favorites*0.2 + shares*0.1
    归一化到 0-1000 范围
    """
    raw = (
        likes     * HEAT_WEIGHTS["likes"]    +
        comments  * HEAT_WEIGHTS["comments"] +
        favorites * HEAT_WEIGHTS["favorites"]+
        shares    * HEAT_WEIGHTS["shares"]
    )
    # Sigmoid 归一化到 0-1000
    import math
    normalized = 1000 / (1 + math.exp(-raw / 100 + 3))
    return round(normalized, 2)


def get_heat_level(score: float) -> tuple[str, str]:
    """返回 (label, key)"""
    for threshold, label, key in HEAT_LEVELS:
        if score >= threshold:
            return label, key
    return "😴 普通", "normal"


def calc_engagement_rate(
    likes: int,
    comments: int,
    favorites: int,
    shares: int,
    follower_count: int = 1000,
) -> float:
    """
    互动率 = (likes + comments*2 + favorites + shares) / follower_count * 100
    comments 权重加倍（主动互动更有价值）
    """
    if follower_count <= 0:
        return 0.0
    total = likes + comments * 2 + favorites + shares
    return round(total / follower_count * 100, 3)


# ════════════════════════════════════════════════════════════════════════════
# 快照采集
# ════════════════════════════════════════════════════════════════════════════

def take_snapshot(
    post_id: int,
    likes: int = 0,
    comments: int = 0,
    favorites: int = 0,
    shares: int = 0,
    views: int = 0,
    follower_count: int = 1000,
    snapshot_type: str = "manual",
) -> dict:
    """
    采集帖子数据快照并存储。
    
    snapshot_type: 'manual' | '1h' | '6h' | '24h' | '72h' | 'auto'
    
    返回快照记录 dict
    """
    heat_score = calc_heat_score(likes, comments, favorites, shares)
    engagement_rate = calc_engagement_rate(likes, comments, favorites, shares, follower_count)

    try:
        record_id = db.save_post_analytics(
            post_id=post_id,
            likes_count=likes,
            comments_count=comments,
            favorites_count=favorites,
            shares_count=shares,
            views_count=views,
            heat_score=heat_score,
            snapshot_type=snapshot_type,
        )
        logger.info(f"[ContentAnalyzer] 快照已保存: post_id={post_id}, heat={heat_score:.1f}, type={snapshot_type}")
    except Exception as e:
        logger.error(f"[ContentAnalyzer] 快照保存失败: {e}")
        record_id = None

    return {
        "id": record_id,
        "post_id": post_id,
        "snapshot_type": snapshot_type,
        "likes": likes,
        "comments": comments,
        "favorites": favorites,
        "shares": shares,
        "views": views,
        "heat_score": heat_score,
        "engagement_rate": engagement_rate,
        "heat_level": get_heat_level(heat_score)[0],
        "recorded_at": datetime.now().isoformat(),
    }


def take_batch_snapshots(
    posts_data: list[dict],
    snapshot_type: str = "auto",
) -> list[dict]:
    """
    批量采集多篇帖子的快照。
    posts_data 每项：{post_id, likes, comments, favorites, shares, views}
    """
    results = []
    for item in posts_data:
        post_id = item.get("post_id") or item.get("id")
        if not post_id:
            continue
        snapshot = take_snapshot(
            post_id=post_id,
            likes=item.get("likes_count", 0),
            comments=item.get("comments_count", 0),
            favorites=item.get("favorites_count", 0),
            shares=item.get("shares_count", 0),
            views=item.get("views_count", 0),
            snapshot_type=snapshot_type,
        )
        results.append(snapshot)
    return results


# ════════════════════════════════════════════════════════════════════════════
# 趋势分析
# ════════════════════════════════════════════════════════════════════════════

def analyze_post_trend(post_id: int, hours: int = 72) -> dict:
    """
    分析单篇帖子的数据趋势。
    
    返回：
      - 时间序列数据
      - 增速（每小时增量）
      - 趋势判断（飙升/稳定/下降）
      - 峰值时间
      - 最新 vs 最早的增量百分比
    """
    snapshots = db.get_post_analytics(post_id)
    if not snapshots:
        return {"post_id": post_id, "status": "no_data", "snapshots": []}

    # 按时间排序
    snapshots = sorted(snapshots, key=lambda x: x.get("recorded_at", ""))

    # 计算增量
    deltas = []
    for i in range(1, len(snapshots)):
        prev = snapshots[i - 1]
        curr = snapshots[i]

        try:
            prev_time = datetime.fromisoformat(prev["recorded_at"])
            curr_time = datetime.fromisoformat(curr["recorded_at"])
            hours_diff = max((curr_time - prev_time).total_seconds() / 3600, 0.01)
        except Exception:
            hours_diff = 1.0

        delta = {
            "from": prev["recorded_at"],
            "to":   curr["recorded_at"],
            "hours_diff": round(hours_diff, 2),
            "likes_delta":     curr.get("likes_count", 0)     - prev.get("likes_count", 0),
            "comments_delta":  curr.get("comments_count", 0)  - prev.get("comments_count", 0),
            "favorites_delta": curr.get("favorites_count", 0) - prev.get("favorites_count", 0),
            "heat_delta":      round(
                (curr.get("heat_score", 0) or 0) - (prev.get("heat_score", 0) or 0), 2
            ),
        }
        # 每小时增量
        delta["likes_per_hour"]    = round(delta["likes_delta"]    / hours_diff, 2)
        delta["comments_per_hour"] = round(delta["comments_delta"] / hours_diff, 2)
        deltas.append(delta)

    # 趋势判断
    trend = "stable"
    trend_label = "📊 稳定"
    if deltas:
        last_heat_delta = deltas[-1].get("heat_delta", 0)
        recent_likes_per_hour = deltas[-1].get("likes_per_hour", 0)

        if last_heat_delta > 50 or recent_likes_per_hour > 10:
            trend = "rising_fast"
            trend_label = "🚀 急速上升"
        elif last_heat_delta > 20 or recent_likes_per_hour > 3:
            trend = "rising"
            trend_label = "📈 上升"
        elif last_heat_delta < -50:
            trend = "falling_fast"
            trend_label = "📉 急速下降"
        elif last_heat_delta < -10:
            trend = "falling"
            trend_label = "⬇️ 下降"

    # 最新数据
    latest = snapshots[-1] if snapshots else {}
    earliest = snapshots[0] if snapshots else {}

    # 增长率
    growth_pct: dict[str, float] = {}
    for field in ("likes_count", "comments_count", "favorites_count"):
        early_val = earliest.get(field, 0) or 0
        late_val  = latest.get(field, 0) or 0
        if early_val > 0:
            growth_pct[field] = round((late_val - early_val) / early_val * 100, 1)
        else:
            growth_pct[field] = 0.0

    return {
        "post_id": post_id,
        "status": "ok",
        "snapshot_count": len(snapshots),
        "trend": trend,
        "trend_label": trend_label,
        "latest": latest,
        "growth_pct": growth_pct,
        "deltas": deltas,
        "heat_level": get_heat_level(latest.get("heat_score", 0) or 0),
    }


def analyze_project_content(project_id: int) -> dict:
    """
    分析项目所有帖子的整体表现。

    返回：
      - 帖子列表（含热度分）
      - 排行榜（Top 3）
      - 平均互动率
      - 热度分布
      - 趋势洞察
    """
    try:
        posts = db.get_posts(project_id=project_id, limit=100)
    except Exception as e:
        logger.error(f"获取帖子失败: {e}")
        return {"error": str(e)}

    if not posts:
        return {
            "project_id": project_id,
            "post_count": 0,
            "insights": ["暂无帖子数据"],
        }

    # 为每篇帖子附上最新分析数据
    enriched = []
    for post in posts:
        latest = db.get_latest_analytics(post["id"])
        heat_score = latest.get("heat_score", 0) if latest else 0
        heat_label, heat_key = get_heat_level(heat_score)
        enriched.append({
            **post,
            "heat_score": heat_score,
            "heat_label": heat_label,
            "heat_key": heat_key,
            "latest_analytics": latest,
        })

    # 按热度排序
    enriched.sort(key=lambda x: x["heat_score"], reverse=True)

    # Top 3
    top_posts = enriched[:3]

    # 热度分布
    heat_dist = {"viral": 0, "hot": 0, "rising": 0, "active": 0, "normal": 0}
    for p in enriched:
        heat_dist[p["heat_key"]] = heat_dist.get(p["heat_key"], 0) + 1

    # 平均数据
    total_likes     = sum(p.get("likes_count", 0) or 0 for p in posts)
    total_comments  = sum(p.get("comments_count", 0) or 0 for p in posts)
    total_favorites = sum(p.get("favorites_count", 0) or 0 for p in posts)
    count = len(posts)
    avg_likes     = round(total_likes / count, 1) if count else 0
    avg_comments  = round(total_comments / count, 1) if count else 0
    avg_favorites = round(total_favorites / count, 1) if count else 0
    avg_heat      = round(sum(p["heat_score"] for p in enriched) / count, 1) if count else 0

    # 生成洞察
    insights = _generate_insights(enriched, heat_dist, avg_likes, avg_comments)

    return {
        "project_id": project_id,
        "post_count": count,
        "top_posts": top_posts,
        "heat_distribution": heat_dist,
        "averages": {
            "likes": avg_likes,
            "comments": avg_comments,
            "favorites": avg_favorites,
            "heat_score": avg_heat,
        },
        "insights": insights,
        "all_posts": enriched,
    }


def _generate_insights(
    posts: list[dict],
    heat_dist: dict[str, int],
    avg_likes: float,
    avg_comments: float,
) -> list[str]:
    """生成数据洞察文字"""
    insights = []
    total = len(posts)

    if total == 0:
        return ["暂无帖子数据，快去创作你的第一篇笔记吧！"]

    # 爆款率
    viral_rate = (heat_dist.get("viral", 0) + heat_dist.get("hot", 0)) / total * 100
    if viral_rate > 30:
        insights.append(f"🔥 热门内容占比 {viral_rate:.0f}%，账号整体表现优秀！")
    elif viral_rate > 10:
        insights.append(f"📈 热门内容占比 {viral_rate:.0f}%，有一定爆款潜力。")
    else:
        insights.append(f"💡 热门内容较少（{viral_rate:.0f}%），建议分析 Top 帖子的成功要素。")

    # 评论率分析
    if avg_likes > 0:
        comment_like_ratio = avg_comments / avg_likes
        if comment_like_ratio > 0.15:
            insights.append(f"💬 评论/点赞比 {comment_like_ratio:.2f}，内容互动性强！")
        elif comment_like_ratio < 0.05:
            insights.append(f"💬 评论/点赞比 {comment_like_ratio:.2f} 偏低，可在结尾增加互动引导。")

    # Top 帖子分析
    if posts:
        top = posts[0]
        if top.get("heat_score", 0) > 200:
            insights.append(
                f"⭐ 最高热度帖子「{top.get('title', '')}」热度 {top['heat_score']:.0f}，"
                f"可以分析其成功要素并复制。"
            )

    # 内容节奏建议
    published_count = sum(1 for p in posts if p.get("status") == "published")
    draft_count = sum(1 for p in posts if p.get("status") == "draft")
    if draft_count > published_count * 2:
        insights.append(f"📝 草稿 {draft_count} 篇，已发布 {published_count} 篇，建议加快发布节奏。")

    return insights


# ════════════════════════════════════════════════════════════════════════════
# 账号趋势分析
# ════════════════════════════════════════════════════════════════════════════

def analyze_account_growth(days: int = 30) -> dict:
    """
    分析账号整体增长趋势。
    从 post_analytics 聚合每日数据。
    """
    try:
        trend_data = db.get_account_trend(days=days)
    except Exception as e:
        return {"error": str(e)}

    if not trend_data:
        return {
            "days": days,
            "status": "no_data",
            "message": "暂无趋势数据，需要先采集帖子数据快照。",
        }

    # 计算增长率
    if len(trend_data) >= 2:
        first = trend_data[0]
        last  = trend_data[-1]
        growth = {}
        for field in ("total_likes", "total_comments", "total_favorites"):
            fv = first.get(field, 0) or 0
            lv = last.get(field, 0) or 0
            growth[field] = {
                "from": fv,
                "to": lv,
                "delta": lv - fv,
                "pct": round((lv - fv) / max(fv, 1) * 100, 1),
            }
    else:
        growth = {}

    # 找出最活跃的一天
    best_day = max(trend_data, key=lambda x: x.get("total_likes", 0), default=None)

    return {
        "days": days,
        "status": "ok",
        "data_points": len(trend_data),
        "growth": growth,
        "best_day": best_day,
        "trend_series": trend_data,
    }


# ════════════════════════════════════════════════════════════════════════════
# 最佳发布时间分析
# ════════════════════════════════════════════════════════════════════════════

BEST_TIME_SLOTS = {
    "morning": {"range": (7, 9),   "label": "早间 07-09", "reason": "通勤刷机时段"},
    "lunch":   {"range": (11, 13), "label": "午间 11-13", "reason": "午休浏览时段"},
    "evening": {"range": (19, 22), "label": "晚间 19-22", "reason": "黄金互动时段"},
    "night":   {"range": (22, 24), "label": "夜间 22-24", "reason": "睡前刷机时段"},
}


def recommend_publish_time(project_id: Optional[int] = None) -> dict:
    """
    基于历史数据推荐最佳发布时间。
    如果没有历史数据，返回通用建议。
    """
    # 通用建议（无历史数据时）
    default_times = [
        {"slot": "evening", **BEST_TIME_SLOTS["evening"],
         "score": 100, "reason": "黄金时段，流量最大"},
        {"slot": "lunch",   **BEST_TIME_SLOTS["lunch"],
         "score": 80,  "reason": "午休场景，适合种草类内容"},
        {"slot": "morning", **BEST_TIME_SLOTS["morning"],
         "score": 60,  "reason": "通勤时段，适合快速消费内容"},
    ]

    return {
        "source": "default",
        "recommendations": default_times,
        "tips": [
            "发布后 30 分钟内回复所有评论，提升算法权重",
            "使用热门话题标签提高曝光",
            "配合完播率高的封面图提升分发量",
        ],
    }


# ════════════════════════════════════════════════════════════════════════════
# 一键分析报告
# ════════════════════════════════════════════════════════════════════════════

def generate_full_report(project_id: int) -> dict:
    """
    生成完整的项目分析报告（供 Agent 调用）。
    包含：内容分析 + 账号增长 + 发布时间建议
    """
    content = analyze_project_content(project_id)
    growth  = analyze_account_growth(days=30)
    timing  = recommend_publish_time(project_id)

    return {
        "generated_at": datetime.now().isoformat(),
        "project_id": project_id,
        "content_analysis": content,
        "account_growth": growth,
        "publish_timing": timing,
        "summary": _build_summary(content, growth),
    }


def _build_summary(content: dict, growth: dict) -> str:
    """生成简短文字总结"""
    parts = []

    post_count = content.get("post_count", 0)
    if post_count:
        parts.append(f"共 {post_count} 篇内容")

    top = content.get("top_posts", [])
    if top:
        parts.append(f"最热帖子「{top[0].get('title', '')}」热度 {top[0].get('heat_score', 0):.0f}")

    avg = content.get("averages", {})
    if avg:
        parts.append(f"平均点赞 {avg.get('likes', 0):.0f}、评论 {avg.get('comments', 0):.0f}")

    insights = content.get("insights", [])
    if insights:
        parts.append(insights[0])

    return "；".join(parts) if parts else "暂无足够数据生成总结"


# ── 便捷函数 ───────────────────────────────────────────────────────────────

def quick_snapshot_all(project_id: int, snapshot_type: str = "auto") -> int:
    """对项目所有已发布帖子采集快照，返回快照数量"""
    posts = db.get_posts(project_id=project_id, status="published", limit=200)
    if not posts:
        return 0

    snapped = take_batch_snapshots(posts, snapshot_type=snapshot_type)
    logger.info(f"[ContentAnalyzer] 项目 {project_id} 共采集 {len(snapped)} 个快照")
    return len(snapped)
