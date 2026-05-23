"""
长线记忆系统
============
- 对话结束后：异步提取客人喜好/雷点 → 存入 user_memories
- 下次对话前：读取记忆 → 注入 System Prompt 实现"千人千面"
"""

import json
import asyncio
from typing import Optional

from .database import (
    get_or_create_memory,
    update_memory_preferences,
    record_interaction,
)
from .api_client import get_deepseek

# 记忆提取的对话缓冲区 {(group_id, user_id): [messages]}
_conversation_buffer: dict[str, list[str]] = {}
_MAX_BUFFER_SIZE = 6  # 攒够 6 条消息触发一次提取


def _conv_key(group_id: str, user_id: str) -> str:
    return f"{group_id}:{user_id}"


# ═══════════════════════════════════════════
# 记忆提取 (对话后异步执行)
# ═══════════════════════════════════════════

def schedule_memory_extraction(
    group_id: str,
    user_id: str,
    user_message: str,
    bot_response: str,
):
    """
    Fire-and-forget 形式调度记忆提取。
    不阻塞主消息流，在后台异步执行。
    """
    key = _conv_key(group_id, user_id)
    if key not in _conversation_buffer:
        _conversation_buffer[key] = []

    _conversation_buffer[key].append(f"客人: {user_message}")
    _conversation_buffer[key].append(f"茉晓: {bot_response}")

    # 攒够一定轮次再提取，避免每次对话都调用 API
    if len(_conversation_buffer[key]) >= _MAX_BUFFER_SIZE:
        # 取出并清空
        conversation = "\n".join(_conversation_buffer[key])
        _conversation_buffer[key] = []
        # 后台执行
        asyncio.create_task(_do_extract_and_save(group_id, user_id, conversation))


async def _do_extract_and_save(
    group_id: str,
    user_id: str,
    conversation: str,
):
    """实际执行偏好提取并更新数据库"""
    ds = get_deepseek()
    if not ds.is_configured:
        return

    try:
        extracted = await ds.extract_preferences(conversation)
        prefs = extracted.get("preferences", [])
        dislikes = extracted.get("dislikes", [])

        if prefs or dislikes:
            # 与旧记忆合并 (新发现追加到已有列表中)
            memory = await get_or_create_memory(user_id, group_id)
            old_prefs = json.loads(memory.get("preferences", "[]"))
            old_dislikes = json.loads(memory.get("dislikes", "[]"))

            merged_prefs = list(set(old_prefs + prefs))
            merged_dislikes = list(set(old_dislikes + dislikes))

            await update_memory_preferences(
                qq_id=user_id,
                group_id=group_id,
                preferences=merged_prefs,
                dislikes=merged_dislikes,
            )
    except Exception:
        pass  # 记忆提取失败不影响主流程


# ═══════════════════════════════════════════
# 记忆注入 (对话前构建上下文)
# ═══════════════════════════════════════════

async def build_memory_context(user_id: str, group_id: str) -> str:
    """
    从数据库读取用户记忆，构建注入 System Prompt 的上下文文本。

    返回格式:
        你记得这位客人喜欢: 咖啡, 猫咪
        你记得这位客人讨厌: 香菜
    """
    await record_interaction(user_id, group_id)

    try:
        memory = await get_or_create_memory(user_id, group_id)
        prefs = json.loads(memory.get("preferences", "[]"))
        dislikes = json.loads(memory.get("dislikes", "[]"))
        notes = memory.get("personality_notes", "")
        count = memory.get("interaction_count", 0)

        parts = []

        if prefs:
            parts.append(f"客人喜欢: {', '.join(prefs[:5])}")
        if dislikes:
            parts.append(f"客人讨厌: {', '.join(dislikes[:5])}")
        if notes:
            parts.append(f"备注: {notes}")

        if count and count >= 10:
            parts.append("这位是常客，你已经很熟悉 ta 了")

        if parts:
            return "## 关于这位客人，你记得:\n" + "\n".join(f"- {p}" for p in parts)

        return ""
    except Exception:
        return ""


async def get_memory_summary(user_id: str, group_id: str) -> dict:
    """获取用户记忆摘要 (供其他模块查询)"""
    try:
        memory = await get_or_create_memory(user_id, group_id)
        return {
            "preferences": json.loads(memory.get("preferences", "[]")),
            "dislikes": json.loads(memory.get("dislikes", "[]")),
            "personality_notes": memory.get("personality_notes", ""),
            "interaction_count": memory.get("interaction_count", 0),
        }
    except Exception:
        return {
            "preferences": [],
            "dislikes": [],
            "personality_notes": "",
            "interaction_count": 0,
        }
