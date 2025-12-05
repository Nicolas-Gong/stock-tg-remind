#!/usr/bin/env python3
"""
股票提醒机器人启动脚本
简化机器人启动过程，支持从配置文件加载设置
"""

import json
import os
import sys

from stock_bot import StockBot, logger


def load_config():
    """加载配置文件"""
    config_file = "config.json"

    if not os.path.exists(config_file):
        print("❌ 配置文件 config.json 不存在")
        print("请创建配置文件并设置正确的Telegram机器人令牌")
        sys.exit(1)

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config
    except (json.JSONDecodeError, IOError) as e:
        print(f"❌ 读取配置文件失败: {e}")
        sys.exit(1)


def validate_config(config):
    """验证配置"""
    if not config.get("telegram_token") or config["telegram_token"] == "YOUR_TELEGRAM_BOT_TOKEN":
        print("❌ 请在config.json中设置有效的Telegram机器人令牌")
        print("获取令牌方法：")
        print("1. 在Telegram中找到 @BotFather")
        print("2. 发送 /newbot 命令")
        print("3. 按照提示创建机器人并获取令牌")
        sys.exit(1)

    return True


async def main():
    """主函数"""
    logger.info("🚀 开始启动股票提醒机器人...")

    try:
        # 加载配置
        logger.info("📋 加载配置文件...")
        config = load_config()
        validate_config(config)
        logger.info("✅ 配置文件加载成功")

        # 创建机器人实例
        logger.info("🤖 初始化机器人...")
        bot = StockBot(config["telegram_token"])
        logger.info("✅ 机器人初始化成功")

        # 设置Bot Commands
        logger.info("⚙️ 设置机器人命令...")
        await bot.setup_bot_commands()
        logger.info("✅ 机器人命令设置成功")

        # 启动提醒检查
        logger.info("🔄 启动定期提醒检查任务...")
        bot.start_checking_alerts()
        logger.info("✅ 提醒检查任务启动成功")

        # 启动机器人
        logger.info("📱 启动Telegram机器人轮询...")
        logger.info("🎉 股票提醒机器人启动成功！")
        logger.info("💡 使用 /start 命令开始与机器人交互")
        logger.info("🛑 按 Ctrl+C 停止机器人")

        try:
            bot.start_polling()
        except KeyboardInterrupt:
            logger.info("🛑 收到停止信号，正在关闭机器人...")
        except Exception as e:
            logger.error(f"机器人运行时发生错误: {e}", exc_info=True)
            raise
        finally:
            logger.info("🤖 机器人已停止运行")

    except Exception as e:
        logger.error(f"启动过程中发生错误: {e}", exc_info=True)
        print(f"\n❌ 启动失败: {e}")
        print("请检查日志文件 'stock_bot.log' 获取详细错误信息")
        sys.exit(1)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
