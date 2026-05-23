"""
塔罗牌多轮对话状态机
====================
严格按照 PRD §5 实现:
1. 客人用自然语言"签到" → 获得 1 次占卜机会
2. 消耗机会 → 进入多轮挂起状态
3. 茉晓依次询问 3 个问题 (等客人回答)
4. 收集完毕 → 随机抽牌 (22张大阿尔卡纳 + 正逆位)
5. 3 个答案 + 牌面 → DeepSeek 生成女仆风占卜报告
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .database import get_tarot_chances, grant_tarot_chance, consume_tarot_chance


# ═══════════════════════════════════════════
# 22 张大阿尔卡纳 (Major Arcana)
# ═══════════════════════════════════════════

@dataclass
class TarotCard:
    id: int
    name_cn: str
    name_en: str
    upright: str    # 正位含义
    reversed_desc: str  # 逆位含义
    emoji: str


MAJOR_ARCANA: list[TarotCard] = [
    TarotCard(0,  "愚者",   "The Fool",       "新的开始、冒险、天真、无限可能", "鲁莽、轻率、错失良机、愚蠢的决定", "🌟"),
    TarotCard(1,  "魔术师", "The Magician",    "创造力、技能、自信、掌控局面", "滥用才能、欺骗、计划受阻", "🎩"),
    TarotCard(2,  "女祭司", "The High Priestess", "直觉、神秘、内在智慧、耐心", "忽视直觉、秘密被揭露、肤浅", "🌙"),
    TarotCard(3,  "皇后",   "The Empress",     "丰收、母性、美丽、丰饶", "依赖、创造力枯竭、情感匮乏", "👑"),
    TarotCard(4,  "皇帝",   "The Emperor",     "权威、稳定、领导力、秩序", "专制、失控、缺乏纪律", "🏰"),
    TarotCard(5,  "教皇",   "The Hierophant",  "传统、信仰、精神指引、学习", "挑战传统、盲目追随、教条", "⛪"),
    TarotCard(6,  "恋人",   "The Lovers",      "爱情、和谐、重要选择、结合", "分离、不忠、错误的选择", "💕"),
    TarotCard(7,  "战车",   "The Chariot",     "胜利、决心、克服困难、前进", "失控、失败、方向错误", "⚔️"),
    TarotCard(8,  "力量",   "Strength",        "勇气、耐心、内在力量、温柔征服", "软弱、自我怀疑、失控", "🦁"),
    TarotCard(9,  "隐者",   "The Hermit",      "内省、独处、寻求真理、智慧", "孤独、逃避现实、固步自封", "🏔️"),
    TarotCard(10, "命运之轮", "Wheel of Fortune", "转机、命运、循环、好运降临", "厄运、循环停滞、无法改变", "🎡"),
    TarotCard(11, "正义",   "Justice",         "公平、真相、因果报应、平衡", "不公、偏见、逃避责任", "⚖️"),
    TarotCard(12, "倒吊人", "The Hanged Man",   "牺牲、换个角度看世界、等待", "停滞、无谓牺牲、固执", "🙃"),
    TarotCard(13, "死神",   "Death",           "结束、转变、重生、放下过去", "恐惧改变、停滞不前、无法放下", "💀"),
    TarotCard(14, "节制",   "Temperance",      "平衡、调和、耐心、中庸之道", "失衡、过度、缺乏节制", "🌊"),
    TarotCard(15, "恶魔",   "The Devil",       "欲望、束缚、物质主义、诱惑", "解脱、觉醒、打破枷锁", "😈"),
    TarotCard(16, "高塔",   "The Tower",       "突变、崩塌、颠覆、真相揭露", "逃避灾难、恐惧改变、勉强维持", "🗼"),
    TarotCard(17, "星星",   "The Star",        "希望、灵感、疗愈、宁静", "绝望、失去信念、消极", "⭐"),
    TarotCard(18, "月亮",   "The Moon",        "潜意识、幻象、恐惧、迷惑", "真相浮现、克服恐惧、清晰", "🌙"),
    TarotCard(19, "太阳",   "The Sun",         "快乐、成功、活力、清晰", "暂时的阴霾、缺乏活力、延迟成功", "☀️"),
    TarotCard(20, "审判",   "Judgement",       "重生、觉醒、清算、召唤", "自我怀疑、拒绝改变、逃避审判", "📯"),
    TarotCard(21, "世界",   "The World",       "圆满、成就、旅程结束、整合", "未完成、停滞、遗憾", "🌍"),
]


# ═══════════════════════════════════════════
# 塔罗会话状态
# ═══════════════════════════════════════════

class TarotSessionState(Enum):
    WAITING_Q1 = "waiting_q1"
    WAITING_Q2 = "waiting_q2"
    WAITING_Q3 = "waiting_q3"
    GENERATING  = "generating"   # 正在生成解读中


@dataclass
class TarotSession:
    user_id: str
    group_id: str
    state: TarotSessionState = TarotSessionState.WAITING_Q1
    answer1: str = ""
    answer2: str = ""
    answer3: str = ""
    card: Optional[TarotCard] = None
    is_reversed: bool = False
    started_at: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        """5 分钟超时自动结束"""
        return (time.time() - self.started_at) > 300


# 预设 3 个塔罗咨询问题
TAROT_QUESTIONS = [
    "客人请告诉茉晓，你最近遇到了什么困扰，或者想问问塔罗牌什么事情呢？(｡･ω･｡)",
    "那么第二个问题～如果用一个词来形容你现在的心情，会是什么呢？",
    "最后一个问题！你希望塔罗牌给你带来什么样的指引呢？",
]


# ═══════════════════════════════════════════
# 塔罗状态机管理器
# ═══════════════════════════════════════════

class TarotStateMachine:
    """
    管理所有用户的塔罗会话。

    用法:
        tsm = TarotStateMachine()

        # 签到获取机会
        chances = await tsm.sign_in(user_id, group_id)

        # 开始占卜
        q1 = await tsm.start_session(user_id, group_id)  # 返回第一个问题

        # 用户回答 Q1
        q2 = await tsm.answer(user_id, group_id, answer1)

        # 用户回答 Q2
        q3 = await tsm.answer(user_id, group_id, answer2)

        # 用户回答 Q3 → 返回牌面 + 3个答案
        result = await tsm.answer(user_id, group_id, answer3)
    """

    def __init__(self):
        self._sessions: dict[str, TarotSession] = {}
        self._lock = asyncio.Lock()

    def _key(self, user_id: str, group_id: str) -> str:
        return f"{group_id}:{user_id}"

    # ── 签到 ──

    async def sign_in(self, user_id: str, group_id: str) -> int:
        """签到：授予今日 1 次占卜机会，返回当前剩余次数"""
        return await grant_tarot_chance(user_id, group_id)

    async def get_chances(self, user_id: str, group_id: str) -> int:
        """查询今日剩余占卜次数"""
        return await get_tarot_chances(user_id, group_id)

    # ── 会话管理 ──

    async def start_session(self, user_id: str, group_id: str) -> Optional[str]:
        """
        消耗 1 次机会，开始占卜会话，返回第一个问题。
        若没有机会或已在会话中，返回 None。
        """
        key = self._key(user_id, group_id)

        async with self._lock:
            # 检查是否已在会话中
            if key in self._sessions and not self._sessions[key].is_expired:
                return None

            # 消耗机会
            ok = await consume_tarot_chance(user_id, group_id)
            if not ok:
                return None

            # 创建会话
            session = TarotSession(user_id=user_id, group_id=group_id)
            self._sessions[key] = session
            return TAROT_QUESTIONS[0]

    async def answer(self, user_id: str, group_id: str, answer_text: str) -> Optional[dict]:
        """
        接收客人的回答，返回下一步。
        - 前两次返回下一个问题 (str)
        - 第三次返回包含 3 个答案 + 抽牌结果的 dict (准备发 AI 解读)
        - 若没有活动会话返回 None
        """
        key = self._key(user_id, group_id)

        async with self._lock:
            session = self._sessions.get(key)

            if not session or session.is_expired:
                # 清理过期会话
                self._sessions.pop(key, None)
                return None

            if session.state == TarotSessionState.WAITING_Q1:
                session.answer1 = answer_text.strip()
                session.state = TarotSessionState.WAITING_Q2
                return {"type": "question", "content": TAROT_QUESTIONS[1]}

            elif session.state == TarotSessionState.WAITING_Q2:
                session.answer2 = answer_text.strip()
                session.state = TarotSessionState.WAITING_Q3
                return {"type": "question", "content": TAROT_QUESTIONS[2]}

            elif session.state == TarotSessionState.WAITING_Q3:
                session.answer3 = answer_text.strip()
                session.state = TarotSessionState.GENERATING

                # 随机抽牌
                card = random.choice(MAJOR_ARCANA)
                is_reversed = random.random() < 0.5
                session.card = card
                session.is_reversed = is_reversed

                # 构建解读请求
                direction = "逆位" if is_reversed else "正位"
                meaning = card.reversed_desc if is_reversed else card.upright

                result = {
                    "type": "reading",
                    "card": {
                        "name_cn": card.name_cn,
                        "name_en": card.name_en,
                        "direction": direction,
                        "meaning": meaning,
                        "emoji": card.emoji,
                        "is_reversed": is_reversed,
                    },
                    "answers": {
                        "q1": session.answer1,
                        "q2": session.answer2,
                        "q3": session.answer3,
                    },
                }

                # 清理会话
                del self._sessions[key]
                return result

            elif session.state == TarotSessionState.GENERATING:
                return None  # 正在生成中，忽略

            return None

    def cancel_session(self, user_id: str, group_id: str):
        """手动取消会话"""
        key = self._key(user_id, group_id)
        self._sessions.pop(key, None)

    def is_in_session(self, user_id: str, group_id: str) -> bool:
        """检查用户是否在塔罗会话中"""
        key = self._key(user_id, group_id)
        session = self._sessions.get(key)
        if session and session.is_expired:
            self._sessions.pop(key, None)
            return False
        return session is not None

    def get_session(self, user_id: str, group_id: str) -> Optional[TarotSession]:
        """获取当前会话 (用于超时清理等)"""
        key = self._key(user_id, group_id)
        return self._sessions.get(key)

    def cleanup_expired(self):
        """清理所有过期会话"""
        expired = [
            k for k, s in self._sessions.items() if s.is_expired
        ]
        for k in expired:
            del self._sessions[k]


# ═══════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════

_tarot_machine: Optional[TarotStateMachine] = None


def get_tarot_machine() -> TarotStateMachine:
    global _tarot_machine
    if _tarot_machine is None:
        _tarot_machine = TarotStateMachine()
    return _tarot_machine
