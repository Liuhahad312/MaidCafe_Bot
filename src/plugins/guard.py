"""
守卫插件 —— 全局安全与行为规则
==============================
1. 拦截 / 开头的消息 → 装傻，解释自己是店员不是机器人
2. 黑名单检查 → 每条消息拦截 + 新成员进群秒踢
"""

import random
from nonebot import on_message, on_notice
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    GroupIncreaseNoticeEvent,
    Bot,
)
from nonebot.rule import Rule

from mox.database import is_blacklisted

# ═══════════════════════════════════════════
# 1. / 指令拦截 (PRD §3 - 绝对无指令化)
# ═══════════════════════════════════════════

SLASH_CONFUSED_REPLIES = [
    "咦？你发的是什么呀，茉晓只是个咖啡店打工的店员啦，看不懂这种带斜杠的东西呢～有什么需要直接跟我说就好啦 (。・ω・。)",
    "啊？这是什么东西...茉晓只会泡咖啡和陪客人聊天哦，不会操作这种东西呢 awa",
    "唔...这位客人，我们咖啡店不提供指令服务的说～想喝什么直接告诉茉晓就行！",
    "诶诶？那个斜杠是什么呀？茉晓不是机器人啦，我是这里的女仆店员！要聊天的话直接说就好～",
    "（歪头）客人你在干嘛呀？茉晓看不懂这些符号呢...不如我们来聊聊咖啡或者占卜吧 (●'◛'●)",
]


async def _is_slash_message(event: GroupMessageEvent) -> bool:
    raw = event.get_plaintext().strip()
    return raw.startswith("/") and len(raw) > 1


slash_guard = on_message(
    rule=Rule(_is_slash_message),
    priority=1,   # 最高优先级
    block=True,   # 拦截后阻止后续处理
)


@slash_guard.handle()
async def handle_slash(event: GroupMessageEvent):
    reply = random.choice(SLASH_CONFUSED_REPLIES)
    await slash_guard.finish(reply)


# ═══════════════════════════════════════════
# 2. 黑名单人员消息拦截
# ═══════════════════════════════════════════

async def _is_blacklisted_user(event: GroupMessageEvent) -> bool:
    return await is_blacklisted(str(event.user_id))


blacklist_guard = on_message(
    rule=Rule(_is_blacklisted_user),
    priority=2,   # 仅次于 slash guard
    block=True,
)


@blacklist_guard.handle()
async def handle_blacklist_msg(event: GroupMessageEvent, bot: Bot):
    # 黑名单用户消息直接吞掉，不回复（避免引起注意）
    # 如果还没踢出群，尝试再次踢出
    try:
        await bot.set_group_kick(
            group_id=event.group_id,
            user_id=event.user_id,
            reject_add_request=True,
        )
    except Exception:
        pass


# ═══════════════════════════════════════════
# 3. 黑名单人员进群秒踢 (PRD §6)
# ═══════════════════════════════════════════

kick_guard = on_notice(priority=1, block=False)


@kick_guard.handle()
async def handle_group_increase(event: GroupIncreaseNoticeEvent, bot: Bot):
    """监听新成员进群，黑名单人员直接秒踢"""
    new_user_id = str(event.user_id)
    group_id = event.group_id

    if await is_blacklisted(new_user_id):
        try:
            await bot.set_group_kick(
                group_id=group_id,
                user_id=event.user_id,
                reject_add_request=True,
            )
        except Exception:
            pass
