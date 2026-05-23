"""
语义雷达 (PRD §4)
=================
低频抽取群聊语义，主动介入:
- 负面情绪 (被骂/运气差/烦恼等) → 主动 @ 安抚 + 推销塔罗占卜
- 吃瓜求证 (真的假的/不可能等) → 主动插话 + 提议去问 Grok 哥哥

非阻塞观察者模式: 不拦截消息，仅在匹配时补充性插话。
"""

import re
import random
import time
from collections import defaultdict

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.rule import Rule

from mox.middleware import get_current_user_id, get_current_group_id
from mox.token_manager import get_token_manager

# ═══════════════════════════════════════════
# 冷却追踪 (防止刷屏)
# ═══════════════════════════════════════════

# 每个用户每种雷达的冷却时间 (秒)
_RADAR_COOLDOWN = {
    "negative": 900,    # 负面情绪: 15 分钟
    "factcheck": 600,   # 吃瓜求证: 10 分钟
}

# 上次触发时间 {(group_id, user_id, radar_type): timestamp}
_last_trigger: dict[str, float] = {}


def _can_trigger(group_id: str, user_id: str, radar_type: str) -> bool:
    """检查是否已过冷却期"""
    key = f"{group_id}:{user_id}:{radar_type}"
    now = time.time()
    if key in _last_trigger:
        if now - _last_trigger[key] < _RADAR_COOLDOWN.get(radar_type, 600):
            return False
    _last_trigger[key] = now
    return True


# ═══════════════════════════════════════════
# 负面情绪模式 (PRD §4 - 语义雷达)
# ═══════════════════════════════════════════

NEGATIVE_EMOTION_PATTERNS = [
    # 被骂/被针对
    r"(被骂|被喷|被怼|被针对|被欺负|被冤枉|受委屈)",
    r"(有人骂我|他们说我|大家都在说|都在骂)",
    # 运气差
    r"(运气.*(差|不好|背|烂)|倒霉|水逆|诸事不顺|太衰了)",
    r"(怎么.*这么.*(惨|倒霉|背|衰))",
    # 烦恼/焦虑
    r"(好烦|烦死了|烦躁|真烦|太烦了|烦人|闹心)",
    r"(焦虑|焦躁|不安|心慌|压力.*大|好累|心累)",
    # 难过/伤心
    r"(难过|伤心|想哭|哭了|好想哭|泪目|呜呜|心痛)",
    r"(崩溃|撑不下去了|受不了了|不想.*(活|在|努力))",
    r"(绝望|无望|没希望|放弃.*算了|算了.*放弃)",
    # 孤独
    r"(孤独|寂寞|一个人|没人.*(理|陪|懂|关心))",
    # 叹气
    r"(唉|哎|叹气).*$",
    r"^(唉|哎|叹气)",
    # 自暴自弃
    r"(我就是.*(没用|废物|垃圾|不行|差劲))",
    r"(什么都.*做不好|什么都.*不会|自己真.*(没用|差|笨))",
]

# 安抚话术池
COMFORT_MESSAGES = [
    "摸摸～自己自暴自弃是不行的，要不要茉晓给你占卜一下？说不定塔罗牌能给你一些指引呢 (。・ω・。)",
    "啊...听起来客人现在心情不太好呢。要不要让茉晓帮你占卜一下？免费的哦～",
    "茉晓虽然不太懂那些复杂的事情，但是可以帮你算一卦塔罗牌！客人要试试看吗 awa",
    "客人别难过啦～来来来，让茉晓给你倒杯虚拟咖啡，再顺便占卜一下运势！(◕‿◕)ﾉ",
    "诶...闻到负面情绪的味道了！茉晓这里有免费的塔罗占卜，说不定能帮客人转运哦～",
]


# ═══════════════════════════════════════════
# 吃瓜求证模式 (PRD §4 - 语义雷达)
# ═══════════════════════════════════════════

FACT_CHECK_PATTERNS = [
    r"(真的假的|真的吗|真的|确定吗|确定|你确定)",
    r"(不可能|绝不可能|怎么会|这也太|太假了|假的吧)",
    r"(难以置信|不敢相信|匪夷所思|天哪|omg)",
    r"(这是.*(真的|假的|谣言))",
    r"(求证|核实|辟谣|有没有.*(这回事|这种事))",
    r"(新闻.*(真的|假的)|消息.*可靠)",
    r"(听说|据说|有人说|网上说).*(真的|假的|是不是)",
    r"(到底.*(真的假的|是不是真的|怎么回事))",
]

# 吃瓜回应话术池
FACT_CHECK_MESSAGES = [
    "确实，根据我的经验这不太可能...要不我去问一下 grok 哥哥？他这方面比较厉害！",
    "诶这个茉晓也不太确定呢，要不要我让 grok 哥哥帮你查查？他可是联网搜索小能手！",
    "客人等等！这种问题茉晓也不太懂...不如我去求求 grok 哥哥帮忙核实一下？",
    "唔...茉晓只是个咖啡店店员不太懂这些，但是可以帮你问问 grok 哥哥！要我去查吗？",
    "啊这个！茉晓也很好奇！要不要我帮你问问 grok 哥哥？他懂得可多了～",
]


# ═══════════════════════════════════════════
# 检测函数
# ═══════════════════════════════════════════

def _detect_negative(text: str) -> bool:
    """检测负面情绪"""
    for pattern in NEGATIVE_EMOTION_PATTERNS:
        if re.search(pattern, text):
            return True
    return False


def _detect_fact_check(text: str) -> bool:
    """检测吃瓜求证意图"""
    for pattern in FACT_CHECK_PATTERNS:
        if re.search(pattern, text):
            return True
    return False


# ═══════════════════════════════════════════
# 雷达消息规则
# ═══════════════════════════════════════════

async def _radar_rule(event: GroupMessageEvent) -> bool:
    """雷达触发规则: 有负面情绪或吃瓜意图"""
    text = event.get_plaintext().strip()
    if len(text) < 4:
        return False
    return _detect_negative(text) or _detect_fact_check(text)


radar = on_message(
    rule=Rule(_radar_rule),
    priority=60,     # 低优先级，在所有业务插件之后
    block=False,     # 不阻塞，观察者模式
)


@radar.handle()
async def handle_radar(event: GroupMessageEvent):
    """
    语义雷达主逻辑。

    因为是 block=False，消息可能已被 chat_handler 等处理过。
    这里仅做补充性插话。
    """
    text = event.get_plaintext().strip()
    user_id = str(event.user_id)
    group_id = str(event.group_id)
    tm = get_token_manager()

    # 已下班时不插话 (避免打扰)
    if tm.is_off_duty():
        return

    at_user = MessageSegment.at(event.user_id)

    # ── 负面情绪雷达 ──
    if _detect_negative(text):
        if not _can_trigger(group_id, user_id, "negative"):
            return
        msg = random.choice(COMFORT_MESSAGES)
        await radar.send(f"{at_user} {msg}")

    # ── 吃瓜求证雷达 ──
    elif _detect_fact_check(text):
        if not _can_trigger(group_id, user_id, "factcheck"):
            return
        msg = random.choice(FACT_CHECK_MESSAGES)
        await radar.send(f"{at_user} {msg}")
