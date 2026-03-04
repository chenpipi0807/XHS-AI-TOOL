"""
小红书用户资料模块
对应 Go 版本: xiaohongshu/user_profile.go
URL: https://www.xiaohongshu.com/user/profile/{userID}?xsec_token={token}&xsec_source=pc_note
数据来源: window.__INITIAL_STATE__.user.userPageData 和 window.__INITIAL_STATE__.user.notes
"""
import asyncio
import logging

from browser.browser import get_browser_page
from xiaohongshu.types import (
    UserProfileResponse, UserBasicInfo, UserNote
)

logger = logging.getLogger(__name__)

XHS_USER_PROFILE_URL = "https://www.xiaohongshu.com/user/profile/{user_id}"


async def _extract_user_profile_data(page) -> UserProfileResponse:
    """
    从 window.__INITIAL_STATE__ 提取用户资料数据
    对应 Go: extractUserProfileData()
    """
    profile_data = await page.evaluate("""
        () => {
            try {
                const state = window.__INITIAL_STATE__;
                if (!state || !state.user) return null;

                // 注意: __INITIAL_STATE__ 是 Vue 响应式对象，有循环引用
                // 必须手动序列化为纯对象，不能直接 return 嵌套对象

                const userPageData = state.user.userPageData || {};
                const basicInfo = userPageData.basicInfo || {};
                const interactions = userPageData.interactions || [];

                // 解析互动数据（关注/粉丝/获赞收藏）
                let follows = 0, fans = 0, interaction = 0;
                if (Array.isArray(interactions)) {
                    for (let i = 0; i < interactions.length; i++) {
                        const item = interactions[i];
                        if (!item) continue;
                        const count = parseInt(item.count) || 0;
                        if (item.type === 'follows') follows = count;
                        else if (item.type === 'fans') fans = count;
                        else if (item.type === 'interaction') interaction = count;
                    }
                }

                // 解析笔记列表（手动展开，避免响应式对象循环引用）
                const notesData = state.user.notes || [];
                const notes = [];
                if (Array.isArray(notesData)) {
                    for (let i = 0; i < notesData.length; i++) {
                        try {
                            const item = notesData[i];
                            if (!item) continue;
                            const cover = item.cover || {};
                            const interactInfo = item.interactInfo || {};
                            notes.push({
                                noteId: String(item.noteId || ''),
                                title: String(item.displayTitle || item.title || ''),
                                coverUrl: String(cover.url || ''),
                                likedCount: String(interactInfo.likedCount || '0'),
                                xsecToken: String(item.xsecToken || ''),
                            });
                        } catch(e2) {}
                    }
                }

                // 手动序列化 basicInfo，避免循环引用
                return {
                    basicInfo: {
                        userId: String(basicInfo.userId || basicInfo.user_id || ''),
                        nickname: String(basicInfo.nickname || ''),
                        avatar: String(basicInfo.images || basicInfo.imageb || basicInfo.avatar || ''),
                        desc: String(basicInfo.desc || ''),
                        gender: Number(basicInfo.gender || 0),
                        location: String(basicInfo.location || ''),
                        follows: follows,
                        fans: fans,
                        interaction: interaction,
                        ipLocation: String(basicInfo.ipLocation || ''),
                    },
                    notes: notes,
                };
            } catch(e) {
                return { error: String(e) };
            }
        }
    """)

    if not profile_data:
        return UserProfileResponse()

    # 处理 JS 错误返回
    if isinstance(profile_data, dict) and profile_data.get("error"):
        logger.warning(f"JS 提取用户资料失败: {profile_data['error']}")
        return UserProfileResponse()

    try:
        bi = profile_data.get("basicInfo", {}) or {}
        basic_info = UserBasicInfo(
            user_id=str(bi.get("userId", "") or ""),
            nickname=str(bi.get("nickname", "") or ""),
            avatar=str(bi.get("avatar", "") or ""),
            desc=str(bi.get("desc", "") or ""),
            gender=int(bi.get("gender", 0) or 0),
            location=str(bi.get("location", "") or ""),
            follows=int(bi.get("follows", 0) or 0),
            fans=int(bi.get("fans", 0) or 0),
            interaction=int(bi.get("interaction", 0) or 0),
            ip_location=str(bi.get("ipLocation", "") or ""),
        )

        notes = []
        for n in profile_data.get("notes", []) or []:
            if not isinstance(n, dict):
                continue
            note = UserNote(
                note_id=str(n.get("noteId", "") or ""),
                title=str(n.get("title", "") or ""),
                cover_url=str(n.get("coverUrl", "") or ""),
                liked_count=str(n.get("likedCount", "0") or "0"),
                xsec_token=str(n.get("xsecToken", "") or ""),
            )
            if note.note_id:
                notes.append(note)

        return UserProfileResponse(basic_info=basic_info, notes=notes)

    except Exception as e:
        logger.error(f"解析用户资料数据失败: {e}")
        return UserProfileResponse()


async def get_user_profile(
    user_id: str,
    xsec_token: str = "",
) -> UserProfileResponse:
    """
    获取用户资料页
    对应 Go: GetUserProfile()

    :param user_id: 用户 ID
    :param xsec_token: 安全 token
    :return: UserProfileResponse
    """
    try:
        page = await get_browser_page()

        # 构建 URL
        url = XHS_USER_PROFILE_URL.format(user_id=user_id)
        if xsec_token:
            url += f"?xsec_token={xsec_token}&xsec_source=pc_note"

        logger.info(f"获取用户资料: {user_id}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # 提取用户资料数据
        profile = await _extract_user_profile_data(page)

        # 如果没有获取到数据，等待并重试
        if not profile.basic_info.user_id:
            logger.warning("首次提取用户资料为空，等待后重试")
            await asyncio.sleep(3)
            profile = await _extract_user_profile_data(page)

        logger.info(
            f"用户资料获取成功: {profile.basic_info.nickname}, "
            f"粉丝: {profile.basic_info.fans}, "
            f"笔记数: {len(profile.notes)}"
        )
        return profile

    except Exception as e:
        logger.error(f"获取用户资料失败 {user_id}: {e}")
        raise


async def get_my_profile() -> UserProfileResponse:
    """
    获取当前登录用户的资料
    对应 Go: GetMyProfile()
    """
    try:
        page = await get_browser_page()

        # 导航到个人中心
        await page.goto(
            "https://www.xiaohongshu.com/user/profile",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await asyncio.sleep(3)

        # 尝试从当前 URL 提取用户 ID
        current_url = page.url
        import re
        match = re.search(r"/user/profile/([^/?]+)", current_url)
        if match:
            user_id = match.group(1)
            logger.info(f"当前用户 ID: {user_id}")

        profile = await _extract_user_profile_data(page)
        return profile

    except Exception as e:
        logger.error(f"获取我的资料失败: {e}")
        raise
