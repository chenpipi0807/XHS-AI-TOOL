"""
Tool/image/gemini_image.py
Gemini Image 生成封装（适配中转/代理 API）

支持功能：
1. 文生图（Text-to-Image）
2. 对话式图像编辑（需保存 thoughtSignature）
3. 带参考图生成
4. 模型：gemini-3.1-flash-image-preview（默认）

API 配置从 Tool/.env 读取：
  GEMINI_API_KEY      : 中转 API Key
  GEMINI_API_BASE_URL : 中转 API Base URL（可含 /v1beta，也可不含）
  GEMINI_IMAGE_MODEL  : 模型名（默认 gemini-3.1-flash-image-preview）
"""

import os
import base64
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

# 加载 Tool/.env
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path, override=False)

logger = logging.getLogger(__name__)

# ── 支持的模型与参数 ──────────────────────────────────────────────
SUPPORTED_MODELS = {
    "gemini-3.1-flash-image-preview",
    "gemini-3-pro-image-preview",
}

MODEL_ASPECT_RATIOS: Dict[str, set] = {
    "gemini-3.1-flash-image-preview": {
        "auto", "1:1", "2:3", "3:2", "3:4", "4:3",
        "4:5", "5:4", "9:16", "16:9", "21:9", "4:1", "1:4", "8:1", "1:8"
    },
    "gemini-3-pro-image-preview": {
        "auto", "1:1", "3:4", "4:3", "9:16", "16:9"
    },
}

MODEL_IMAGE_SIZES: Dict[str, List[str]] = {
    "gemini-3.1-flash-image-preview": ["512", "1K", "2K", "4K"],
    "gemini-3-pro-image-preview": ["1K", "2K", "4K"],
}


# ── 工具函数 ──────────────────────────────────────────────────────
def _read_file_b64(path: str) -> Tuple[str, str]:
    """读取本地图片文件，返回 (mime_type, base64_str)"""
    ext = os.path.splitext(path)[1].lower()
    mime = "image/png"
    if ext in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif ext == ".webp":
        mime = "image/webp"
    elif ext == ".gif":
        mime = "image/gif"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return mime, b64


def _get_api_config() -> Tuple[str, str, str]:
    """
    从环境变量读取 API 配置。
    返回 (api_base_url, api_key, model)
    """
    api_key  = os.environ.get("GEMINI_API_KEY", "").strip()
    api_base = os.environ.get("GEMINI_API_BASE_URL", "").strip().rstrip("/")
    model    = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image-preview").strip()
    return api_base, api_key, model


def _build_api_url(api_base: str, model: str) -> str:
    """
    拼接 Gemini API URL。
    支持中转 API base 含或不含 /v1beta。
    """
    if "v1beta" in api_base:
        return f"{api_base}/models/{model}:generateContent"
    return f"{api_base}/v1beta/models/{model}:generateContent"


# ── 核心生成函数 ──────────────────────────────────────────────────
def generate_image(
    prompt: str,
    ref_image_paths: Optional[List[str]] = None,
    output_dir: Optional[Path] = None,
    output_filename: Optional[str] = None,
    aspect_ratio: str = "3:4",
    image_size: str = "2K",
    use_google_search: bool = False,
    temperature: float = 1.0,
    history: Optional[List[Dict[str, Any]]] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    使用 Gemini Image Preview 生成图像（适配中转 API）。

    Args:
        prompt          : 提示词
        ref_image_paths : 参考图路径列表（图生图时传入）
        output_dir      : 保存目录，None 则不保存到磁盘
        output_filename : 自定义文件名（不含扩展名），None 则自动生成
        aspect_ratio    : 宽高比，默认 "3:4"（小红书竖图）
        image_size      : "512" / "1K" / "2K" / "4K"
        use_google_search: 是否启用 Google Search 工具
        temperature     : 生成温度（默认 1.0）
        history         : 对话历史（对话式编辑时传入）
        model           : 模型名（None 则读 env）
        api_key         : API Key（None 则读 env）
        request_id      : 请求 ID（日志用）

    Returns:
        {
          'success': bool,
          'images': List[str],           # 本地保存路径
          'image_data': List[str],       # base64 数据
          'thought_signatures': List[str],
          'history': List[Dict],         # 用于下次对话式编辑
          'error': str,
          'error_details': str,
        }
    """
    if not request_id:
        request_id = uuid.uuid4().hex[:8]

    try:
        # ── 读取 API 配置 ────────────────────────────────────────────
        api_base, env_key, env_model = _get_api_config()
        if not api_key:
            api_key = env_key
        if not model:
            model = env_model

        if not api_base:
            return {"success": False, "error": "GEMINI_API_BASE_URL 未配置，请在 Tool/.env 中填写"}
        if not api_key:
            return {"success": False, "error": "GEMINI_API_KEY 未配置，请在 Tool/.env 中填写"}

        # ── 参数标准化 ───────────────────────────────────────────────
        model        = model.strip()
        aspect_ratio = (aspect_ratio or "auto").strip()
        image_size   = (image_size   or "2K").strip().upper()

        # 模型校验
        if model not in SUPPORTED_MODELS:
            return {
                "success": False,
                "error": f"不支持的模型: {model}",
                "error_details": f"支持: {', '.join(sorted(SUPPORTED_MODELS))}"
            }

        # 宽高比校验
        allowed_ratios = MODEL_ASPECT_RATIOS.get(model, {"auto"})
        if aspect_ratio not in allowed_ratios:
            return {
                "success": False,
                "error": f"模型 {model} 不支持宽高比 {aspect_ratio}",
                "error_details": f"可选: {', '.join(sorted(allowed_ratios))}"
            }

        # 分辨率校验
        allowed_sizes = MODEL_IMAGE_SIZES.get(model, ["2K"])
        if image_size not in allowed_sizes:
            return {
                "success": False,
                "error": f"模型 {model} 不支持分辨率 {image_size}",
                "error_details": f"可选: {', '.join(allowed_sizes)}"
            }

        # ── 构建 contents ────────────────────────────────────────────
        # 如果有历史记录（对话式编辑），沿用历史 contents
        if history:
            contents = list(history)
            # 追加新用户轮次
            contents.append({"role": "user", "parts": [{"text": prompt}]})
        else:
            parts: List[Dict[str, Any]] = []

            # ── 参考图放在 prompt 之前（图生图正确格式）────────────────
            loaded_ref_count = 0
            if ref_image_paths:
                logger.info(f"[gemini_image:{request_id}] ===== 参考图传入详情 =====")
                logger.info(f"[gemini_image:{request_id}] 共 {len(ref_image_paths)} 张参考图")
                for idx, img_path in enumerate(ref_image_paths):
                    abs_path = os.path.abspath(img_path)
                    exists = os.path.exists(abs_path)
                    logger.info(f"[gemini_image:{request_id}] 参考图[{idx}]: {abs_path} | 存在={exists}")
                    if not exists:
                        logger.warning(f"[gemini_image:{request_id}] ⚠️ 参考图不存在，跳过: {abs_path}")
                        continue
                    try:
                        file_size = os.path.getsize(abs_path)
                        mime, b64 = _read_file_b64(abs_path)
                        logger.info(f"[gemini_image:{request_id}] ✅ 参考图[{idx}] 读取成功: mime={mime}, 文件大小={file_size}字节, base64长度={len(b64)}")
                        parts.append({"inlineData": {"mimeType": mime, "data": b64}})
                        loaded_ref_count += 1
                    except Exception as e:
                        logger.warning(f"[gemini_image:{request_id}] ❌ 读取参考图失败 {abs_path}: {e}")
                logger.info(f"[gemini_image:{request_id}] 参考图加载完成: {loaded_ref_count}/{len(ref_image_paths)} 张成功加入请求")
                logger.info(f"[gemini_image:{request_id}] ===========================")
            else:
                logger.info(f"[gemini_image:{request_id}] 无参考图（纯文生图模式）")

            # 文字 prompt 放在图片之后
            if prompt:
                parts.append({"text": prompt})

            contents = [{"role": "user", "parts": parts}]

        # ── 构建请求体 ───────────────────────────────────────────────
        image_config: Dict[str, Any] = {"imageSize": image_size}
        if aspect_ratio and aspect_ratio.lower() != "auto":
            image_config["aspectRatio"] = aspect_ratio

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "responseModalities": ["TEXT", "IMAGE"],  # 必须显式声明输出包含图像
                "imageConfig": image_config,
            },
        }

        if use_google_search:
            payload["tools"] = [{"google_search": {}}]

        # ── 发送请求 ─────────────────────────────────────────────────
        api_url = _build_api_url(api_base, model)
        headers = {
            "x-goog-api-key": api_key,
            "Content-Type":   "application/json",
        }
        timeout = 600 if ref_image_paths else 300

        # ── 发送前打印 payload 摘要（不含 base64 数据）──────────────
        user_parts = contents[0].get("parts", []) if contents else []
        text_parts_count = sum(1 for p in user_parts if "text" in p)
        image_parts_count = sum(1 for p in user_parts if "inlineData" in p)
        logger.info(
            f"[gemini_image:{request_id}] POST {api_url} model={model} "
            f"size={image_size} ratio={aspect_ratio} "
            f"| parts总数={len(user_parts)} (文本={text_parts_count}, 参考图inlineData={image_parts_count})"
        )
        print(
            f"\n🖼️  [gemini_image:{request_id}] 生图请求摘要:\n"
            f"   模型: {model}\n"
            f"   URL:  {api_url}\n"
            f"   参数: size={image_size}, ratio={aspect_ratio}\n"
            f"   Parts: 总计={len(user_parts)}, 文本={text_parts_count}, 参考图={image_parts_count}\n"
            + (
                "   ✅ 参考图已加入请求\n"
                if image_parts_count > 0
                else "   ⚠️  无参考图（纯文生图）\n"
            ),
            flush=True
        )

        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"请求异常: {e}", "error_details": str(e)}

        if response.status_code != 200:
            err = response.text[:500]
            logger.error(f"[gemini_image:{request_id}] HTTP {response.status_code}: {err}")
            return {
                "success": False,
                "error": f"API 请求失败: {response.status_code}",
                "error_details": response.text,
            }

        # ── 解析响应 ─────────────────────────────────────────────────
        resp_data  = response.json()
        candidates = resp_data.get("candidates", [])
        if not candidates:
            return {
                "success": False,
                "error": "响应中没有 candidates",
                "error_details": json.dumps(resp_data, ensure_ascii=False),
            }

        candidate0 = candidates[0]
        content    = candidate0.get("content", {})
        resp_parts = content.get("parts", [])

        image_data_list:   List[str] = []
        thought_signatures: List[str] = []

        for part in resp_parts:
            # 提取 thoughtSignature（对话式编辑需保存）
            if "thoughtSignature" in part:
                thought_signatures.append(part["thoughtSignature"])

            # 提取图像数据（inlineData 格式）
            if "inlineData" in part:
                data = part["inlineData"].get("data", "")
                if data:
                    image_data_list.append(data)

            # 兼容 image_url 格式（部分中转 API）
            elif part.get("type") == "image_url":
                url = part.get("image_url", {}).get("url", "")
                if url.startswith("data:image/") and ";base64," in url:
                    image_data_list.append(url.split(";base64,", 1)[1])
                elif url.startswith("http"):
                    try:
                        r = requests.get(url, timeout=60)
                        if r.status_code == 200:
                            image_data_list.append(base64.b64encode(r.content).decode())
                    except Exception as e:
                        logger.warning(f"[gemini_image:{request_id}] 下载图片失败: {e}")

        if not image_data_list:
            return {
                "success": False,
                "error": "响应中没有图像数据",
                "error_details": json.dumps(resp_parts, ensure_ascii=False)[:500],
            }

        logger.info(f"[gemini_image:{request_id}] 生成成功，共 {len(image_data_list)} 张图片，{len(thought_signatures)} 个 thoughtSignature")

        # ── 保存到本地 ───────────────────────────────────────────────
        saved_paths: List[str] = []
        if output_dir:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            for idx, img_data in enumerate(image_data_list):
                try:
                    fname = f"{output_filename}_{idx}.png" if output_filename else f"gemini_{request_id}_{idx}.png"
                    img_path = output_dir / fname
                    img_path.write_bytes(base64.b64decode(img_data))
                    saved_paths.append(str(img_path))
                    logger.info(f"[gemini_image:{request_id}] 保存图片: {img_path}")
                except Exception as e:
                    logger.error(f"[gemini_image:{request_id}] 保存图片 {idx} 失败: {e}")

        # ── 构建对话历史（用于下次对话式编辑）────────────────────────
        new_history = list(contents)
        new_history.append({"role": "model", "parts": resp_parts})

        return {
            "success":           True,
            "images":            saved_paths,
            "image_data":        image_data_list,
            "thought_signatures": thought_signatures,
            "history":           new_history,
            "error":             "",
        }

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"[gemini_image:{request_id}] 未捕获异常: {e}\n{tb}")
        return {"success": False, "error": f"生成图像时发生错误: {e}", "error_details": tb}


def edit_image(
    edit_prompt: str,
    history: List[Dict[str, Any]],
    output_dir: Optional[Path] = None,
    output_filename: Optional[str] = None,
    aspect_ratio: str = "3:4",
    image_size: str = "2K",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    对话式图像编辑（基于 generate_image 返回的 history）。

    Args:
        edit_prompt     : 编辑指令（例如："把天空改成夜晚"）
        history         : generate_image 返回的 history 字段
        其余参数同 generate_image

    Returns:
        同 generate_image
    """
    return generate_image(
        prompt=edit_prompt,
        history=history,
        output_dir=output_dir,
        output_filename=output_filename,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        model=model,
        api_key=api_key,
        request_id=request_id,
    )


def generate_image_simple(
    prompt: str,
    output_path: str,
    aspect_ratio: str = "3:4",
    image_size: str = "2K",
) -> bool:
    """
    简化版接口：生成单张图片并保存到指定路径。

    Args:
        prompt      : 提示词
        output_path : 输出文件完整路径（.png）
        aspect_ratio: 宽高比
        image_size  : 分辨率

    Returns:
        bool: 是否成功
    """
    output_path = Path(output_path)
    result = generate_image(
        prompt=prompt,
        output_dir=output_path.parent,
        output_filename=output_path.stem,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
    )

    if result["success"] and result["images"]:
        # 如果生成文件名与目标不同，重命名
        generated = Path(result["images"][0])
        if generated != output_path and generated.exists():
            generated.rename(output_path)
        return True
    return False
