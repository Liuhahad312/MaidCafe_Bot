"""
Pillow 图片修改服务
===================
基础滤镜、贴纸、文字处理。
客人发送图片 + 文字指令 → 返回处理后的图片。
"""

import io
import os
import re
import tempfile
from pathlib import Path

import httpx
from PIL import Image, ImageFilter, ImageDraw, ImageFont, ImageOps
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Bot, MessageSegment
from nonebot.rule import Rule

# ═══════════════════════════════════════════
# 图片处理指令检测
# ═══════════════════════════════════════════

IMAGE_SERVICE_KEYWORDS = [
    "修图", "P图", "p图", "滤镜", "p一下", "P一下",
    "加滤镜", "黑白", "复古", "模糊", "加字", "贴纸",
    "处理一下", "帮我修", "改一下图", "美化",
    "灰度", "怀旧", "柔化", "锐化",
]


def _is_image_service_request(text: str) -> bool:
    return any(kw in text for kw in IMAGE_SERVICE_KEYWORDS)


def _extract_images_for_service(event: GroupMessageEvent) -> list[str]:
    """提取消息中的图片 URL"""
    urls = []
    for seg in event.message:
        if seg.type == "image":
            url = seg.data.get("url", "")
            if url:
                urls.append(url)
    return urls


async def _image_service_rule(event: GroupMessageEvent) -> bool:
    text = event.get_plaintext().strip()
    has_image = len(_extract_images_for_service(event)) > 0
    return has_image and _is_image_service_request(text)


image_service = on_message(
    rule=Rule(_image_service_rule),
    priority=42,   # 高于 chat_handler 但低于塔罗
    block=True,
)


# ═══════════════════════════════════════════
# 图片下载
# ═══════════════════════════════════════════

async def _download_image(url: str) -> bytes:
    """下载图片字节"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


# ═══════════════════════════════════════════
# 滤镜处理
# ═══════════════════════════════════════════

def _apply_filter(img: Image.Image, filter_type: str) -> Image.Image:
    """应用指定滤镜"""
    filter_type = filter_type.lower()

    if filter_type in ("黑白", "灰度", "grayscale", "grey", "gray"):
        return ImageOps.grayscale(img).convert("RGB")

    elif filter_type in ("复古", "怀旧", "sepia"):
        gray = ImageOps.grayscale(img)
        # Sepia matrix
        sepia_img = Image.new("RGB", img.size)
        pixels = gray.load()
        sepia_pixels = sepia_img.load()
        for y in range(img.height):
            for x in range(img.width):
                g = pixels[x, y]
                r = min(255, int(g * 1.2))
                g2 = min(255, int(g * 0.95))
                b = min(255, int(g * 0.7))
                sepia_pixels[x, y] = (r, g2, b)
        return sepia_img

    elif filter_type in ("模糊", "blur"):
        return img.filter(ImageFilter.GaussianBlur(radius=3))

    elif filter_type in ("柔化", "柔焦", "soft"):
        return img.filter(ImageFilter.SMOOTH_MORE)

    elif filter_type in ("锐化", "sharpen"):
        return img.filter(ImageFilter.SHARPEN)

    elif filter_type in ("轮廓", "contour"):
        return img.filter(ImageFilter.CONTOUR)

    elif filter_type in ("浮雕", "emboss"):
        return img.filter(ImageFilter.EMBOSS)

    elif filter_type in ("边缘", "edge"):
        return img.filter(ImageFilter.FIND_EDGES)

    return img


def _parse_filter_request(text: str) -> Optional[str]:
    """从文字中解析滤镜类型"""
    filter_map = {
        "黑白": "黑白", "灰度": "黑白", "grayscale": "黑白",
        "复古": "复古", "怀旧": "复古", "sepia": "复古", "老照片": "复古",
        "模糊": "模糊", "blur": "模糊", "朦胧": "模糊",
        "柔化": "柔化", "柔焦": "柔化", "soft": "柔化",
        "锐化": "锐化", "sharpen": "锐化", "清晰": "锐化",
        "轮廓": "轮廓", "contour": "轮廓",
        "浮雕": "浮雕", "emboss": "浮雕",
        "边缘": "边缘", "edge": "边缘",
    }
    for keyword, filter_name in filter_map.items():
        if keyword.lower() in text.lower():
            return filter_name
    return "黑白"  # 默认灰度


def _extract_overlay_text(text: str) -> Optional[str]:
    """提取要叠加的文字 (引号内或「」内的文字)"""
    # 匹配中文引号
    for pattern in [r'"([^"]+)"', r'"([^"]+)"', r'"([^"]+)"', r'「([^」]+)」']:
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return None


def _add_text_overlay(img: Image.Image, text: str) -> Image.Image:
    """在图片底部添加文字"""
    result = img.copy()
    draw = ImageDraw.Draw(result)

    # 计算文字大小和位置
    font_size = max(20, img.height // 15)
    try:
        # 尝试使用系统字体
        font = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", font_size)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", font_size)
        except (OSError, IOError):
            font = ImageFont.load_default()

    # 文字位置: 底部居中
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (img.width - text_width) // 2
    y = img.height - text_height - 30

    # 半透明背景
    padding = 10
    bg_bbox = [
        x - padding,
        y - padding,
        x + text_width + padding,
        y + text_height + padding,
    ]
    overlay = Image.new("RGBA", result.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle(bg_bbox, fill=(0, 0, 0, 120))
    result = Image.alpha_composite(result.convert("RGBA"), overlay)

    # 画文字
    draw = ImageDraw.Draw(result)
    draw.text((x, y), text, fill=(255, 255, 255), font=font)

    return result.convert("RGB")


# ═══════════════════════════════════════════
# 消息处理器
# ═══════════════════════════════════════════

@image_service.handle()
async def handle_image_service(event: GroupMessageEvent, bot: Bot):
    """
    处理图片修改请求:
    1. 下载图片
    2. 应用滤镜
    3. 叠加文字 (如有)
    4. 发回处理后的图片
    """
    text = event.get_plaintext().strip()
    image_urls = _extract_images_for_service(event)

    if not image_urls:
        await image_service.send("诶？茉晓没有看到客人发的图片呢...再试一次？")
        return

    await image_service.send("收到～茉晓来帮你修图！稍等一下下哦 (。・ω・。)")

    try:
        # 下载第一张图片
        img_data = await _download_image(image_urls[0])
        img = Image.open(io.BytesIO(img_data)).convert("RGB")

        # 应用滤镜
        filter_type = _parse_filter_request(text)
        img = _apply_filter(img, filter_type)

        # 叠加文字
        overlay_text = _extract_overlay_text(text)
        if overlay_text:
            img = _add_text_overlay(img, overlay_text)

        # 保存到临时文件
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
            img.save(tmp_path, "PNG")

        # 发送处理后的图片
        try:
            # 使用 file:// 协议发送本地文件
            msg = MessageSegment.image(f"file:///{tmp_path.replace(os.sep, '/').lstrip('/')}")
            await image_service.send(msg)
            await image_service.send(f"修好啦～应用了「{filter_type}」滤镜 {'+ 文字装饰' if overlay_text else ''} (◕‿◕)ﾉ")
        except Exception:
            await image_service.send(f"啊...图片处理好了但是发不出去呢 >_<\n（应用了「{filter_type}」滤镜，请检查图片发送权限）")

        # 清理临时文件 (延迟，避免发送未完成)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    except Exception as e:
        await image_service.send(f"唔...修图的时候出了点问题呢 >_< （{str(e)[:60]}）")
