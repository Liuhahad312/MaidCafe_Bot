"""
茉晓mox - 咖啡店打工女仆 Bot 入口
"""

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(ONEBOT_V11Adapter)

# 加载全局阶层解析中间件（基础设施层，优先级最高）
nonebot.load_plugin("mox.middleware")

# 加载聊天业务插件
nonebot.load_plugins("src/plugins")


# ═══════════════════════════════════════════
# 生命周期钩子
# ═══════════════════════════════════════════

@driver.on_startup
async def on_startup():
    """Bot 启动时初始化数据库"""
    from mox.database import init_db
    await init_db()
    # 预热 API 客户端单例
    from mox.api_client import get_deepseek, get_grok
    get_deepseek()
    get_grok()


@driver.on_shutdown
async def on_shutdown():
    """Bot 关闭时清理资源"""
    from mox.image_buffer import get_image_buffer
    buf = get_image_buffer()
    buf.cleanup()

    from mox.api_client import get_deepseek, get_grok
    ds = get_deepseek()
    gk = get_grok()
    await ds.close()
    await gk.close()


if __name__ == "__main__":
    nonebot.run()
