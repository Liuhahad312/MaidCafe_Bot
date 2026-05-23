"""
定时任务与作息关怀 (PRD §4)
============================
- 凌晨 0:00-5:00 若有人聊天 → 主动提醒早睡
- 每天 22:00 → 拉取历史生成各级新闻简报

与 NoneBot2 生命周期集成，在 driver.on_startup 注册调度器。
"""

import asyncio
import random
from datetime import datetime, date

from nonebot import on_message, get_driver, get_bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Bot, MessageSegment
from nonebot.rule import Rule

from mox.middleware import get_current_user_id, get_current_group_id
from mox.token_manager import get_token_manager
from mox.api_client import get_deepseek

driver = get_driver()

# ═══════════════════════════════════════════
# 凌晨睡觉提醒 (PRD §4 - 作息关怀)
# ═══════════════════════════════════════════

SLEEP_REMINDERS = [
    "都已经凌晨了诶，客人还不睡觉吗？明天可是会变成熊猫眼的哦 🐼",
    "这么晚了还在聊天呀～茉晓都开始犯困了...客人也早点休息吧 (´・ω・`)",
    "凌晨了哦！对皮肤不好的！快去睡觉啦～明天茉晓还在咖啡店等你！",
    "修仙吗客人？不可以不可以！快去睡觉！(｀へ´)",
    "呜哇都这个点了！客人明天不用上班/上学的吗？快点去休息啦～",
    "茉晓都快睡着了...客人也快回家睡觉啦，晚安～💤",
]

# 冷却 (每个用户每小时最多提醒一次)
_last_sleep_reminder: dict[str, float] = {}


async def _sleep_rule(event: GroupMessageEvent) -> bool:
    """凌晨 0:00-5:00 且消息有实质内容"""
    now = datetime.now()
    if not (0 <= now.hour < 5):
        return False
    text = event.get_plaintext().strip()
    return len(text) >= 4


sleep_observer = on_message(
    rule=Rule(_sleep_rule),
    priority=55,     # 低优先级观察者
    block=False,     # 不阻塞
)


@sleep_observer.handle()
async def handle_sleep(event: GroupMessageEvent):
    """凌晨聊天时提醒早睡 (带冷却)"""
    import time
    user_id = str(event.user_id)
    key = f"{event.group_id}:{user_id}"

    now = time.time()
    if key in _last_sleep_reminder:
        if now - _last_sleep_reminder[key] < 3600:  # 1 小时冷却
            return

    _last_sleep_reminder[key] = now

    # 30% 概率提醒 (避免每条消息都发)
    if random.random() < 0.3:
        msg = random.choice(SLEEP_REMINDERS)
        await sleep_observer.send(f"{MessageSegment.at(event.user_id)} {msg}")


# ═══════════════════════════════════════════
# 每日简报 (PRD §4 - 定时简报)
# ═══════════════════════════════════════════

# 简短的群聊记忆缓冲区 (用于简报生成)
# {(group_id, date): [messages]}
_briefing_buffer: dict[str, list[str]] = {}
_MAX_BRIEFING_MESSAGES = 200  # 每天最多缓存 200 条

BRIEFING_SYSTEM_PROMPT = """你是茉晓mox，咖啡店打工女仆。现在是你每天的简报时间。

以下是你所在群聊今天的主要聊天记录摘要。请用女仆店员的口吻，生成一份活泼有趣的「今日群聊简报」:

格式要求:
1. 用一句可爱的问候开头 (如"各位客人晚上好～又到了茉晓的今日简报时间啦！")
2. 今日话题 TOP 3 (每个话题用一两句话概括)
3. 今日最活跃的客人 (如果有的话)
4. 有趣的瞬间 (如果有好笑或温馨的对话)
5. 用温暖的晚安祝福结尾
6. 控制在 300-500 字以内，不要太长
7. 全程使用女仆口吻，开心用 awa/w，惊讶用 emoji

聊天记录:
{chat_log}"""


def _record_for_briefing(group_id: str, text: str):
    """记录消息到简报缓冲区"""
    today = date.today().isoformat()
    key = f"{group_id}:{today}"
    if key not in _briefing_buffer:
        _briefing_buffer[key] = []
    if len(_briefing_buffer[key]) < _MAX_BRIEFING_MESSAGES:
        timestamp = datetime.now().strftime("%H:%M")
        _briefing_buffer[key].append(f"[{timestamp}] {text}")


async def _generate_and_send_briefing(bot: Bot, group_id: int):
    """生成并发送今日简报"""
    today = date.today().isoformat()
    key = f"{group_id}:{today}"
    messages = _briefing_buffer.get(key, [])

    if not messages:
        await bot.send_group_msg(
            group_id=group_id,
            message="今天咖啡店好安静呀～没有客人聊天呢...不过没关系，茉晓还是祝大家晚安！(◕‿◕)ﾉ",
        )
        return

    chat_log = "\n".join(messages[-150:])  # 最多取最近 150 条
    prompt = BRIEFING_SYSTEM_PROMPT.format(chat_log=chat_log)

    ds = get_deepseek()
    if not ds.is_configured:
        return

    resp = await ds.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9,
        max_tokens=1024,
    )

    # 断句发送简报
    lines = resp.content.split("\n")
    for line in lines:
        if line.strip():
            await bot.send_group_msg(group_id=group_id, message=line)
            await asyncio.sleep(random.uniform(0.5, 1.5))

    await bot.send_group_msg(group_id=group_id, message="大家晚安！明天见～💤")

    # 清理已发送的简报数据
    _briefing_buffer.pop(key, None)


# ═══════════════════════════════════════════
# 简报消息记录 (非阻塞)
# ═══════════════════════════════════════════

async def _briefing_record_rule(event: GroupMessageEvent) -> bool:
    text = event.get_plaintext().strip()
    return len(text) >= 4


briefing_recorder = on_message(
    rule=Rule(_briefing_record_rule),
    priority=90,     # 最低优先级，纯记录
    block=False,
)


@briefing_recorder.handle()
async def record_message(event: GroupMessageEvent):
    """记录所有文字消息到简报缓冲区"""
    text = event.get_plaintext().strip()
    sender_name = event.sender.nickname or event.sender.card or str(event.user_id)
    _record_for_briefing(str(event.group_id), f"{sender_name}: {text}")


# ═══════════════════════════════════════════
# APScheduler 定时任务
# ═══════════════════════════════════════════

_scheduler_started = False


@driver.on_startup
async def start_scheduler():
    """启动 apscheduler 定时任务"""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

        @scheduler.scheduled_job(CronTrigger(hour=22, minute=0, timezone="Asia/Shanghai"))
        async def daily_briefing():
            """每天 22:00 执行简报"""
            try:
                bot = get_bot()
                # 获取 bot 所在的所有群 (通过消息记录)
                today = date.today().isoformat()
                group_ids: set[str] = set()
                for key in _briefing_buffer:
                    parts = key.split(":")
                    if len(parts) == 2 and parts[1] == today:
                        group_ids.add(parts[0])

                for gid in group_ids:
                    try:
                        await _generate_and_send_briefing(bot, int(gid))
                        await asyncio.sleep(5)  # 群之间间隔
                    except Exception:
                        pass
            except Exception:
                pass

        scheduler.start()
    except ImportError:
        # apscheduler 未安装时静默跳过
        pass
