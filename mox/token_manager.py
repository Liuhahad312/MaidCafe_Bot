"""
Token 薪酬与下班拦截机制
========================
- 追踪每日 API 消耗 (换算为美元)
- 每日 $2.00 USD 上限
- 耗尽后宣布"今天下班啦"，拦截普通请求 (仅贵客做极少响应)
- 仅老板 1035585165 可通过自然语言"加工资"临时提高当日限额
"""

import asyncio
import time
from datetime import date
from typing import Optional

import yaml
from pathlib import Path

from .database import add_token_spent, reset_daily_token_all

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def _load_daily_budget() -> float:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        if cfg:
            limits = cfg.get("limits", {})
            return float(limits.get("daily_token_budget_usd", 2.0))
    return 2.0


def _load_pricing() -> dict:
    """从 config.yaml 加载 Token 定价配置"""
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        if cfg:
            return cfg.get("pricing", {})
    return {}

_PRICING_CACHE = None

def _get_pricing() -> dict:
    global _PRICING_CACHE
    if _PRICING_CACHE is None:
        _PRICING_CACHE = _load_pricing()
    return _PRICING_CACHE


def estimate_cost_usd(model: str, tokens: int, is_input: bool = True) -> float:
    """根据模型和 token 数估算花费 (USD) — 从 config.yaml 读取定价"""
    pricing = _get_pricing()
    rate_type = "input" if is_input else "output"

    if "deepseek" in model.lower():
        rate_per_million = float(pricing.get("deepseek", {}).get(rate_type, 0.14))
    elif "grok" in model.lower():
        rate_per_million = float(pricing.get("grok", {}).get(rate_type, 2.0))
    else:
        default_pricing = pricing.get("default", {})
        rate_per_million = float(default_pricing.get(rate_type, 1.0))

    return (tokens / 1_000_000.0) * rate_per_million


# ═══════════════════════════════════════════
# Token 管理器
# ═══════════════════════════════════════════

class TokenManager:
    """
    每日 Token 薪酬管理器。

    - 追踪全局今日花费
    - 每日零点自动重置
    - 老板可"加工资"临时提升当日限额
    """

    def __init__(self):
        self._daily_budget: float = _load_daily_budget()
        self._original_budget: float = self._daily_budget
        self._spent_today: float = 0.0
        self._current_date: str = date.today().isoformat()
        self._is_off_duty: bool = False
        self._salary_raise_expiry: float = 0.0  # 加工资过期时间戳
        self._lock = asyncio.Lock()

    def _check_date_reset(self):
        """检查是否跨天，需要重置"""
        today = date.today().isoformat()
        if today != self._current_date:
            self._spent_today = 0.0
            self._current_date = today
            self._is_off_duty = False
            self._daily_budget = self._original_budget
            self._salary_raise_expiry = 0.0

    async def record_spending(
        self,
        qq_id: str,
        group_id: str,
        tokens: int,
        model: str,
        is_input: bool = True,
    ):
        """记录一次 API 调用花费"""
        async with self._lock:
            self._check_date_reset()
            cost = estimate_cost_usd(model, tokens, is_input)
            self._spent_today += cost

            # 更新数据库中的用户个人花费
            await add_token_spent(qq_id, group_id, cost)

            # 检查是否超出预算
            if self._spent_today >= self._daily_budget and not self._is_off_duty:
                self._is_off_duty = True

    @property
    def spent_today(self) -> float:
        return self._spent_today

    @property
    def daily_budget(self) -> float:
        return self._daily_budget

    @property
    def remaining(self) -> float:
        return max(0.0, self._daily_budget - self._spent_today)

    def is_off_duty(self) -> bool:
        """是否已下班"""
        self._check_date_reset()
        return self._is_off_duty

    async def raise_salary(self, amount_usd: float = 1.0) -> str:
        """
        老板加工资！临时提高当日限额。

        仅老板 1035585165 可调用，外部需做权限验证。
        """
        async with self._lock:
            self._check_date_reset()
            self._daily_budget += amount_usd
            self._is_off_duty = False
            self._salary_raise_expiry = time.time() + 86400  # 24 小时后失效
            return (
                f"谢谢老板！今天的工资涨到 ${self._daily_budget:.2f} 啦～"
                f" 茉晓会继续努力打工的！(◕‿◕)ﾉ"
            )

    def get_status_message(self) -> str:
        """获取当前薪酬状态 (供 System Prompt 或调试)"""
        self._check_date_reset()
        return (
            f"💰 今日预算: ${self._daily_budget:.2f} | "
            f"已花费: ${self._spent_today:.4f} | "
            f"剩余: ${self.remaining:.4f} | "
            f"状态: {'🔴 已下班' if self._is_off_duty else '🟢 营业中'}"
        )


# ═══════════════════════════════════════════
# 下班拦截文案
# ═══════════════════════════════════════════

OFF_DUTY_FULL_MESSAGE = (
    "呜哇...今天的工资已经花完了呢，茉晓要下班啦～(；′⌒`)\n"
    "明天再来找我玩吧！晚安～"
)

OFF_DUTY_VIP_MESSAGE = (
    "啊，是贵客呀...茉晓已经下班了，不过既然是贵客，就破例多陪你聊两句吧～"
    "不过不要太久哦，我还要打扫咖啡店的 (｡・ω・｡)"
)


# ═══════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════

_token_manager: Optional[TokenManager] = None


def get_token_manager() -> TokenManager:
    global _token_manager
    if _token_manager is None:
        _token_manager = TokenManager()
    return _token_manager
