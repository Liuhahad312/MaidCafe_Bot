"""
全局阶层解析中间件
==================
拦截每条群消息，获取发送者 role 与 title，
严格按 PRD 第 2 节划分为五级阶层体系，
并将结果存入协程安全的 contextvar 供下游插件读取。
"""

from contextvars import ContextVar

from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.message import event_preprocessor

from .database import upsert_user

# ═══════════════════════════════════════════
# 至高权限 QQ 号 (PRD §2 - 老板/主人)
# ═══════════════════════════════════════════
MASTER_QQ: str = "1035585165"

# 协程安全的上下文变量，供整条消息处理链路读取
_current_hierarchy: ContextVar[str] = ContextVar("mox_hierarchy", default="客人")
_current_honorific: ContextVar[str] = ContextVar("mox_honorific", default="")
_current_group_id: ContextVar[str] = ContextVar("mox_group_id", default="")
_current_user_id: ContextVar[str] = ContextVar("mox_user_id", default="")


# ═══════════════════════════════════════════
# 对外查询接口 (下游插件调用)
# ═══════════════════════════════════════════

def get_hierarchy() -> str:
    """返回当前消息发送者的阶层: 老板 / 主人 / 员工 / 贵客 / 客人"""
    return _current_hierarchy.get()


def get_honorific() -> str:
    """返回当前发送者的专属称呼 (仅对 1035585165 有值)"""
    return _current_honorific.get()


def get_current_group_id() -> str:
    """返回当前群号"""
    return _current_group_id.get()


def get_current_user_id() -> str:
    """返回当前发送者 QQ 号"""
    return _current_user_id.get()


# 便捷判断函数
def is_boss() -> bool:
    """是否为老板 (1035585165 且在本群为群主)"""
    return _current_hierarchy.get() == "老板"


def is_master() -> bool:
    """是否为至高权限者 (老板 或 主人)"""
    return _current_hierarchy.get() in ("老板", "主人")


def is_employee() -> bool:
    """是否为员工 (普通群管理员)"""
    return _current_hierarchy.get() == "员工"


def is_vip() -> bool:
    """是否为贵客 (拥有群专属头衔)"""
    return _current_hierarchy.get() == "贵客"


def is_guest() -> bool:
    """是否为普通客人"""
    return _current_hierarchy.get() == "客人"


# ═══════════════════════════════════════════
# 阶层判定核心逻辑 (PRD §2)
# ═══════════════════════════════════════════

def _resolve_hierarchy(qq_id: str, role: str, title: str) -> tuple[str, str]:
    """
    根据 QQ 号、群角色、群头衔解析阶层与称呼。

    规则 (PRD §2):
    - qq_id == 1035585165 且 role == "owner"  → 老板，称呼「老板」
    - qq_id == 1035585165 且 role != "owner"  → 主人，称呼「主人」
    - role == "admin" 且 qq_id != 1035585165   → 员工 (同事)
    - 有 title (群专属头衔) 且非以上身份        → 贵客
    - 其他                                     → 客人
    """
    if qq_id == MASTER_QQ:
        if role == "owner":
            return ("老板", "老板")
        else:
            return ("主人", "主人")

    if role == "admin":
        return ("员工", "")

    if title:
        return ("贵客", "")

    return ("客人", "")


# ═══════════════════════════════════════════
# NoneBot2 事件预处理器
# ═══════════════════════════════════════════

@event_preprocessor
async def hierarchy_middleware(event: GroupMessageEvent):
    """
    阶层解析中间件 - 在每条群消息被分发至业务插件前执行。

    1. 提取 sender.role / sender.title
    2. 判定五级阶层
    3. 存入 contextvar 供下游插件读取
    4. 异步入库更新用户身份快照
    """
    sender = event.sender
    qq_id = str(sender.user_id)
    role = sender.role or "member"
    title = sender.title or ""
    group_id = str(event.group_id)
    nickname = sender.nickname or sender.card or ""

    # 解析阶层与称呼
    hierarchy, honorific = _resolve_hierarchy(qq_id, role, title)

    # 注入协程安全上下文
    _current_hierarchy.set(hierarchy)
    _current_honorific.set(honorific)
    _current_group_id.set(group_id)
    _current_user_id.set(qq_id)

    # 异步更新数据库用户快照 (不阻塞消息处理)
    await upsert_user(
        qq_id=qq_id,
        group_id=group_id,
        nickname=nickname,
        role=role,
        title=title,
    )
