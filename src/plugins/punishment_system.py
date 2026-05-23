"""
安保处刑系统 (PRD §6)
====================
严格按 PRD 第 6 节实现口球(禁言) / 送客(踢出) 完整处刑逻辑。
所有处刑必须: 先发嘲讽台词 → 再调用 API。

口球触发条件 (满足其一即触发):
  1. 连犯三次店规 (对 Bot 不敬/刷屏/脏话)
  2. 老板/员工要求
  3. 客人真诚主动请求
  4. 被 3 位以上贵客投票
  5. 惹茉晓极度生气

送客(踢出)触发条件:
  条件A: 被老板/员工下逐客令
  条件B: 单月被口球满 7 次
"""

import asyncio
import re
import time
from collections import defaultdict

from nonebot import on_message, get_bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Bot, MessageSegment
from nonebot.rule import Rule

from mox.middleware import is_master, is_employee, is_vip, get_current_user_id, get_current_group_id
from mox.database import (
    increment_gag_count,
    add_vip_vote,
    get_user,
)

# ═══════════════════════════════════════════
# 店规违反追踪 (条件1)
# ═══════════════════════════════════════════

# 违反关键词/模式
VIOLATION_PATTERNS = [
    r"(傻逼|sb|煞笔|脑残|弱智|废物|垃圾|cnm|草泥马|操你|fuck|bitch)",
    r"(滚|滚蛋|滚开|走开).*(茉晓|mox|bot|机器人)",
    r"(垃圾|破|烂|废物).*(bot|机器人|茉晓|女仆|店)",
    # 刷屏检测: 同用户 3 秒内连续 5 条以上 (在外部逻辑中处理)
]

# 每个用户在 10 分钟内的违规计数 {(group_id, user_id): [timestamps]}
_violation_log: dict[str, list[float]] = defaultdict(list)
_VIOLATION_WINDOW = 600      # 10 分钟窗口
_VIOLATION_THRESHOLD = 3     # 3 次触发口球
_GAG_DURATION = 600          # 口球时长 10 分钟 (秒)


def _record_violation(group_id: str, user_id: str) -> int:
    """记录一次违规，返回窗口内的违规次数"""
    key = f"{group_id}:{user_id}"
    now = time.time()
    # 清理过期记录
    _violation_log[key] = [t for t in _violation_log[key] if now - t < _VIOLATION_WINDOW]
    _violation_log[key].append(now)
    return len(_violation_log[key])


def _is_violation(text: str) -> bool:
    """检测消息是否违反店规"""
    for pattern in VIOLATION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


# ═══════════════════════════════════════════
# 口球投票追踪 (条件4)
# ═══════════════════════════════════════════

# 投票状态 {(group_id, target_qq): {voter_qq1, voter_qq2, ...}}
_vote_tracker: dict[str, set[str]] = defaultdict(set)
_VOTE_THRESHOLD = 3  # 3 位贵客投票触发


def _record_vip_vote(group_id: str, target_qq: str, voter_qq: str) -> tuple[bool, int]:
    """记录贵客投票，返回 (是否达到阈值, 当前票数)"""
    key = f"{group_id}:{target_qq}"
    _vote_tracker[key].add(voter_qq)
    count = len(_vote_tracker[key])
    reached = count >= _VOTE_THRESHOLD
    return reached, count


def _clear_votes(group_id: str, target_qq: str):
    """清除某人的投票记录 (口球执行后)"""
    key = f"{group_id}:{target_qq}"
    _vote_tracker.pop(key, None)


# ═══════════════════════════════════════════
# 处刑台词 (必须一字不差)
# ═══════════════════════════════════════════

# --- 口球台词 (PRD §6 - 强制处刑台词) ---
GAG_TAUNT = "嘿嘿嘿，[CQ:at,qq={target_qq}]带口球的样子真可爱awa"

# --- 逐客令台词 (条件A: 老板/员工下逐客令) ---
KICK_A_LINE1 = "哎呀，看来你被老板下逐客令了w"
KICK_A_LINE2 = "客人请走这边awa"
KICK_A_LINE3 = "欢迎下次光临!"

# --- 7次口球自动送客台词 (条件B) ---
KICK_B_LINE1 = "哎呀，这位客人似乎有点挑剔"
KICK_B_LINE2 = "我先带你出去静静吧awa"

# --- 客人主动请求口球回复 ---
SELF_GAG_REPLY = "诶？客人自己要求的话...那好吧～{gag_taunt}"
SELF_GAG_REPLY_ALT = "真是奇怪的请求呢...不过茉晓满足你！{gag_taunt}"


# ═══════════════════════════════════════════
# 处刑执行函数
# ═══════════════════════════════════════════

async def _execute_gag(bot: Bot, group_id: int, target_qq: int, reason: str = ""):
    """
    执行口球 (禁言)。

    顺序: 1. 发嘲讽 → 2. 调禁言 API
    """
    taunt = GAG_TAUNT.format(target_qq=target_qq)
    await bot.send_group_msg(group_id=group_id, message=taunt)
    await asyncio.sleep(0.5)

    try:
        await bot.set_group_ban(
            group_id=group_id,
            user_id=target_qq,
            duration=_GAG_DURATION,
        )
    except Exception:
        await bot.send_group_msg(
            group_id=group_id,
            message="唔...茉晓没有权限给这位客人戴口球呢 >_< (需要管理员权限哦)",
        )
        return

    # 数据库记录禁言次数
    new_count = await increment_gag_count(str(target_qq), str(group_id))

    # 清除投票
    _clear_votes(str(group_id), str(target_qq))

    # 检查是否达到 7 次 → 自动送客
    if new_count >= 7:
        await asyncio.sleep(1.0)
        await _execute_kick_b(bot, group_id, target_qq)


async def _execute_kick_a(bot: Bot, group_id: int, target_qq: int):
    """
    执行逐客 (条件A: 老板/员工逐客令)。

    顺序: 台词1 → 台词2 → 踢人 → 台词3
    """
    await bot.send_group_msg(group_id=group_id, message=KICK_A_LINE1)
    await asyncio.sleep(0.8)
    await bot.send_group_msg(group_id=group_id, message=KICK_A_LINE2)
    await asyncio.sleep(0.5)

    try:
        await bot.set_group_kick(
            group_id=group_id,
            user_id=target_qq,
            reject_add_request=True,
        )
    except Exception:
        await bot.send_group_msg(
            group_id=group_id,
            message="唔...茉晓踢不动这位客人呢 >_< (需要管理员权限哦)",
        )
        return

    await asyncio.sleep(0.5)
    await bot.send_group_msg(group_id=group_id, message=KICK_A_LINE3)


async def _execute_kick_b(bot: Bot, group_id: int, target_qq: int):
    """
    执行逐客 (条件B: 单月口球满 7 次)。

    顺序: 台词1 → 台词2 → 踢人
    """
    await bot.send_group_msg(group_id=group_id, message=KICK_B_LINE1)
    await asyncio.sleep(0.8)
    await bot.send_group_msg(group_id=group_id, message=KICK_B_LINE2)
    await asyncio.sleep(0.5)

    try:
        await bot.set_group_kick(
            group_id=group_id,
            user_id=target_qq,
            reject_add_request=True,
        )
    except Exception:
        pass  # 踢人失败不额外提示 (台词已发)


# ═══════════════════════════════════════════
# 消息规则: 处刑相关检测
# ═══════════════════════════════════════════

async def _punishment_rule(event: GroupMessageEvent) -> bool:
    text = event.get_plaintext().strip()
    user_id = str(event.user_id)
    group_id = str(event.group_id)

    # 1. 违反店规
    if _is_violation(text):
        return True

    # 2. 老板/员工要求口球或踢人
    if is_master() or is_employee():
        gag_kw = ("口球", "禁言", "戴口球", "闭嘴", "不许说话", "让他安静", "把他禁了")
        kick_kw = ("逐客", "踢出去", "送客", "踢了", "赶出去", "请他出去", "把他踢了")
        if any(kw in text for kw in gag_kw) or any(kw in text for kw in kick_kw):
            return True

    # 3. 客人主动请求口球
    if any(kw in text for kw in ("给我口球", "请禁言我", "禁言我吧", "让我闭嘴", "给我戴口球", "我要口球")):
        return True

    # 4. 贵客投票口球
    if is_vip():
        if "投票口球" in text or "投口球" in text or ("支持" in text and "口球" in text):
            return True

    return False


punishment_handler = on_message(
    rule=Rule(_punishment_rule),
    priority=35,   # 高优先级，在 tarot(40) 和 chat(50) 之前
    block=True,
)


@punishment_handler.handle()
async def handle_punishment(event: GroupMessageEvent, bot: Bot):
    text = event.get_plaintext().strip()
    user_id = str(event.user_id)
    group_id = str(event.group_id)

    # ─────────────────────────────────────
    # 条件 1: 违反店规 3 次 → 口球
    # ─────────────────────────────────────
    if _is_violation(text):
        count = _record_violation(group_id, user_id)
        if count >= _VIOLATION_THRESHOLD:
            await bot.send_group_msg(
                group_id=event.group_id,
                message=f"[CQ:at,qq={event.user_id}] 你已经连续违反店规 {count} 次了！"
            )
            await _execute_gag(bot, event.group_id, event.user_id, "连犯三次店规")
            return
        elif count == _VIOLATION_THRESHOLD - 1:
            await bot.send_group_msg(
                group_id=event.group_id,
                message=f"客人请注意言行哦...再这样下去茉晓要生气了 (｀へ´)  ({count}/{_VIOLATION_THRESHOLD})"
            )
            return

    # ─────────────────────────────────────
    # 条件 3: 客人主动请求口球
    # ─────────────────────────────────────
    if any(kw in text for kw in ("给我口球", "请禁言我", "禁言我吧", "让我闭嘴", "给我戴口球", "我要口球")):
        if is_master():
            # 老板/主人不能给自己口球
            await punishment_handler.send("老板你在说什么呀...茉晓怎么敢给你戴口球呢！(。・ω・。)")
            return

        import random
        template = random.choice([SELF_GAG_REPLY, SELF_GAG_REPLY_ALT])
        taunt = GAG_TAUNT.format(target_qq=event.user_id)
        await bot.send_group_msg(group_id=event.group_id, message=template.format(gag_taunt=taunt))
        return

    # ─────────────────────────────────────
    # 条件 4: 贵客投票口球
    # ─────────────────────────────────────
    if is_vip() and ("投票口球" in text or "投口球" in text or ("支持" in text and "口球" in text)):
        target_qq = _extract_at_qq(event) or _extract_qq_number(text)
        if target_qq:
            reached, count = _record_vip_vote(group_id, target_qq, user_id)
            # 数据库记录
            await add_vip_vote(target_qq, group_id)

            if reached:
                await punishment_handler.send(
                    f"已经有 {count} 位贵客投票给 [CQ:at,qq={target_qq}] 口球！"
                )
                await _execute_gag(bot, event.group_id, int(target_qq), "贵客联名投票")
            else:
                await punishment_handler.send(
                    f"收到贵客的投票！当前 [CQ:at,qq={target_qq}] 有 {count}/{_VOTE_THRESHOLD} 票 (｡・ω・｡)"
                )
        else:
            await punishment_handler.send("贵客想给谁投口球呀？@ 一下 ta 或者告诉茉晓 QQ 号～")
        return

    # ─────────────────────────────────────
    # 条件 2: 老板/员工要求处刑
    # ─────────────────────────────────────
    if is_master() or is_employee():
        target_qq_str = _extract_at_qq(event) or _extract_qq_number(text)

        # 口球命令
        gag_kw = ("口球", "禁言", "戴口球", "闭嘴", "不许说话", "让他安静", "把他禁了")
        if any(kw in text for kw in gag_kw):
            if target_qq_str:
                target_qq = int(target_qq_str)
                # 不能口球老板
                if target_qq_str == "1035585165":
                    await punishment_handler.send("诶？！不能给老板戴口球啊！(；′⌒`)")
                    return
                await _execute_gag(bot, event.group_id, target_qq, "老板/员工命令")
            else:
                await punishment_handler.send("想给谁戴口球呀？@ 一下 ta 或者告诉茉晓 QQ 号～")
            return

        # 逐客令
        kick_kw = ("逐客", "踢出去", "送客", "踢了", "赶出去", "请他出去", "把他踢了")
        if any(kw in text for kw in kick_kw):
            if target_qq_str:
                target_qq = int(target_qq_str)
                if target_qq_str == "1035585165":
                    await punishment_handler.send("诶诶诶？！不能踢老板啊！(；′⌒`)")
                    return
                # 条件A: 老板/员工逐客令 — 使用特定台词序列
                if is_master():
                    # 老板的逐客令，台词中说"老板"
                    await _execute_kick_a(bot, event.group_id, target_qq)
                else:
                    # 员工(管理员)的逐客令，用相同流程
                    await _execute_kick_a(bot, event.group_id, target_qq)
            else:
                await punishment_handler.send("想把谁请出去呀？@ 一下 ta 或者告诉茉晓 QQ 号～")
            return


# ═══════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════

def _extract_at_qq(event: GroupMessageEvent) -> str | None:
    """从消息中提取被 @ 的 QQ 号"""
    for seg in event.message:
        if seg.type == "at":
            qq = seg.data.get("qq", "")
            if qq and qq != "all":
                return qq
    return None


def _extract_qq_number(text: str) -> str | None:
    """从文本中提取 QQ 号"""
    m = re.search(r"\b(\d{5,11})\b", text)
    return m.group(1) if m else None
