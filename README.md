# 茉晓mox - 咖啡店打工女仆 Bot

<p align="center">
  <b>一个会手癌、会占卜、会戴口球的女仆 QQ Bot</b><br>
  Python 3.10+ · NoneBot2 · NapCatQQ · DeepSeek · Grok
</p>

---

## 她是谁？

茉晓（mox / 茉晚）是一个在咖啡店打工的 17 岁女仆店员。她不是冷冰冰的机器人——会手癌打错字然后撤回、会给客人占卜塔罗牌、会在凌晨催你睡觉、被欺负了还会叫老板给你戴口球。

## 快速开始

```bash
# Windows: 双击 install.bat → 编辑 config.yaml → 双击 run.bat
# Linux:   ./install.sh → 编辑 config.yaml → ./run.sh
```

需要配置的东西：
- `config.yaml` — 填入 DeepSeek & Grok API Key
- NapCatQQ — 反向 WS 连接 `ws://127.0.0.1:8090/onebot/v11/ws`

详见 [茉晓说明书.md](./茉晓说明书.md)

## 核心能力

| 模块 | 说明 |
|------|------|
| 🎭 五级阶层 | 老板/主人/员工/贵客/客人，自动识别，差别对待 |
| ☕ 女仆服务 | 文字咖啡甜品制作、Pillow 修图滤镜 |
| 🔮 塔罗占卜 | 签到→3问→抽22张大阿尔卡纳→AI 解读 |
| 🖐️ 手癌模拟 | 30% 概率打错字→1.5秒后撤回→补发正确版吐槽 |
| 🖼️ 图文缓冲 | 收到图片等4秒看有没有文字，模拟"先发图再说话" |
| 🚔 安保处刑 | 口球5条件+踢人2条件+贵客投票，台词一字不差 |
| 🧠 双 AI 引擎 | DeepSeek(日常) + Grok(外包/看图/查证) |
| 🧹 语义雷达 | 检测负面情绪→安抚+推塔罗；检测吃瓜→提议问Grok |
| 💰 Token 薪酬 | $2/天预算，耗尽下班，老板可加工资 |
| 📝 长线记忆 | 自动提取喜好/雷点 JSON，千人千面 |
| 🌙 定时任务 | 凌晨催睡觉 + 每天22:00群聊简报 |
| ⌨️ 无指令化 | 遇到 `/` 开头的消息装傻，坚称自己是店员 |

## 项目结构

```
MaidCafe_Bot/
├── bot.py                    # 入口
├── config.yaml               # 填 API Key
├── mox/                      # 核心基础设施 (8 模块)
│   ├── database.py           # 异步 SQLite 4 表
│   ├── middleware.py          # 五级阶层解析
│   ├── api_client.py         # DeepSeek + Grok
│   ├── image_buffer.py       # 图文防抖缓冲池
│   ├── memory_system.py      # 长线记忆
│   ├── token_manager.py      # 薪酬管理
│   ├── sender.py             # 手癌+断句发送
│   └── tarot_state.py        # 22张塔罗状态机
└── src/plugins/              # 业务插件 (9 模块)
    ├── guard.py              # /拦截 + 黑名单
    ├── chat_handler.py       # 主聊天路由
    ├── tarot_handler.py      # 多轮占卜
    ├── punishment_system.py  # 口球+踢人
    ├── wake_on_name.py       # 名字唤醒
    ├── image_service.py      # Pillow 修图
    ├── semantic_radar.py     # 语义雷达
    └── scheduler_tasks.py    # 定时简报
```

## 技术栈

Python 3.10+ · NoneBot2 · OneBot v11 · aiosqlite · httpx · Pillow · apscheduler

## 许可

MIT License — 随便玩，别用茉晓做坏事就好 awa
