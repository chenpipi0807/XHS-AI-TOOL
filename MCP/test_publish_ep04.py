"""
ep04 完整发布测试脚本
直接调用 publish_image_content 函数，测试：
1. 话题选择是否选到第一个
2. 封面裁切弹窗是否被正确处理
3. 发布是否成功

使用方法（在 MCP 目录下）：
  python test_publish_ep04.py

注意：MCP 服务器不需要运行，本脚本直接调用函数
"""
import asyncio
import os
import sys
import logging

# 设置日志级别为 INFO，看到完整流程
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xiaohongshu.publish import publish_image_content

# ep04 图片路径（绝对路径）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP04_DIR = os.path.join(BASE_DIR, "Tool", "data", "projects", "lol_comic", "ep04")

IMAGE_PATHS = [
    os.path.join(EP04_DIR, f"page_{str(i).zfill(2)}_0.png")
    for i in range(1, 10)
]

# ep04 发布参数（根据 script.md）
TITLE = "第4话：容器"  # 6字，不超限
CONTENT = """梅尔潜入前哨站执行任务，却陷入包围——

而地牢深处，弗拉基米尔正向乐芙兰展示他的"新容器"...

#英雄联盟 #Arcane双城之战 #梅尔 #弗拉基米尔 #芮尔 #漫画创作 #同人漫画"""

TAGS = ["英雄联盟", "Arcane双城之战", "梅尔", "弗拉基米尔", "芮尔", "漫画创作", "同人漫画"]


async def main():
    print("=" * 60)
    print("ep04 发布测试")
    print("=" * 60)

    # 检查图片文件
    print("\n📁 检查图片文件:")
    missing = []
    for path in IMAGE_PATHS:
        exists = os.path.exists(path)
        size_kb = os.path.getsize(path) // 1024 if exists else 0
        status = f"✅ {size_kb}KB" if exists else "❌ 不存在"
        print(f"  {status}  {os.path.basename(path)}")
        if not exists:
            missing.append(path)

    if missing:
        print(f"\n❌ 缺少 {len(missing)} 个图片文件，退出")
        return

    print(f"\n📝 发布参数:")
    print(f"  标题: {TITLE!r} ({len(TITLE)} 字)")
    print(f"  正文: {CONTENT[:60]}...")
    print(f"  话题: {TAGS}")
    print(f"  图片: {len(IMAGE_PATHS)} 张")
    print(f"\n🚀 开始发布...")

    try:
        result = await publish_image_content(
            title=TITLE,
            content=CONTENT,
            image_paths=IMAGE_PATHS,
            tags=TAGS,
            is_private=False,   # 公开发布
            is_original=True,
        )
        print("\n" + "=" * 60)
        print("✅ 发布成功！")
        print(f"  标题: {result.get('title')}")
        print(f"  图片数: {result.get('images_count')}")
        print(f"  帖子 URL: {result.get('post_url', '（未获取到）')}")
        print("=" * 60)
    except Exception as e:
        print("\n" + "=" * 60)
        print(f"❌ 发布失败: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
