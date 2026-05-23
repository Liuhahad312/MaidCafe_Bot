"""
图文防抖缓冲池
=============
还原真实人类"先发图再打字"的习惯。

收到图片/表情包后，挂起 4 秒倒计时：
- 若 4 秒内同一用户发出文字 → 取消倒计时，将图文合并处理
- 若 4 秒超时未收到文字 → 单独将图片发给 Grok Vision 处理

全程非阻塞、协程安全。
"""

import asyncio
import time
from typing import Callable, Optional, Awaitable

from nonebot.adapters.onebot.v11 import GroupMessageEvent

# 缓冲超时 (秒) — 可通过 config.yaml 配置
DEFAULT_BUFFER_TIMEOUT = 4.0

# 回调类型: (event, merged_text_or_None)
ProcessCallback = Callable[[GroupMessageEvent, Optional[str]], Awaitable[None]]


class ImageBuffer:
    """
    单用户图文缓冲状态。

    - _event: 缓存的图片/表情包事件
    - _task:  正在倒计时的 asyncio Task
    - _arrived_at: 图片到达时间戳
    """

    def __init__(self, event: GroupMessageEvent, task: asyncio.Task, arrived_at: float):
        self.event = event
        self.task = task
        self.arrived_at = arrived_at


class ImageBufferManager:
    """
    图文防抖缓冲池管理器 (全局单例)。

    用法:
        manager = ImageBufferManager(timeout=4.0)
        manager.set_callback(my_process_function)

        # 收到图片时
        await manager.on_image(event)

        # 收到文字时: 返回 (是否被消耗, 缓存的图片事件或None)
        consumed, image_event = await manager.on_text(event)
    """

    def __init__(self, timeout: float = DEFAULT_BUFFER_TIMEOUT):
        self._timeout = timeout
        self._buffers: dict[str, ImageBuffer] = {}
        self._callback: Optional[ProcessCallback] = None

    def set_callback(self, callback: ProcessCallback):
        """设置图片处理回调 (图片超时或图文合并时调用)"""
        self._callback = callback

    def _make_key(self, group_id: str, user_id: str) -> str:
        return f"{group_id}:{user_id}"

    async def on_image(self, event: GroupMessageEvent) -> bool:
        """
        收到图片/表情包消息时调用。

        返回 True 表示图片已存入缓冲等待文字，
        返回 False 表示无回调无法处理。
        """
        if not self._callback:
            return False

        group_id = str(event.group_id)
        user_id = str(event.user_id)
        key = self._make_key(group_id, user_id)

        # 取消该用户之前未完成的倒计时 (覆盖旧图)
        existing = self._buffers.pop(key, None)
        if existing:
            existing.task.cancel()

        # 创建新的非阻塞倒计时
        arrived_at = time.time()
        task = asyncio.create_task(
            self._timeout_handler(key, event, arrived_at)
        )
        self._buffers[key] = ImageBuffer(event, task, arrived_at)
        return True

    async def on_text(self, event: GroupMessageEvent) -> tuple[bool, Optional[GroupMessageEvent]]:
        """
        收到文字消息时调用。

        返回 (consumed, image_event):
          - (True, event): 文字消耗了缓冲区中的图片，返回缓存的图片事件供合并
          - (False, None):  没有待处理的图片，正常文字消息
        """
        group_id = str(event.group_id)
        user_id = str(event.user_id)
        key = self._make_key(group_id, user_id)

        buf = self._buffers.pop(key, None)
        if buf is None:
            return (False, None)

        # 取消倒计时 — 图文合并！
        buf.task.cancel()
        return (True, buf.event)

    async def _timeout_handler(
        self,
        key: str,
        event: GroupMessageEvent,
        arrived_at: float,
    ):
        """4 秒倒计时到期 → 单独处理图片"""
        await asyncio.sleep(self._timeout)

        # 二次确认：这个 key 还对应同一个 buffer (防御性编程)
        buf = self._buffers.pop(key, None)
        if buf is None or buf.arrived_at != arrived_at:
            return  # 已被覆盖或取消

        if self._callback:
            await self._callback(event, None)

    def cancel_user(self, group_id: str, user_id: str):
        """手动取消某用户的缓冲 (如被踢出群)"""
        key = self._make_key(group_id, user_id)
        buf = self._buffers.pop(key, None)
        if buf:
            buf.task.cancel()

    def cleanup(self):
        """清理所有缓冲 (Bot 关闭时调用)"""
        for buf in self._buffers.values():
            buf.task.cancel()
        self._buffers.clear()

    @property
    def pending_count(self) -> int:
        """当前缓冲中的图片数量"""
        return len(self._buffers)


# ═══════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════

_buffer_manager: Optional[ImageBufferManager] = None


def get_image_buffer(timeout: float = DEFAULT_BUFFER_TIMEOUT) -> ImageBufferManager:
    global _buffer_manager
    if _buffer_manager is None:
        _buffer_manager = ImageBufferManager(timeout=timeout)
    return _buffer_manager
