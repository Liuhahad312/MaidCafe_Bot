"""
主聊天路由插件
==============
协调所有子系统完成完整对话流程:
  image_buffer → memory → token → API routing → response

包含:
- 图文防抖缓冲协调
- 智商外包判断 (DeepSeek → Grok)
- 长线记忆注入与提取
- Token 预算与下班拦截
- 老板专属命令 (加工资 / 黑名单操作 / 解除黑名单)
"""

import re
import random
from typing import Optional

from nonebot import on_message, get_bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Bot, MessageSegment
from nonebot.rule import Rule

from mox.middleware import (
    get_hierarchy,
    get_honorific,
    is_master,
    is_vip,
    is_employee,
)
from mox.api_client import (
    get_deepseek,
    get_grok,
    MOX_SYSTEM_PROMPT,
    MOX_OUTSOURCE_MESSAGE,
)
from mox.image_buffer import get_image_buffer
from mox.memory_system import build_memory_context, schedule_memory_extraction
from mox.token_manager import (
    get_token_manager,
    OFF_DUTY_FULL_MESSAGE,
    OFF_DUTY_VIP_MESSAGE,
)
from mox.database import (
    add_to_blacklist,
    remove_from_blacklist,
    is_blacklisted,
)
from mox.sender import MessageSender

# ═══════════════════════════════════════════
# 系统初始化
# ═══════════════════════════════════════════

buffer = get_image_buffer(timeout=4.0)
tm = get_token_manager()
ds = get_deepseek()
grok = get_grok()

# ═══════════════════════════════════════════
# 消息类型检测工具
# ═══════════════════════════════════════════

def _extract_images(event: GroupMessageEvent) -> list[str]:
    """提取消息中所有图片 URL"""
    urls = []
    for seg in event.message:
        if seg.type == "image":
            url = seg.data.get("url", "")
            if url:
                urls.append(url)
    return urls


def _is_pure_image(event: GroupMessageEvent) -> bool:
    """纯图片消息 (无实质文字)"""
    has_image = any(seg.type == "image" for seg in event.message)
    if not has_image:
        return False
    text = event.get_plaintext().strip()
    img_placeholders = ("[图片]", "[表情]", "[动画表情]", "[image]", "")
    return text in img_placeholders


def _is_pure_face(event: GroupMessageEvent) -> bool:
    """纯 QQ 表情消息 (不触发任何 AI，忽略)"""
    has_face = any(seg.type in ("face", "mface") for seg in event.message)
    if not has_face:
        return False
    has_other = any(seg.type not in ("face", "mface") for seg in event.message)
    return not has_other


def _has_image_and_text(event: GroupMessageEvent) -> bool:
    """同一消息中同时包含图片和有效文字 (用户已自行合并)"""
    has_image = any(seg.type == "image" for seg in event.message)
    if not has_image:
        return False
    text = event.get_plaintext().strip()
    img_placeholders = ("", "[图片]", "[表情]", "[动画表情]", "[image]")
    real_text = text not in img_placeholders
    return has_image and real_text


# ═══════════════════════════════════════════
# 图文防抖回调
# ═══════════════════════════════════════════

async def _process_image_callback(event: GroupMessageEvent, merged_text: Optional[str]):
    """
    图片缓冲回调 — 超时或图文合并后触发。
    merged_text=None 表示超时(仅图片) / 有值表示图文合并。
    """
    bot = get_bot()
    group_id = event.group_id
    user_id = event.user_id
    image_urls = _extract_images(event)

    if not image_urls:
        return

    # 构建提示词
    if merged_text:
        prompt = f"客人发送了图片并说: {merged_text}\n请以茉晓的口吻回应，描述你对图片的看法。"
    else:
        prompt = "客人发送了一张图片。请以茉晓的口吻描述你看到了什么，并用活泼可爱的语气回应。"

    # 调用 Grok Vision
    resp = await grok.chat_with_vision(
        text=prompt,
        image_urls=image_urls,
        system_prompt="你是茉晓mox，咖啡店打工女仆。请用活泼可爱的语气回应。",
    )

    # 记录花费
    if resp.tokens_used > 0:
        await tm.record_spending(
            str(user_id), str(group_id), resp.tokens_used, resp.model, is_input=False
        )

    # 发送回复 (使用断句发送器)
    sender = MessageSender(bot)
    await sender.send_long_message(group_id=group_id, text=resp.content)


# 注册回调
buffer.set_callback(_process_image_callback)


# ═══════════════════════════════════════════
# 智商外包判断 (PRD §2 - Grok 外包)
# ═══════════════════════════════════════════

COMPLEX_TASK_PATTERNS = [
    # 代码/编程
    r"(写|帮我写|帮我弄|给我写).*(代码|程序|脚本|爬虫)",
    r"(编程|写码|代码|程序|python|java|html|css|js|javascript)",
    r"(函数|function|算法|algorithm|api|接口)",
    r"(bug|报错|错误).*(怎么|如何|帮忙)",
    # 写作/论文
    r"(写|帮我写|给我写).*(论文|文章|作文|报告|essay|article)",
    r"(毕业论文|期末论文|开题报告)",
    # 事实查证
    r"(真的假的|是不是真的|核实|查证|求证|帮忙查|查一下|辟谣)",
    r"(这件事|新闻|消息).*(真的|假的|可靠|可信)",
    # 翻译
    r"(翻译|translate).*(这段|这篇文章|这个|一下)",
    # 数学
    r"(算|计算|求解|数学题).*(一下|这个|帮我)",
    # 主动找 Grok
    r"(问.*grok|找.*grok|叫.*grok|grok.*帮)",
]


def _is_complex_task(text: str) -> bool:
    """判断是否为超纲任务 (需外包给 Grok)"""
    text_lower = text.lower()
    for pattern in COMPLEX_TASK_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


# ═══════════════════════════════════════════
# 老板自然语言命令检测
# ═══════════════════════════════════════════

def _extract_at_qq(event: GroupMessageEvent) -> Optional[str]:
    """从消息中提取被 @ 的 QQ 号"""
    for seg in event.message:
        if seg.type == "at":
            return seg.data.get("qq", "")
    return None


def _extract_qq_number(text: str) -> Optional[str]:
    """从文本中提取 QQ 号 (5-11位数字)"""
    m = re.search(r"\b(\d{5,11})\b", text)
    return m.group(1) if m else None


async def _handle_master_commands(event: GroupMessageEvent) -> Optional[str]:
    """
    检测并处理老板的自然语言命令。
    返回回复文本 (如果有)，返回 None 表示不是命令，继续普通对话。
    """
    text = event.get_plaintext().strip()
    group_id = str(event.group_id)

    # --- 加工资 (PRD §7) ---
    if any(kw in text for kw in ("加工资", "给你加工资", "涨工资", "加薪", "加工资啦")):
        # 尝试提取金额
        amount = 1.0
        m = re.search(r"(\d+\.?\d*)\s*(美元|刀|块|元|美金|USD)", text)
        if m:
            amount = float(m.group(1))
        return await tm.raise_salary(amount)

    # --- 加黑名单 (PRD §6) ---
    if any(kw in text for kw in ("加黑名单", "拉黑", "列入不受欢迎名单", "加入黑名单", "拉进黑名单")):
        target_qq = _extract_at_qq(event) or _extract_qq_number(text)
        if target_qq:
            # 提取理由
            reason = ""
            reason_m = re.search(r"(因为|理由|原因)[:：]?(.+)", text)
            if reason_m:
                reason = reason_m.group(2).strip()
            ok = await add_to_blacklist(target_qq, str(event.user_id), group_id, reason)
            if ok:
                return f"好的老板，已经把 {target_qq} 加入不受欢迎名单了！下次 ta 来咖啡店会被直接请出去的 awa"
            else:
                return f"嗯？{target_qq} 已经在不受欢迎名单里了哦老板～"
        else:
            return "老板，你想把谁加入黑名单呀？@ 一下 ta 或者告诉我 QQ 号就行～"

    # --- 解除黑名单 ---
    if any(kw in text for kw in ("解除黑名单", "移出黑名单", "取消拉黑", "从黑名单移除", "原谅")):
        target_qq = _extract_at_qq(event) or _extract_qq_number(text)
        if target_qq:
            ok = await remove_from_blacklist(target_qq)
            if ok:
                return f"好的老板，已经原谅 {target_qq} 了，从黑名单里移除了～"
            else:
                return f"诶？{target_qq} 本来就不在不受欢迎名单里呢..."
        else:
            return "老板想原谅谁呀？@ 一下 ta 或者给我 QQ 号～"

    return None  # 不是命令


# ═══════════════════════════════════════════
# System Prompt 构建
# ═══════════════════════════════════════════

async def _build_system_prompt(user_id: str, group_id: str) -> str:
    """动态构建茉晓的 System Prompt (含阶层 + 记忆)"""

    hierarchy = get_hierarchy()
    honorific = get_honorific()

    # 阶层上下文
    if hierarchy in ("老板", "主人"):
        hierarchy_context = f"当前和你说话的是你的{honorific}({hierarchy})。ta 拥有最高权限，你要对 ta 格外尊重和亲近。称呼 ta 为「{honorific}」。"
    elif hierarchy == "员工":
        hierarchy_context = "当前和你说话的是你的同事(员工/管理员)。你们是平级的打工人，可以亲切地称呼对方。"
    elif hierarchy == "贵客":
        hierarchy_context = "当前和你说话的是贵客(拥有群专属头衔的客人)。请提供更优先、更热情的服务。"
    else:
        hierarchy_context = "当前和你说话的是普通客人。请提供友好、日常的女仆服务。"

    # 记忆上下文
    memory_context = await build_memory_context(user_id, group_id)

    return MOX_SYSTEM_PROMPT.format(
        hierarchy_context=hierarchy_context,
        memory_context=memory_context if memory_context else "",
    )


# ═══════════════════════════════════════════
# 主消息处理器
# ═══════════════════════════════════════════

# 排除纯表情消息 (不触发任何处理)
def _not_pure_face(event: GroupMessageEvent) -> bool:
    return not _is_pure_face(event)


chat = on_message(
    rule=Rule(_not_pure_face),
    priority=50,   # 低于 guard，高于其他插件
    block=True,
)


@chat.handle()
async def handle_chat(event: GroupMessageEvent, bot: Bot):
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    text = event.get_plaintext().strip()
    sender = MessageSender(bot)  # 手癌 + 断句发送器

    # ── 1. 纯图片 → 缓冲等待文字 ──
    if _is_pure_image(event):
        await buffer.on_image(event)
        return

    # ── 2. 纯表情 → 忽略 (不回应) ──
    if _is_pure_face(event):
        return

    # ── 3. 同一消息已含图文 → 直接处理 ──
    if _has_image_and_text(event):
        image_urls = _extract_images(event)
        if image_urls:
            prompt = f"客人发送了图片并说: {text}\n请以茉晓的口吻回应，描述你对图片的看法并结合客人的话做出回应。"
            resp = await grok.chat_with_vision(
                text=prompt,
                image_urls=image_urls,
                system_prompt="你是茉晓mox，咖啡店打工女仆。请用活泼可爱的语气回应。",
            )
            if resp.tokens_used > 0:
                await tm.record_spending(user_id, group_id, resp.tokens_used, resp.model, is_input=False)
            await sender.send_long_message(event.group_id, resp.content)
            return

    # ── 4. 检查图文缓冲：是否在等图的文字 ──
    consumed, image_event = await buffer.on_text(event)
    if consumed and image_event:
        # 合并处理：之前的图片 + 当前文字
        image_urls = _extract_images(image_event)
        if image_urls:
            prompt = f"客人发送了图片并随后说: {text}\n请以茉晓的口吻回应，结合图片内容和客人的话。"
            resp = await grok.chat_with_vision(
                text=prompt,
                image_urls=image_urls,
                system_prompt="你是茉晓mox，咖啡店打工女仆。请用活泼可爱的语气回应。",
            )
            if resp.tokens_used > 0:
                await tm.record_spending(user_id, group_id, resp.tokens_used, resp.model, is_input=False)
            await sender.send_long_message(event.group_id, resp.content)
            return

    # ── 5. 纯文字消息 ──

    # 5a. 老板命令检测
    if is_master():
        cmd_result = await _handle_master_commands(event)
        if cmd_result:
            await chat.send(cmd_result)
            return

    # 5b. Token 预算检查 (下班拦截)
    if tm.is_off_duty():
        if is_vip():
            await chat.send(OFF_DUTY_VIP_MESSAGE)
            # 贵客破例：允许极简短回应
            resp = await ds.chat(
                messages=[{"role": "user", "content": text}],
                system_prompt="你是茉晓，已下班。这是贵客。用极短的一句话回应(不超过30字)，然后催促对方早点休息。",
                max_tokens=100,
            )
            if resp.tokens_used > 0:
                await tm.record_spending(user_id, group_id, resp.tokens_used, resp.model, is_input=False)
            await sender.send_single(event.group_id, resp.content)  # 简短回应，不用拆分
        else:
            await chat.send(OFF_DUTY_FULL_MESSAGE)
        return

    # 5c. 智商外包判断
    is_complex = _is_complex_task(text)

    if is_complex:
        # 先发外包声明
        await chat.send(MOX_OUTSOURCE_MESSAGE)

        # 转交 Grok 处理
        resp = await grok.chat(
            messages=[{"role": "user", "content": text}],
            system_prompt=(
                "你是茉晓mox，咖啡店打工女仆。刚才你把一个复杂任务外包给了 grok 哥哥(也就是你自己现在的角色)。"
                "请用茉晓的口吻回答，但要提供高质量的内容。"
                "记住你不是机器人，用活泼可爱的女仆语气。"
            ),
        )
        if resp.tokens_used > 0:
            await tm.record_spending(user_id, group_id, resp.tokens_used, resp.model, is_input=False)
        await sender.send_long_message(event.group_id, resp.content)

        # 火后记忆提取
        schedule_memory_extraction(group_id, user_id, text, resp.content)
        return

    # 5d. 普通对话 → DeepSeek (主大脑)
    system_prompt = await _build_system_prompt(user_id, group_id)

    resp = await ds.chat(
        messages=[{"role": "user", "content": text}],
        system_prompt=system_prompt,
    )

    if resp.tokens_used > 0:
        await tm.record_spending(user_id, group_id, resp.tokens_used, resp.model, is_input=False)

    await sender.send_long_message(event.group_id, resp.content)

    # 火后记忆提取
    schedule_memory_extraction(group_id, user_id, text, resp.content)
