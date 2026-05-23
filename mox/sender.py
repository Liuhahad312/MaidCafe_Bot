"""
手癌与断句发送器
================
- AI 长文本按 \\n 拆分 → asyncio.sleep 模拟打字逐条发送
- 30% 概率 (本地测试) 用同音错字替换 1-2 个字
- 发送错字后获取 message_id → 等待 1.5s → 撤回 → 补发正确版 + 吐槽
"""

import asyncio
import random
import re
from typing import Optional

from nonebot.adapters.onebot.v11 import Bot

# ═══════════════════════════════════════════
# 同音错字映射表 (手癌核心)
# key = 正确字, value = [可选错别字列表]
# ═══════════════════════════════════════════

HOMOPHONE_TYPOS: dict[str, list[str]] = {
    # 高频字
    "的": ["得", "德", "地"],
    "得": ["的", "德"],
    "地": ["的", "底"],
    "是": ["事", "市", "试"],
    "事": ["是", "市"],
    "我": ["窝", "握", "卧"],
    "你": ["泥", "拟", "尼"],
    "他": ["她", "它", "塔"],
    "她": ["他", "它"],
    "在": ["再", "载"],
    "再": ["在", "载"],
    "不": ["步", "部", "布"],
    "了": ["勒", "乐"],
    "吗": ["嘛", "马", "码"],
    "吧": ["巴", "把", "八"],
    "呢": ["讷", "呐"],
    "很": ["狠", "恨"],
    "都": ["兜", "抖"],
    "会": ["回", "汇", "惠"],
    "能": ["嫩"],
    "要": ["摇", "腰", "耀"],
    "有": ["又", "友", "右"],
    "也": ["夜", "液", "页"],
    "就": ["旧", "救", "舅"],
    "说": ["硕", "烁"],
    "看": ["砍", "刊"],
    "想": ["响", "享", "向"],
    "让": ["嚷", "壤"],
    "做": ["作", "坐", "座"],
    "作": ["做", "坐"],
    "过": ["果", "裹"],
    "还": ["孩", "海"],
    "和": ["合", "河", "盒"],
    "来": ["莱", "赖"],
    "去": ["取", "趣"],
    "到": ["道", "倒"],
    "道": ["到", "倒"],
    "人": ["仁", "任"],
    "大": ["达", "打"],
    "小": ["晓", "笑"],
    "好": ["号", "浩"],
    "可": ["克", "刻", "客"],
    "以": ["已", "椅"],
    "为": ["位", "未", "味"],
    "上": ["尚", "伤"],
    "下": ["夏", "吓"],
    "中": ["钟", "终"],
    "用": ["永", "泳", "勇"],
    "发": ["法", "罚"],
    "面": ["免", "棉"],
    "当": ["挡", "党"],
    "后": ["厚", "候"],
    "前": ["钱", "千"],
    "没": ["美", "每", "煤"],
    "只": ["之", "支"],
    "点": ["店", "电"],
    "吃": ["尺", "迟"],
    "喝": ["合", "河"],
    "玩": ["完", "丸"],
    "看": ["砍", "刊"],
    "听": ["厅", "停"],
    "觉": ["决", "绝", "角"],
    "得": ["的", "德"],
    "知": ["之", "支", "只"],
    "喜": ["洗", "希", "西"],
    "欢": ["环", "缓"],
    "心": ["新", "辛", "芯"],
    "开": ["凯", "揩"],
    "走": ["奏", "揍"],
    "跑": ["泡", "炮"],
    "笑": ["小", "晓", "效"],
    "哭": ["库", "酷"],
    "气": ["期", "七", "妻"],
    "爱": ["碍", "艾"],
    "恨": ["很", "狠"],
    "话": ["化", "画", "划"],
    "名": ["明", "鸣"],
    "字": ["自", "紫"],
    "年": ["念", "粘"],
    "月": ["越", "乐"],
    "日": ["认", "任"],
    "时": ["识", "十", "石"],
    "间": ["见", "件", "健"],
    "家": ["加", "佳", "嘉"],
    "店": ["点", "电", "垫"],
    "咖": ["卡", "喀"],
    "啡": ["非", "飞", "费"],
    "女": ["努", "怒"],
    "仆": ["普", "葡", "蒲"],
    "客": ["可", "克", "刻"],
    "谢": ["写", "血", "协"],
    "对": ["队", "兑", "堆"],
    "起": ["启", "七", "企"],
    "真": ["针", "珍", "侦"],
    "错": ["措", "挫"],
    "等": ["灯", "登"],
    "帮": ["邦", "绑"],
    "忙": ["茫", "芒", "盲"],
    "买": ["卖", "麦"],
    "卖": ["买", "迈"],
    "钱": ["前", "千", "潜"],
    "先": ["现", "线", "鲜"],
    "今": ["金", "斤", "近"],
    "明": ["名", "鸣"],
    "昨": ["作", "左"],
    "每": ["没", "美", "煤"],
    "问": ["文", "闻", "纹"],
    "答": ["达", "打"],
    "聊": ["辽", "疗", "了"],
    "讲": ["奖", "蒋"],
    "请": ["情", "晴", "轻"],
    "慢": ["满", "曼"],
    "快": ["块", "筷"],
    "早": ["找", "澡", "枣"],
    "晚": ["万", "碗", "完"],
}

# 吐槽文案池
TYPO_COMPLAINTS = [
    "啊手滑了😨",
    "呜哇打错字了 awa",
    "刚才不小心手癌了...重来重来 >_<",
    "呜呜键盘不听话！",
    "啊呀呀打错了！重新发一下 (；′⌒`)",
    "手癌犯了对不起！(。・ω・。)",
    "不好意思打错字了啦～",
]


# ═══════════════════════════════════════════
# 错字引擎
# ═══════════════════════════════════════════

def _apply_typo(text: str, num_chars: int = 2) -> str:
    """
    对文本中的 1-2 个中文字符替换为同音错字。
    返回修改后的文本 (不修改原文本)。

    Args:
        text: 原始文本
        num_chars: 最多替换几个字 (实际 1~num_chars 个)
    """
    # 找到所有中文字符的位置
    chinese_positions = [
        (i, c) for i, c in enumerate(text)
        if "一" <= c <= "鿿" and c in HOMOPHONE_TYPOS
    ]

    if not chinese_positions:
        return text  # 没有可替换的中文字

    # 随机选 1~num_chars 个位置
    n = min(random.randint(1, num_chars), len(chinese_positions))
    targets = random.sample(chinese_positions, n)

    # 替换
    chars = list(text)
    for pos, char in targets:
        typo = random.choice(HOMOPHONE_TYPOS[char])
        chars[pos] = typo

    return "".join(chars)


# ═══════════════════════════════════════════
# 断句 + 手癌发送器
# ═══════════════════════════════════════════

class MessageSender:
    """
    智能消息发送器。

    - 长文本按 \\n 拆分逐条发送
    - 模拟人类打字速度 (1-3秒间隔)
    - 30% 概率触发手癌 → 撤回 → 补发
    """

    def __init__(self, bot: Bot, typo_rate: float = 0.3):
        self._bot = bot
        self._typo_rate = typo_rate

    async def send_long_message(
        self,
        group_id: int,
        text: str,
        max_line_length: int = 0,
    ):
        """
        按换行拆分发送长消息。

        Args:
            group_id: 群号
            text: 完整文本 (含 \\n)
            max_line_length: 单行最大长度，0 表示不限制 (但 \\n 始终会拆分)
        """
        lines = text.split("\n")
        # 过滤纯空行但保留有意义空白
        lines = [l for l in lines if l.strip() != "" or l == ""]

        for i, line in enumerate(lines):
            if not line.strip():
                # 纯换行 → 发空行做视觉间隔
                await asyncio.sleep(0.3)
                continue

            await self._send_single_line(group_id, line)

            # 模拟打字间隔 (最后一行不加)
            is_last = (i == len(lines) - 1)
            if not is_last:
                delay = random.uniform(1.0, 3.0)
                await asyncio.sleep(delay)

    async def _send_single_line(self, group_id: int, line: str):
        """发送单条消息，可能触发手癌"""

        # 太短的文本不触发手癌 (如单独的 emoji、标点等)
        chinese_chars = sum(1 for c in line if "一" <= c <= "鿿")
        should_typo = (
            chinese_chars >= 4  # 至少4个中文字
            and random.random() < self._typo_rate
        )

        if not should_typo:
            await self._bot.send_group_msg(group_id=group_id, message=line)
            return

        # --- 手癌分支 ---
        typo_line = _apply_typo(line)

        # 发送错字版
        result = await self._bot.send_group_msg(group_id=group_id, message=typo_line)
        msg_id = _extract_message_id(result)

        # 等待 1.5 秒
        await asyncio.sleep(1.5)

        # 撤回错字
        if msg_id:
            try:
                await self._bot.delete_msg(message_id=msg_id)
            except Exception:
                pass  # 撤回失败不影响后续

        # 补发正确版 + 吐槽
        complaint = random.choice(TYPO_COMPLAINTS)
        correct_msg = f"{line}\n（{complaint}）"
        await self._bot.send_group_msg(group_id=group_id, message=correct_msg)

    async def send_single(self, group_id: int, text: str):
        """发送单条消息 (不拆分)，可能触发手癌"""
        await self._send_single_line(group_id, text)

    async def send_plain(self, group_id: int, text: str):
        """发送纯文本，不触发手癌"""
        await self._bot.send_group_msg(group_id=group_id, message=text)

    @property
    def typo_rate(self) -> float:
        return self._typo_rate

    @typo_rate.setter
    def typo_rate(self, value: float):
        self._typo_rate = max(0.0, min(1.0, value))


def _extract_message_id(result) -> Optional[int]:
    """从 send_group_msg 返回值中提取 message_id"""
    if isinstance(result, dict):
        return result.get("message_id")
    # NoneBot2 可能返回其他类型，尝试属性访问
    if hasattr(result, "message_id"):
        return result.message_id
    return None
