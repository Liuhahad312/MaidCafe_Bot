"""
高优唤醒插件
============
全局监听包含 "茉晓" / "mox" / "茉晚" 的消息。
包含这些词的消息必须高优回应——哪怕 Bot 已下班，也要做出反应。
"""

import re
import random
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.rule import Rule

from mox.middleware import get_honorific, is_master
from mox.token_manager import get_token_manager

# ═══════════════════════════════════════════
# 唤醒词
# ═══════════════════════════════════════════

WAKE_PATTERNS = [
    "茉晓", "mox", "Mox", "MOX",
    "茉晚", "曉晓", "小茉",
]

WAKE_RESPONSES = [
    "诶？有人在叫茉晓吗～来啦来啦！(◕‿◕)ﾉ",
    "听到了听到了！客人有什么需要呀 awa",
    "茉晓在这里！正在擦杯子呢...有什么吩咐～",
    "嗯嗯！我在我在～是要点咖啡还是想聊天呀？",
    "呼...刚才在厨房忙呢，客人叫我吗？(。・ω・。)",
]

OFF_DUTY_WAKE_RESPONSES = [
    "唔...茉晓已经下班了在打扫卫生呢...不过既然客人叫我，有什么事吗？(´・ω・`)",
    "啊...是客人呀，虽然下班了但如果是急事的话...茉晓尽量帮忙 (；′⌒`)",
    "呜哇被发现了！茉晓正准备溜回家呢...客人有啥事呀？",
]

MASTER_WAKE_RESPONSES = [
    "{honorific}！茉晓在呢在呢～有什么吩咐 awa",
    "来啦{honorific}！一直在等你叫我呢 (◕‿◕)ﾉ",
    "{honorific}好！今天想喝点什么还是有什么任务呀？",
]


def _contains_wake_word(text: str) -> bool:
    """检测文本是否包含唤醒词"""
    return any(pattern.lower() in text.lower() for pattern in WAKE_PATTERNS)


async def _wake_rule(event: GroupMessageEvent) -> bool:
    text = event.get_plaintext().strip()
    return _contains_wake_word(text)


wake_handler = on_message(
    rule=Rule(_wake_rule),
    priority=5,    # 非常高优先级 (仅次于 guard)
    block=False,   # 不阻止，让消息继续流转到 chat_handler
)


@wake_handler.handle()
async def handle_wake(event: GroupMessageEvent):
    """
    检测到唤醒词时，发送唤醒回应。

    注意: block=False，所以消息会继续被 chat_handler 处理。
    这里只负责确保 Bot 对被叫名字的反应。
    """
    tm = get_token_manager()
    honorific = get_honorific()

    if is_master():
        # 老板/主人专属回应
        title = honorific if honorific else "老板"
        reply = random.choice(MASTER_WAKE_RESPONSES).format(honorific=title)
        await wake_handler.send(reply)

    elif tm.is_off_duty():
        # 下班但仍被叫到 → 极简回应
        reply = random.choice(OFF_DUTY_WAKE_RESPONSES)
        await wake_handler.send(reply)

    else:
        # 正常回应
        reply = random.choice(WAKE_RESPONSES)
        await wake_handler.send(reply)
