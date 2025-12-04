#!/usr/bin/env python3
"""
股票提醒机器人启动脚本
简化机器人启动过程，支持从配置文件加载设置
"""

import os
import json
import sys
from stock_bot import StockBot

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

def main():
    """主函数"""
    print("🚀 启动股票提醒机器人...")

    # 加载配置
    config = load_config()
    validate_config(config)

    # 创建机器人实例
    try:
        bot = StockBot(config["telegram_token"])
        print("✅ 机器人初始化成功")
    except Exception as e:
        print(f"❌ 机器人初始化失败: {e}")
        sys.exit(1)

    # 启动提醒检查
    print("🔄 启动提醒检查线程...")
    bot.start_checking_alerts()

    # 启动机器人
    print("📱 启动Telegram机器人...")
    print("机器人已启动！使用 /start 命令开始使用")
    print("按 Ctrl+C 停止机器人")

    try:
        bot.start_polling()
    except KeyboardInterrupt:
        print("\n🛑 机器人已停止")
    except Exception as e:
        print(f"❌ 机器人运行出错: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
