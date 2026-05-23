"""
塔罗牌占卜插件
==============
严格按 PRD §5 实现多轮对话状态机:
  签到 → 消耗机会 → Q1 → Q2 → Q3 → 抽牌 → AI 解读
"""

import re
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Bot
from nonebot.rule import Rule

from mox.tarot_state import get_tarot_machine
from mox.api_client import get_deepseek
from mox.token_manager import get_token_manager
from mox.sender import MessageSender

tarot_machine = get_tarot_machine()

# ═══════════════════════════════════════════
# 签到关键词
# ═══════════════════════════════════════════

SIGN_IN_KEYWORDS = [
    "签到", "打卡", "报到", "来了", "到店", "进店",
    "今日签到", "每日签到", "sign in", "check in",
]

TAROT_REQUEST_KEYWORDS = [
    "占卜", "塔罗", "算一卦", "占一卦", "帮我算",
    "塔罗牌", "算一下", "占一下", "测一下", "算算",
    "占星", "算命", "测运势", "看看运势", "运势",
]


def _is_sign_in(text: str) -> bool:
    return any(kw in text for kw in SIGN_IN_KEYWORDS)


def _is_tarot_request(text: str) -> bool:
    return any(kw in text for kw in TAROT_REQUEST_KEYWORDS)


# ═══════════════════════════════════════════
# 塔罗解读 System Prompt
# ═══════════════════════════════════════════

TAROT_READING_PROMPT = """你是茉晓mox，咖啡店打工的17岁女仆。你正在为一位客人做塔罗牌占卜。

抽到的牌是: {card_name} ({card_en}) [{direction}]
牌面含义: {meaning}

客人对3个问题的回答:
1. 困扰/想问的事: {q1}
2. 现在的心情: {q2}
3. 希望得到的指引: {q3}

请用女仆店员的口吻，为客人解读这张牌。要求:
1. 先介绍今天抽到的牌 (名字、正逆位、基本含义)
2. 结合客人的3个回答，给出针对性的解读和建议
3. 用活泼可爱的语气，开心时用awa/w颜文字，惊讶用emoji
4. 最后给客人一句温暖的祝福
5. 整个解读在300-500字之间，不要太长"""


# ═══════════════════════════════════════════
# 消息规则: 塔罗相关 OR 正在会话中
# ═══════════════════════════════════════════

async def _tarot_rule(event: GroupMessageEvent) -> bool:
    text = event.get_plaintext().strip()
    user_id = str(event.user_id)
    group_id = str(event.group_id)

    # 处于塔罗会话中 → 拦截
    if tarot_machine.is_in_session(user_id, group_id):
        return True

    # 签到关键词
    if _is_sign_in(text):
        return True

    # 占卜请求关键词
    if _is_tarot_request(text):
        return True

    return False


tarot_handler = on_message(
    rule=Rule(_tarot_rule),
    priority=40,   # 高于 chat_handler (50)
    block=True,    # 拦截，不继续到 chat_handler
)


@tarot_handler.handle()
async def handle_tarot(event: GroupMessageEvent, bot: Bot):
    text = event.get_plaintext().strip()
    user_id = str(event.user_id)
    group_id = str(event.group_id)
    sender = MessageSender(bot)

    # ── 1. 会话中 → 处理回答 ──
    if tarot_machine.is_in_session(user_id, group_id):
        # 检查是否想退出
        if any(kw in text for kw in ("算了", "不占了", "取消", "不要了", "下次")):
            tarot_machine.cancel_session(user_id, group_id)
            await tarot_handler.send("好的客人～那等你想占卜的时候再来找茉晓哦 (。・ω・。)")
            return

        # 提交回答
        result = await tarot_machine.answer(user_id, group_id, text)
        if result is None:
            await tarot_handler.send("诶？茉晓有点迷糊了...客人我们重新来过好不好？(；′⌒`)")
            tarot_machine.cancel_session(user_id, group_id)
            return

        if result["type"] == "question":
            # 下一个问题
            await tarot_handler.send(result["content"])

        elif result["type"] == "reading":
            # 抽牌完成 → 发解读
            card = result["card"]
            answers = result["answers"]

            # 先发抽牌结果
            direction_str = "逆位 ⤓" if card["is_reversed"] else "正位 ⤒"
            card_preview = (
                f"{card['emoji']} 抽到了！\n"
                f"「{card['name_cn']}」({card['name_en']})\n"
                f"方向: {direction_str}\n"
                f"让茉晓来给客人解读一下～"
            )
            await tarot_handler.send(card_preview)

            # 构建解读 prompt
            prompt = TAROT_READING_PROMPT.format(
                card_name=card["name_cn"],
                card_en=card["name_en"],
                direction=card["direction"],
                meaning=card["meaning"],
                q1=answers["q1"],
                q2=answers["q2"],
                q3=answers["q3"],
            )

            # 调用 DeepSeek 解读
            ds = get_deepseek()
            resp = await ds.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=1.0,
                max_tokens=1024,
            )

            # 记录花费
            if resp.tokens_used > 0:
                await get_token_manager().record_spending(
                    user_id, group_id, resp.tokens_used, resp.model, is_input=False
                )

            # 断句发送解读结果
            await sender.send_long_message(group_id=event.group_id, text=resp.content)

        return

    # ── 2. 签到 ──
    if _is_sign_in(text) and not _is_tarot_request(text):
        chances = await tarot_machine.sign_in(user_id, group_id)
        if chances >= 1:
            await tarot_handler.send(
                f"欢迎光临～签到成功！今天你有 {chances} 次占卜机会哦 (◕‿◕)ﾉ\n"
                f"想占卜的话直接跟茉晓说「占卜」就好啦～"
            )
        else:
            await tarot_handler.send(f"签到成功～今天已累计 {chances} 次机会啦！")
        return

    # ── 3. 占卜请求 ──
    if _is_tarot_request(text):
        # 先检查是否有机会
        chances = await tarot_machine.get_chances(user_id, group_id)

        if chances <= 0:
            # 今天还没签到
            await tarot_handler.send(
                "啊，客人今天还没有占卜机会呢～\n"
                "先「签到」一下，茉晓就帮你占卜！每天可以免费占卜一次哦 awa"
            )
            return

        # 已有会话中？
        if tarot_machine.is_in_session(user_id, group_id):
            await tarot_handler.send("我们现在正在占卜中哦～请先回答茉晓刚才问的问题吧 (。・ω・。)")
            return

        # 开始占卜会话
        q1 = await tarot_machine.start_session(user_id, group_id)
        if q1 is None:
            await tarot_handler.send("唔...出了点小问题，客人稍等再试试？")
            return

        # 发送第一个问题
        await tarot_handler.send(
            f"好的客人！那茉晓要开始占卜啦～\n"
            f"机会 -1，还剩 {chances - 1} 次 (｡･ω･｡)\n\n"
            f"{q1}"
        )
