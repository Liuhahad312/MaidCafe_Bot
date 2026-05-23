"""
纯异步 SQLite 数据库层
========================
管理所有持久化数据表: users / user_memories / blacklist / tarot_chances
"""

import json
import aiosqlite
from pathlib import Path
from typing import Optional
from datetime import date

import yaml

# 从 config.yaml 读取数据库路径
_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
_DB_PATH = Path("data/mox.db")

if _CONFIG_PATH.exists():
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        _cfg = yaml.safe_load(f)
    _db_cfg = _cfg.get("database", {}) if _cfg else {}
    _DB_PATH = Path(_db_cfg.get("path", "data/mox.db"))


async def get_db() -> aiosqlite.Connection:
    """获取数据库连接 (WAL 模式 + 外键约束)"""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(_DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


# ═══════════════════════════════════════════
# 初始化
# ═══════════════════════════════════════════

async def init_db():
    """初始化全部数据表 (幂等)"""
    db = await get_db()
    try:
        await db.executescript("""
            -- =============================================
            -- users: 用户核心表
            -- 记录每个群内每个用户的身份、Token消耗、禁言次数等
            -- =============================================
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_id           TEXT    NOT NULL,
                group_id        TEXT    NOT NULL,
                nickname        TEXT    DEFAULT '',
                role            TEXT    DEFAULT 'member',
                title           TEXT    DEFAULT '',
                title_vip       INTEGER DEFAULT 0,
                token_spent     REAL    DEFAULT 0.0,
                gag_count       INTEGER DEFAULT 0,
                vip_votes_received INTEGER DEFAULT 0,
                gag_reset_month TEXT    DEFAULT '',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(qq_id, group_id)
            );

            CREATE INDEX IF NOT EXISTS idx_users_qq_id    ON users(qq_id);
            CREATE INDEX IF NOT EXISTS idx_users_group_id ON users(group_id);
            CREATE INDEX IF NOT EXISTS idx_users_role     ON users(role);

            -- =============================================
            -- user_memories: 长线喜好记忆表
            -- 存储 JSON 格式的偏好/雷点/性格笔记
            -- =============================================
            CREATE TABLE IF NOT EXISTS user_memories (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_id               TEXT    NOT NULL,
                group_id            TEXT    NOT NULL,
                preferences         TEXT    DEFAULT '[]',
                dislikes            TEXT    DEFAULT '[]',
                personality_notes   TEXT    DEFAULT '',
                interaction_count   INTEGER DEFAULT 0,
                last_interaction    TIMESTAMP,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(qq_id, group_id)
            );

            CREATE INDEX IF NOT EXISTS idx_memories_qq_id ON user_memories(qq_id);

            -- =============================================
            -- blacklist: 不受欢迎名单 (永久黑名单)
            -- 仅老板 1035585165 可操作
            -- =============================================
            CREATE TABLE IF NOT EXISTS blacklist (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_id       TEXT    NOT NULL UNIQUE,
                group_id    TEXT    DEFAULT '',
                reason      TEXT    DEFAULT '',
                added_by    TEXT    NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_blacklist_qq_id ON blacklist(qq_id);

            -- =============================================
            -- tarot_chances: 今日塔罗牌占卜剩余次数
            -- 每人每天 1 次机会，签到后获得
            -- =============================================
            CREATE TABLE IF NOT EXISTS tarot_chances (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_id             TEXT    NOT NULL,
                group_id          TEXT    NOT NULL,
                date              TEXT    NOT NULL,
                chances_remaining INTEGER DEFAULT 1,
                used_at           TIMESTAMP,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(qq_id, group_id, date)
            );

            CREATE INDEX IF NOT EXISTS idx_tarot_qq_date ON tarot_chances(qq_id, date);
        """)
        await db.commit()
    finally:
        await db.close()


# ═══════════════════════════════════════════
# users 表操作
# ═══════════════════════════════════════════

async def upsert_user(
    qq_id: str,
    group_id: str,
    nickname: str = "",
    role: str = "member",
    title: str = "",
) -> None:
    """插入或更新用户信息 (每次发消息时调用，保持身份信息最新)"""
    title_vip = 1 if title else 0
    db = await get_db()
    try:
        await db.execute("""
            INSERT INTO users (qq_id, group_id, nickname, role, title, title_vip, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(qq_id, group_id) DO UPDATE SET
                nickname  = CASE WHEN excluded.nickname != '' THEN excluded.nickname ELSE nickname END,
                role      = excluded.role,
                title     = excluded.title,
                title_vip = excluded.title_vip,
                updated_at = CURRENT_TIMESTAMP
        """, [qq_id, group_id, nickname, role, title, title_vip])
        await db.commit()
    finally:
        await db.close()


async def get_user(qq_id: str, group_id: str) -> Optional[dict]:
    """获取单个用户在指定群的信息"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM users WHERE qq_id = ? AND group_id = ?",
            [qq_id, group_id]
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_user_global(qq_id: str) -> list[dict]:
    """获取用户在所有群的信息"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM users WHERE qq_id = ?", [qq_id]
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def add_token_spent(qq_id: str, group_id: str, amount_usd: float) -> None:
    """累加 Token 花费 (美元)"""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE users SET token_spent = token_spent + ?, updated_at = CURRENT_TIMESTAMP WHERE qq_id = ? AND group_id = ?",
            [amount_usd, qq_id, group_id]
        )
        await db.commit()
    finally:
        await db.close()


async def get_daily_token_spent(qq_id: str, group_id: str) -> float:
    """获取今日 Token 花费 (按 updated_at 粗略估算当日值)"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT token_spent FROM users WHERE qq_id = ? AND group_id = ?",
            [qq_id, group_id]
        )
        row = await cursor.fetchone()
        return row["token_spent"] if row else 0.0
    finally:
        await db.close()


async def increment_gag_count(qq_id: str, group_id: str) -> int:
    """禁言次数 +1，返回本月累计次数 (自动跨月重置)"""
    current_month = date.today().strftime("%Y-%m")
    db = await get_db()
    try:
        user = await get_user(qq_id, group_id)
        if not user:
            return 0
        if user.get("gag_reset_month") != current_month:
            await db.execute(
                "UPDATE users SET gag_count = 1, gag_reset_month = ?, updated_at = CURRENT_TIMESTAMP WHERE qq_id = ? AND group_id = ?",
                [current_month, qq_id, group_id]
            )
        else:
            await db.execute(
                "UPDATE users SET gag_count = gag_count + 1, updated_at = CURRENT_TIMESTAMP WHERE qq_id = ? AND group_id = ?",
                [qq_id, group_id]
            )
        await db.commit()

        cursor = await db.execute(
            "SELECT gag_count FROM users WHERE qq_id = ? AND group_id = ?",
            [qq_id, group_id]
        )
        row = await cursor.fetchone()
        return row["gag_count"] if row else 0
    finally:
        await db.close()


async def add_vip_vote(qq_id: str, group_id: str) -> int:
    """贵客投票 +1，返回当前票数"""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE users SET vip_votes_received = vip_votes_received + 1, updated_at = CURRENT_TIMESTAMP WHERE qq_id = ? AND group_id = ?",
            [qq_id, group_id]
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT vip_votes_received FROM users WHERE qq_id = ? AND group_id = ?",
            [qq_id, group_id]
        )
        row = await cursor.fetchone()
        return row["vip_votes_received"] if row else 0
    finally:
        await db.close()


async def reset_daily_token_all() -> None:
    """每日零点重置所有用户的 token_spent"""
    db = await get_db()
    try:
        await db.execute("UPDATE users SET token_spent = 0.0, updated_at = CURRENT_TIMESTAMP")
        await db.commit()
    finally:
        await db.close()


# ═══════════════════════════════════════════
# user_memories 表操作
# ═══════════════════════════════════════════

async def get_or_create_memory(qq_id: str, group_id: str) -> dict:
    """获取或创建用户记忆"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM user_memories WHERE qq_id = ? AND group_id = ?",
            [qq_id, group_id]
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        await db.execute(
            "INSERT INTO user_memories (qq_id, group_id) VALUES (?, ?)",
            [qq_id, group_id]
        )
        await db.commit()
        return {
            "id": None, "qq_id": qq_id, "group_id": group_id,
            "preferences": "[]", "dislikes": "[]",
            "personality_notes": "", "interaction_count": 0,
            "last_interaction": None, "created_at": None, "updated_at": None
        }
    finally:
        await db.close()


async def update_memory_preferences(
    qq_id: str,
    group_id: str,
    preferences: list[str] | None = None,
    dislikes: list[str] | None = None,
    personality_notes: str | None = None,
) -> None:
    """更新用户喜好/雷点/性格笔记"""
    db = await get_db()
    try:
        await get_or_create_memory(qq_id, group_id)

        updates = []
        params = []
        if preferences is not None:
            updates.append("preferences = ?")
            params.append(json.dumps(preferences, ensure_ascii=False))
        if dislikes is not None:
            updates.append("dislikes = ?")
            params.append(json.dumps(dislikes, ensure_ascii=False))
        if personality_notes is not None:
            updates.append("personality_notes = ?")
            params.append(personality_notes)

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.extend([qq_id, group_id])
            await db.execute(
                f"UPDATE user_memories SET {', '.join(updates)} WHERE qq_id = ? AND group_id = ?",
                params
            )
            await db.commit()
    finally:
        await db.close()


async def record_interaction(qq_id: str, group_id: str) -> None:
    """记录一次互动 (计数 + 更新时间)"""
    db = await get_db()
    try:
        await get_or_create_memory(qq_id, group_id)
        await db.execute(
            "UPDATE user_memories SET interaction_count = interaction_count + 1, last_interaction = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE qq_id = ? AND group_id = ?",
            [qq_id, group_id]
        )
        await db.commit()
    finally:
        await db.close()


# ═══════════════════════════════════════════
# blacklist 表操作
# ═══════════════════════════════════════════

async def add_to_blacklist(qq_id: str, added_by: str, group_id: str = "", reason: str = "") -> bool:
    """将用户加入黑名单 (仅老板可调用，返回 True=新增 / False=已在名单中)"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id FROM blacklist WHERE qq_id = ?", [qq_id]
        )
        if await cursor.fetchone():
            return False
        await db.execute(
            "INSERT INTO blacklist (qq_id, group_id, reason, added_by) VALUES (?, ?, ?, ?)",
            [qq_id, group_id, reason, added_by]
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def remove_from_blacklist(qq_id: str) -> bool:
    """从黑名单移除"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "DELETE FROM blacklist WHERE qq_id = ?", [qq_id]
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def is_blacklisted(qq_id: str) -> bool:
    """检查用户是否在黑名单中"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id FROM blacklist WHERE qq_id = ?", [qq_id]
        )
        row = await cursor.fetchone()
        return row is not None
    finally:
        await db.close()


async def get_full_blacklist() -> list[dict]:
    """获取完整黑名单列表"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM blacklist ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


# ═══════════════════════════════════════════
# tarot_chances 表操作
# ═══════════════════════════════════════════

async def get_tarot_chances(qq_id: str, group_id: str) -> int:
    """获取今日剩余占卜次数"""
    today = date.today().isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT chances_remaining FROM tarot_chances WHERE qq_id = ? AND group_id = ? AND date = ?",
            [qq_id, group_id, today]
        )
        row = await cursor.fetchone()
        return row["chances_remaining"] if row else 0
    finally:
        await db.close()


async def grant_tarot_chance(qq_id: str, group_id: str) -> int:
    """签到授予今日 1 次占卜机会，返回当前剩余次数"""
    today = date.today().isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, chances_remaining FROM tarot_chances WHERE qq_id = ? AND group_id = ? AND date = ?",
            [qq_id, group_id, today]
        )
        row = await cursor.fetchone()
        if row:
            await db.execute(
                "UPDATE tarot_chances SET chances_remaining = chances_remaining + 1 WHERE id = ?",
                [row["id"]]
            )
        else:
            await db.execute(
                "INSERT INTO tarot_chances (qq_id, group_id, date, chances_remaining) VALUES (?, ?, ?, 1)",
                [qq_id, group_id, today]
            )
        await db.commit()

        cursor = await db.execute(
            "SELECT chances_remaining FROM tarot_chances WHERE qq_id = ? AND group_id = ? AND date = ?",
            [qq_id, group_id, today]
        )
        row = await cursor.fetchone()
        return row["chances_remaining"] if row else 0
    finally:
        await db.close()


async def consume_tarot_chance(qq_id: str, group_id: str) -> bool:
    """消耗一次占卜机会，返回是否成功"""
    today = date.today().isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, chances_remaining FROM tarot_chances WHERE qq_id = ? AND group_id = ? AND date = ?",
            [qq_id, group_id, today]
        )
        row = await cursor.fetchone()
        if not row or row["chances_remaining"] <= 0:
            return False

        await db.execute(
            "UPDATE tarot_chances SET chances_remaining = chances_remaining - 1, used_at = CURRENT_TIMESTAMP WHERE id = ?",
            [row["id"]]
        )
        await db.commit()
        return True
    finally:
        await db.close()
