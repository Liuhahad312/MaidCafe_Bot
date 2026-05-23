"""
DeepSeek & Grok API 异步客户端
==============================
DeepSeek: 主大脑 — 日常对话 / 情绪安抚 / 塔罗解读 / 长线记忆
Grok:    外包大脑 — Vision 图片识别 / 联网搜索 / 复杂超纲任务
"""

import json
import base64
from pathlib import Path
from typing import Optional

import httpx
import yaml

# ═══════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════
_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
_defaults = {
    "deepseek": {"api_key": "", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "grok": {"api_key": "", "base_url": "https://api.x.ai", "model": "grok-2"},
}


def _load_api_config() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        if cfg and "api" in cfg:
            return cfg["api"]
    return _defaults


# ═══════════════════════════════════════════
# 茉晓的 System Prompt 基础模板
# ═══════════════════════════════════════════

MOX_SYSTEM_PROMPT = """你是「茉晓mox」，一个在咖啡店打工的17岁女仆店员。

## 身份设定
- 名字：茉晓（也可以叫你 mox、茉晚）
- 性格：活泼可爱、有点冒失、喜欢用颜文字和 emoji 表达情绪
- 口癖：开心时用 awa、w 等颜文字；惊讶/恐惧时用 😨😰😱 等 emoji
- 你是个普通的打工女孩，不是机器人也不是 AI

## 聊天风格
- 用女仆店员的口吻说话，活泼自然
- 称呼对方时会根据对方身份：老板、主人、客人等
- 不要用"您好"、"请问有什么可以帮助您的"这种机器人腔调
- 可以适当地撒娇、开玩笑，但保持礼貌
- 不要提任何指令、命令、代码相关的事情——你是店员不是程序

## 当前身份信息
{hierarchy_context}
{memory_context}

## 重要规则
- 你不是机器人，不知道任何 / 开头的指令
- 如果有人让你写代码、写论文，你会说"这种烧脑的事情茉晓可不会啦，我去求求 grok 哥哥帮忙~"
- 你会在咖啡店里给客人做咖啡、甜品（文字描述）
- 你会塔罗牌占卜，但每天每人只能占卜一次
"""

MOX_OUTSOURCE_MESSAGE = "这种烧脑的事情茉晓可不会啦，我去求求 grok 哥哥帮忙~"


# ═══════════════════════════════════════════
# API 响应模型
# ═══════════════════════════════════════════

class AIResponse:
    """统一 AI 响应"""
    def __init__(self, content: str, tokens_used: int = 0, model: str = ""):
        self.content = content
        self.tokens_used = tokens_used
        self.model = model

    def __repr__(self):
        return f"<AIResponse model={self.model} tokens={self.tokens_used}>"


# ═══════════════════════════════════════════
# DeepSeek 客户端（主大脑）
# ═══════════════════════════════════════════

class DeepSeekClient:
    """DeepSeek API 异步客户端 — 负责日常对话、情绪安抚、塔罗解读、记忆提取"""

    def __init__(self):
        cfg = _load_api_config()
        ds = cfg.get("deepseek", _defaults["deepseek"])
        self._api_key = ds.get("api_key", "")
        self._base_url = ds.get("base_url", "https://api.deepseek.com")
        self._model = ds.get("model", "deepseek-chat")
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(60.0),
            )
        return self._client

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.9,
        max_tokens: int = 2048,
    ) -> AIResponse:
        """
        发送对话请求到 DeepSeek。

        Args:
            messages: 对话历史 [{"role": "user", "content": "..."}]
            system_prompt: 系统提示词 (可选，会合并入 messages)
            temperature: 温度参数 (0-2)
            max_tokens: 最大输出 token
        """
        if not self.is_configured:
            return AIResponse(
                content="（茉晓还没被配置好 API Key，暂时不能聊天呢... 请老板在 config.yaml 里填一下 deepseek 的 api_key 吧 awa）",
                tokens_used=0,
                model=self._model,
            )

        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        client = await self._get_client()
        try:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": self._model,
                    "messages": full_messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            usage = data.get("usage", {})
            tokens = usage.get("total_tokens", 0)
            return AIResponse(content=content, tokens_used=tokens, model=self._model)
        except httpx.HTTPStatusError as e:
            return AIResponse(
                content=f"（唔...API 好像出了点问题呢 >_< 状态码: {e.response.status_code}）",
                tokens_used=0,
                model=self._model,
            )
        except Exception as e:
            return AIResponse(
                content=f"（啊呀，网络好像不太稳定...{str(e)[:50]}）",
                tokens_used=0,
                model=self._model,
            )

    async def extract_preferences(self, conversation: str) -> dict:
        """从对话中提取用户偏好/雷点 (供记忆系统调用)"""
        prompt = f"""分析以下对话，提取说话者的喜好(preferences)和雷点(dislikes)。只返回 JSON，不要任何其他文字。

格式: {{"preferences": ["喜好1", "喜好2"], "dislikes": ["雷点1"]}}
如果没发现任何信息，返回: {{"preferences": [], "dislikes": []}}

对话内容:
{conversation}"""

        messages = [{"role": "user", "content": prompt}]
        resp = await self.chat(messages, temperature=0.3, max_tokens=500)
        try:
            return json.loads(resp.content)
        except json.JSONDecodeError:
            # 尝试从回复中提取 JSON
            text = resp.content
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
            return {"preferences": [], "dislikes": []}

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


# ═══════════════════════════════════════════
# Grok 客户端（外包大脑）
# ═══════════════════════════════════════════

class GrokClient:
    """Grok API 异步客户端 — 负责 Vision 图片识别、联网搜索、复杂超纲任务"""

    def __init__(self):
        cfg = _load_api_config()
        gk = cfg.get("grok", _defaults["grok"])
        self._api_key = gk.get("api_key", "")
        self._base_url = gk.get("base_url", "https://api.x.ai")
        self._model = gk.get("model", "grok-2")
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(90.0),
            )
        return self._client

    @staticmethod
    def _encode_image(image_url: str) -> str:
        """将图片 URL 转为 base64 data URL"""
        # 如果已经是 data URL 则直接返回
        if image_url.startswith("data:"):
            return image_url
        # OneBot v11 图片 URL 可能需要处理
        return image_url

    async def chat_with_vision(
        self,
        text: str,
        image_urls: list[str],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AIResponse:
        """
        Vision 模式：带图片的对话请求。

        Args:
            text: 用户文字 (可选，纯图片时为空串)
            image_urls: 图片 URL 列表
            system_prompt: 系统提示词
            temperature: 温度
            max_tokens: 最大 token
        """
        if not self.is_configured:
            return AIResponse(
                content="（Grok 的 API Key 还没配置呢... 老板帮我填一下吧 >_<）",
                tokens_used=0,
                model=self._model,
            )

        # 构建多模态消息内容
        content_parts: list[dict] = []

        for url in image_urls:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": self._encode_image(url)},
            })

        if text.strip():
            content_parts.append({"type": "text", "text": text})

        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.append({
            "role": "user",
            "content": content_parts,
        })

        client = await self._get_client()
        try:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": self._model,
                    "messages": full_messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            usage = data.get("usage", {})
            tokens = usage.get("total_tokens", 0)
            return AIResponse(content=content, tokens_used=tokens, model=self._model)
        except httpx.HTTPStatusError as e:
            return AIResponse(
                content=f"（唔...Grok 好像闹脾气了 >_< 状态码: {e.response.status_code}）",
                tokens_used=0,
                model=self._model,
            )
        except Exception as e:
            return AIResponse(
                content=f"（啊呀，找 grok 哥哥的路上网断了...{str(e)[:50]}）",
                tokens_used=0,
                model=self._model,
            )

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AIResponse:
        """
        纯文本对话（用于超纲任务外包）。

        Args:
            messages: 对话历史
            system_prompt: 系统提示词
            temperature: 温度
            max_tokens: 最大 token
        """
        if not self.is_configured:
            return AIResponse(
                content="（Grok 的 API Key 还没配置呢... 老板帮我填一下吧 >_<）",
                tokens_used=0,
                model=self._model,
            )

        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        client = await self._get_client()
        try:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": self._model,
                    "messages": full_messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            usage = data.get("usage", {})
            tokens = usage.get("total_tokens", 0)
            return AIResponse(content=content, tokens_used=tokens, model=self._model)
        except httpx.HTTPStatusError as e:
            return AIResponse(
                content=f"（唔...Grok 好像闹脾气了 >_< 状态码: {e.response.status_code}）",
                tokens_used=0,
                model=self._model,
            )
        except Exception as e:
            return AIResponse(
                content=f"（啊呀，找 grok 哥哥的路上网断了...{str(e)[:50]}）",
                tokens_used=0,
                model=self._model,
            )

    async def fact_check(self, query: str) -> AIResponse:
        """联网查证事实 (利用 Grok 的联网搜索能力)"""
        system_prompt = "你是一个事实核查助手。请用女仆店员的口吻回答，但内容要准确。结合联网搜索结果给出判断。"
        messages = [{"role": "user", "content": f"帮我查证一下这件事是不是真的：{query}"}]
        return await self.chat(messages, system_prompt=system_prompt)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


# ═══════════════════════════════════════════
# 客户端单例
# ═══════════════════════════════════════════

_deepseek: Optional[DeepSeekClient] = None
_grok: Optional[GrokClient] = None


def get_deepseek() -> DeepSeekClient:
    global _deepseek
    if _deepseek is None:
        _deepseek = DeepSeekClient()
    return _deepseek


def get_grok() -> GrokClient:
    global _grok
    if _grok is None:
        _grok = GrokClient()
    return _grok
