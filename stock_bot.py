#!/usr/bin/env python3
"""
Telegram股票价格提醒机器人
功能：
1. 添加/修改股票提醒列表
2. 实时监控股票价格变化
3. 支持多种提醒条件：
   - 几分钟内涨/跌幅超过指定百分比
   - 今日涨/跌幅超过指定百分比
   - 设置提醒频率
4. 使用文件缓存存储数据
"""

import os
import json
import time
import threading
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import telegram
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# 默认配置
DEFAULT_CONFIG = {
    "telegram_token": "YOUR_TELEGRAM_BOT_TOKEN",
    "data_file": "stock_data.json",
    "cache_file": "stock_cache.json",
    "name_cache_file": "stock_names.json",  # 股票名称缓存文件
    "check_interval": 60,  # 检查间隔（秒）
    "timeout": 10,  # 请求超时时间
    "cache_expiry_seconds": 6,  # 缓存过期时间（秒）
}

# 从配置文件加载配置
def load_config():
    """从config.json加载配置"""
    config_file = "config.json"
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
            # 合并用户配置和默认配置
            config = DEFAULT_CONFIG.copy()
            config.update(user_config)
            return config
        except (json.JSONDecodeError, IOError) as e:
            print(f"加载配置文件失败: {e}")
            print("使用默认配置...")
            return DEFAULT_CONFIG
    else:
        print("未找到config.json文件，使用默认配置...")
        return DEFAULT_CONFIG

# 全局配置
CONFIG = load_config()

def is_trading_time(stock_code: str) -> bool:
    """
    检查股票是否在交易时间内
    支持A股、港股、美股的交易时间判断（北京时间）
    """
    now = datetime.now()
    current_time = now.time()
    weekday = now.weekday()  # 0=周一, 6=周日

    # 周六日不交易
    if weekday >= 5:
        return False

    # 根据股票代码判断市场和交易时间
    if stock_code.startswith(('6', '0', '3')):
        # 中国A股：北京时间 9:30-11:30, 13:00-15:00
        morning_start = datetime.strptime("09:30", "%H:%M").time()
        morning_end = datetime.strptime("11:30", "%H:%M").time()
        afternoon_start = datetime.strptime("13:00", "%H:%M").time()
        afternoon_end = datetime.strptime("15:00", "%H:%M").time()

        return (morning_start <= current_time <= morning_end) or \
               (afternoon_start <= current_time <= afternoon_end)

    elif stock_code.isdigit() and len(stock_code) == 5:
        # 港股：北京时间 9:30-12:00, 13:00-16:00
        morning_start = datetime.strptime("09:30", "%H:%M").time()
        morning_end = datetime.strptime("12:00", "%H:%M").time()
        afternoon_start = datetime.strptime("13:00", "%H:%M").time()
        afternoon_end = datetime.strptime("16:00", "%H:%M").time()

        return (morning_start <= current_time <= morning_end) or \
               (afternoon_start <= current_time <= afternoon_end)

    elif stock_code.replace('.', '').isalpha():
        # 美股：美东时间 9:30-16:00，转换为北京时间
        # 北京时间：21:30(今) - 04:00(明) 或 22:30(今) - 05:00(明)
        # 这里简化为北京时间 21:30 到次日 04:00
        us_start_evening = datetime.strptime("21:30", "%H:%M").time()
        us_end_night = datetime.strptime("23:59:59", "%H:%M:%S").time()
        us_start_next_morning = datetime.strptime("00:00:00", "%H:%M:%S").time()
        us_end_next_morning = datetime.strptime("04:00", "%H:%M").time()

        # 如果是晚上21:30到23:59，或是凌晨00:00到04:00
        if (current_time >= us_start_evening and current_time <= us_end_night) or \
           (current_time >= us_start_next_morning and current_time <= us_end_next_morning):
            return True

        return False

    else:
        # 未知市场，默认认为在交易时间内
        return True

# 股票名称缓存
class StockNameCache:
    def __init__(self, name_cache_file: str):
        self.name_cache_file = name_cache_file
        self.name_cache = self._load_cache()

    def _load_cache(self) -> Dict:
        """加载名称缓存文件"""
        if os.path.exists(self.name_cache_file):
            try:
                with open(self.name_cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save_cache(self):
        """保存名称缓存到文件"""
        try:
            with open(self.name_cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.name_cache, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"保存名称缓存失败: {e}")

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        """获取股票名称"""
        return self.name_cache.get(stock_code)

    def set_stock_name(self, stock_code: str, name: str):
        """设置股票名称到缓存"""
        if stock_code and name:
            self.name_cache[stock_code] = name
            self._save_cache()

# 股票数据缓存
class StockCache:
    def __init__(self, cache_file: str):
        self.cache_file = cache_file
        self.cache = self._load_cache()

    def _load_cache(self) -> Dict:
        """加载缓存文件"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save_cache(self):
        """保存缓存到文件"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"保存缓存失败: {e}")

    def get_stock_data(self, stock_code: str) -> Optional[Dict]:
        """获取股票数据（优先从缓存）"""
        if stock_code in self.cache:
            cached_data = self.cache[stock_code]
            # 检查缓存是否过期
            cache_expiry = timedelta(seconds=CONFIG.get("cache_expiry_seconds", 6))
            if datetime.now() - datetime.fromisoformat(cached_data['timestamp']) < cache_expiry:
                return cached_data['data']
        return None

    def set_stock_data(self, stock_code: str, data: Dict):
        """设置股票数据到缓存"""
        self.cache[stock_code] = {
            'data': data,
            'timestamp': datetime.now().isoformat()
        }
        self._save_cache()

# 股票数据获取
class StockDataFetcher:
    def __init__(self, cache: StockCache, name_cache: StockNameCache = None):
        self.cache = cache
        self.name_cache = name_cache
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def fetch_stock_data(self, stock_code: str) -> Optional[Dict]:
        """从腾讯财经API获取股票数据"""
        # 先尝试从缓存获取
        cached_data = self.cache.get_stock_data(stock_code)
        if cached_data:
            return cached_data

        # 从腾讯财经API获取数据（支持批量请求优化）
        try:
            # 构建API URL（单个请求）
            # 支持多市场：上海(sh)、深圳(sz)、港股(hk)、美股(us)
            if stock_code.startswith('6'):
                api_url = f"http://qt.gtimg.cn/q=sh{stock_code}"
            elif stock_code.startswith('0') or stock_code.startswith('3'):
                api_url = f"http://qt.gtimg.cn/q=sz{stock_code}"
            elif stock_code.isdigit() and len(stock_code) == 5:
                # 港股代码（5位数字）
                api_url = f"http://qt.gtimg.cn/q=hk{stock_code}"
            elif stock_code.replace('.', '').isalpha():
                # 美股代码（字母）
                api_url = f"http://qt.gtimg.cn/q=us{stock_code}"
            else:
                # 默认当作上海股票
                api_url = f"http://qt.gtimg.cn/q=sh{stock_code}"

            # 发送HTTP请求
            response = self.session.get(api_url, timeout=10)
            response.raise_for_status()

            # 解析腾讯财经API返回的数据
            stock_data = self._parse_api_response(response.text, stock_code)
            if stock_data:
                # 缓存数据
                self.cache.set_stock_data(stock_code, stock_data)
                return stock_data

        except Exception as e:
            print(f"获取股票数据失败: {e}")
            return None

    def _parse_api_response(self, raw_data: str, target_code: str) -> Optional[Dict]:
        """解析腾讯财经API的批量响应数据"""
        if not raw_data or 'v_' not in raw_data:
            print(f"无效的API响应: {raw_data}")
            return None

        # 腾讯财经API支持批量请求，返回多行数据
        # 每行格式：v_{market}{code}="data"\n
        lines = raw_data.strip().split('\n')

        for line in lines:
            if not line.startswith('v_'):
                continue

            try:
                # 提取股票代码和数据
                # 格式：v_sh600519="1~茅台~600519~..." 或 v_usAAPL="200~Apple~AAPL~..."
                parts = line.split('=', 1)
                if len(parts) != 2:
                    continue

                code_part = parts[0][2:]  # 去掉'v_'前缀
                data_str = parts[1].strip('";')

                # 检查是否是我们需要的股票代码
                if target_code in code_part:
                    fields = data_str.split('~')

                    if len(fields) < 50:  # 确保有足够的数据字段
                        print(f"数据字段不完整: {len(fields)}")
                        continue

                    # 解析股票数据
                    stock_data = {
                        "code": fields[2],  # 股票代码
                        "name": fields[1],  # 股票名称
                        "current_price": float(fields[3]),  # 当前价格
                        "prev_close": float(fields[4]),     # 昨收
                        "open_price": float(fields[5]),     # 今开
                        "high_price": float(fields[33]) if len(fields) > 33 and fields[33] else 0,    # 最高价
                        "low_price": float(fields[34]) if len(fields) > 34 and fields[34] else 0,     # 最低价
                        "timestamp": datetime.now().isoformat()
                    }

                    # 计算涨跌幅
                    if stock_data["prev_close"] > 0:
                        change = stock_data["current_price"] - stock_data["prev_close"]
                        change_percent = (change / stock_data["prev_close"]) * 100
                        stock_data["change"] = round(change, 2)
                        stock_data["change_percent"] = round(change_percent, 2)
                    else:
                        stock_data["change"] = 0
                        stock_data["change_percent"] = 0

                    # 缓存股票名称
                    if self.name_cache and stock_data["name"]:
                        self.name_cache.set_stock_name(target_code, stock_data["name"])

                    return stock_data

            except (ValueError, IndexError) as e:
                print(f"解析股票数据时出错: {e}")
                continue

        return None

# 提醒管理
class AlertManager:
    def __init__(self, data_file: str):
        self.data_file = data_file
        self.alerts = self._load_alerts()

    def _load_alerts(self) -> Dict:
        """加载提醒数据"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {"alerts": [], "last_alert_times": {}}
        return {"alerts": [], "last_alert_times": {}}

    def _save_alerts(self):
        """保存提醒数据"""
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.alerts, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"保存提醒数据失败: {e}")

    def add_alert(self, user_id: int, stock_code: str, alert_type: str,
                  threshold: float, interval_minutes: int = 5, threshold_direction: str = 'both') -> bool:
        """添加提醒"""
        alert = {
            "user_id": user_id,
            "stock_code": stock_code,
            "alert_type": alert_type,
            "threshold": threshold,
            "threshold_direction": threshold_direction,  # 'both', 'up', 'down'
            "interval_minutes": interval_minutes,
            "last_alert_time": None,
            "created_at": datetime.now().isoformat()
        }

        # 检查是否已存在完全相同的提醒
        for existing in self.alerts["alerts"]:
            if (existing["user_id"] == user_id and
                existing["stock_code"] == stock_code and
                existing["alert_type"] == alert_type and
                existing["threshold"] == threshold and
                existing.get("threshold_direction", "both") == threshold_direction and
                existing["interval_minutes"] == interval_minutes):
                return False  # 已存在

        self.alerts["alerts"].append(alert)
        self._save_alerts()
        return True

    def remove_alert(self, user_id: int, alert_id: int) -> bool:
        """移除提醒"""
        for i, alert in enumerate(self.alerts["alerts"]):
            if alert["user_id"] == user_id and i == alert_id:
                del self.alerts["alerts"][i]
                self._save_alerts()
                return True
        return False

    def get_user_alerts(self, user_id: int) -> List[Dict]:
        """获取用户的所有提醒"""
        return [alert for alert in self.alerts["alerts"] if alert["user_id"] == user_id]

    def can_send_alert(self, alert: Dict) -> bool:
        """检查是否可以发送提醒（根据提醒设置的时间间隔）"""
        user_id = alert["user_id"]
        stock_code = alert["stock_code"]
        alert_type = alert["alert_type"]
        interval_minutes = alert["interval_minutes"]

        key = f"{user_id}_{stock_code}_{alert_type}"
        last_time = self.alerts["last_alert_times"].get(key)

        if last_time:
            last_datetime = datetime.fromisoformat(last_time)
            if datetime.now() - last_datetime < timedelta(minutes=interval_minutes):
                return False

        self.alerts["last_alert_times"][key] = datetime.now().isoformat()
        self._save_alerts()
        return True

    def check_alerts(self, fetcher: StockDataFetcher, bot: telegram.Bot):
        """检查所有提醒并发送通知"""
        current_time = datetime.now()
        print(f"[{current_time}] 开始检查提醒，共 {len(self.alerts['alerts'])} 个提醒")

        for alert in self.alerts["alerts"]:
            stock_code = alert["stock_code"]

            # 检查是否在交易时间内
            is_trading = is_trading_time(stock_code)
            print(f"[{current_time}] 检查 {stock_code} 是否在交易时间内: {is_trading}")
            if not is_trading:
                continue

            stock_data = fetcher.fetch_stock_data(stock_code)
            if not stock_data:
                print(f"[{current_time}] 获取 {stock_code} 数据失败")
                continue

            print(f"[{current_time}] {stock_code} 价格: {stock_data.get('current_price', 0)}, 涨跌幅: {stock_data.get('change_percent', 0)}%")

            # 检查提醒条件
            alert_triggered = False
            message = ""

            if alert["alert_type"] == "price_change":
                # 价格变化提醒
                change_percent = stock_data.get("change_percent", 0)
                threshold_direction = alert.get("threshold_direction", "both")

                # 根据方向判断是否触发提醒
                should_trigger = False
                if threshold_direction == "both":
                    should_trigger = abs(change_percent) >= alert["threshold"]
                elif threshold_direction == "up":
                    should_trigger = change_percent >= alert["threshold"]
                elif threshold_direction == "down":
                    should_trigger = change_percent <= -alert["threshold"]

                if should_trigger:
                    alert_triggered = True
                    direction = "上涨" if change_percent > 0 else "下跌"
                    direction_desc = {
                        'both': f"{direction}幅度",
                        'up': "涨幅",
                        'down': "跌幅"
                    }[threshold_direction]

                    message = (f"🔔 股票提醒\n"
                              f"股票: {stock_data['name']} ({stock_data['code']})\n"
                              f"当前价格: {stock_data['current_price']}\n"
                              f"{direction_desc}: {abs(change_percent)}%\n"
                              f"阈值: {alert['threshold']}%")

            elif alert["alert_type"] == "daily_change":
                # 今日涨跌幅提醒
                change_percent = stock_data.get("change_percent", 0)
                threshold_direction = alert.get("threshold_direction", "both")

                # 根据方向判断是否触发提醒
                should_trigger = False
                if threshold_direction == "both":
                    should_trigger = abs(change_percent) >= alert["threshold"]
                elif threshold_direction == "up":
                    should_trigger = change_percent >= alert["threshold"]
                elif threshold_direction == "down":
                    should_trigger = change_percent <= -alert["threshold"]

                if should_trigger:
                    alert_triggered = True
                    direction = "上涨" if change_percent > 0 else "下跌"
                    direction_desc = {
                        'both': f"今日{direction}幅",
                        'up': "今日涨幅",
                        'down': "今日跌幅"
                    }[threshold_direction]

                    message = (f"🔔 今日涨跌幅提醒\n"
                              f"股票: {stock_data['name']} ({stock_data['code']})\n"
                              f"{direction_desc}: {abs(change_percent)}%\n"
                              f"阈值: {alert['threshold']}%")

            # 检查是否可以发送提醒
            if alert_triggered and self.can_send_alert(alert):
                try:
                    bot.send_message(
                        chat_id=alert["user_id"],
                        text=message,
                        parse_mode=telegram.constants.ParseMode.HTML
                    )
                except Exception as e:
                    print(f"发送提醒失败: {e}")

# 机器人命令处理
class StockBot:
    def __init__(self, token: str):
        self.token = token
        self.bot = telegram.Bot(token=token)
        self.cache = StockCache(CONFIG["cache_file"])
        self.name_cache = StockNameCache(CONFIG["name_cache_file"])
        self.fetcher = StockDataFetcher(self.cache, self.name_cache)
        self.alert_manager = AlertManager(CONFIG["data_file"])

        # 创建应用
        self.app = Application.builder().token(token).build()

        # 注册命令处理器
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("help", self.help))
        self.app.add_handler(CommandHandler("add", self.add_alert))
        self.app.add_handler(CommandHandler("list", self.list_alerts))
        self.app.add_handler(CommandHandler("remove", self.remove_alert))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))

        # 注册消息处理器
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /start 命令"""
        user = update.effective_user
        await update.message.reply_text(
            f"👋 你好，{user.first_name}！\n"
            "我是股票价格提醒机器人。\n\n"
            "你可以使用以下命令：\n"
            "/add - 添加股票提醒\n"
            "/list - 查看我的提醒列表\n"
            "/remove - 移除提醒\n"
            "/help - 查看帮助信息"
        )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /help 命令"""
        help_text = (
            "📚 股票提醒机器人帮助\n\n"
            "📌 命令说明：\n"
            "/start - 开始使用机器人\n"
            "/add - 添加股票提醒\n"
            "/list - 查看我的提醒列表\n"
            "/remove - 移除提醒\n"
            "/help - 查看帮助信息\n\n"
            "📌 添加提醒示例：\n"
            "/add 600000 价格变化 2 5 - 添加股票600000，当价格变化超过2%时提醒，每5分钟最多提醒一次\n"
            "/add 000001 今日涨跌 5 - 添加股票000001，当今日涨跌幅超过5%时提醒\n\n"
            "📌 提醒类型：\n"
            "价格变化 - 最近几分钟内的价格变化\n"
            "今日涨跌 - 今日整体涨跌幅\n"
        )
        await update.message.reply_text(help_text)

    async def add_alert(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /add 命令"""
        user = update.effective_user
        args = context.args

        if len(args) < 3:
            await update.message.reply_text(
                "❌ 无效的命令格式。\n"
                "示例：/add 600000 价格变化 ±2 5\n"
                "参数：股票代码 提醒类型 阈值(%) [时间间隔(分钟)]\n\n"
                "阈值格式：\n"
                "  ±2 或 2   - 涨跌幅超过2%时提醒（双向）\n"
                "  +2        - 涨幅超过2%时提醒（单向上）\n"
                "  -2        - 跌幅超过2%时提醒（单向下）"
            )
            return

        stock_code = args[0].upper()
        alert_type = args[1]

        # 解析阈值，支持 ±2, +2, -2, 2 格式
        threshold_str = args[2]
        try:
            if threshold_str.startswith('±'):
                threshold_value = float(threshold_str[1:])
                threshold_direction = 'both'  # 双向
            elif threshold_str.startswith('+'):
                threshold_value = float(threshold_str[1:])
                threshold_direction = 'up'  # 向上
            elif threshold_str.startswith('-'):
                threshold_value = float(threshold_str[1:])
                threshold_direction = 'down'  # 向下
            else:
                threshold_value = float(threshold_str)
                threshold_direction = 'both'  # 双向
        except ValueError:
            await update.message.reply_text(
                "❌ 无效的阈值格式。\n"
                "支持格式：±2, +2, -2 或 2\n"
                "例如：±2（双向）、+2（上涨）、-2（下跌）"
            )
            return

        try:
            interval_minutes = int(args[3]) if len(args) > 3 else 5
        except ValueError:
            await update.message.reply_text("❌ 无效的时间间隔。请输入数字。")
            return

        if alert_type not in ["价格变化", "今日涨跌"]:
            await update.message.reply_text("❌ 无效的提醒类型。请选择：价格变化 或 今日涨跌")
            return

        # 添加提醒
        success = self.alert_manager.add_alert(
            user.id, stock_code, alert_type, threshold_value, interval_minutes, threshold_direction
        )

        if success:
            direction_text = {
                'both': '涨跌',
                'up': '上涨',
                'down': '下跌'
            }[threshold_direction]

            # 尝试获取股票名称
            stock_name = self.name_cache.get_stock_name(stock_code)
            if not stock_name:
                # 如果缓存中没有，尝试获取一次
                stock_data = self.fetcher.fetch_stock_data(stock_code)
                if stock_data:
                    stock_name = stock_data.get('name', '')

            stock_display = f"{stock_name} ({stock_code})" if stock_name else stock_code

            await update.message.reply_text(
                f"✅ 成功添加提醒！\n"
                f"股票：{stock_display}\n"
                f"类型：{alert_type}\n"
                f"阈值：{threshold_str}（{direction_text}）\n"
                f"时间间隔：{interval_minutes}分钟"
            )
        else:
            await update.message.reply_text("❌ 添加提醒失败，可能已存在相同提醒。")

    async def list_alerts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /list 命令"""
        user = update.effective_user
        alerts = self.alert_manager.get_user_alerts(user.id)

        if not alerts:
            await update.message.reply_text("📋 你还没有添加任何提醒。使用 /add 命令添加新提醒。")
            return

        message = "📋 你的股票提醒列表：\n\n"
        for i, alert in enumerate(alerts):
            # 获取股票名称
            stock_name = self.name_cache.get_stock_name(alert['stock_code'])
            if not stock_name:
                # 如果缓存中没有，尝试获取一次
                stock_data = self.fetcher.fetch_stock_data(alert['stock_code'])
                if stock_data:
                    stock_name = stock_data.get('name', '')

            stock_display = f"{stock_name} ({alert['stock_code']})" if stock_name else alert['stock_code']

            # 获取阈值方向显示
            threshold_direction = alert.get('threshold_direction', 'both')
            direction_symbols = {
                'both': '±',
                'up': '+',
                'down': '-'
            }
            threshold_display = f"{direction_symbols[threshold_direction]}{alert['threshold']}"

            message += (
                f"{i+1}. 股票: {stock_display}\n"
                f"   类型: {alert['alert_type']}\n"
                f"   阈值: {threshold_display}%\n"
                f"   时间间隔: {alert['interval_minutes']}分钟\n"
                f"   创建时间: {alert['created_at']}\n\n"
            )

        message += "使用 /remove 命令移除提醒。"
        await update.message.reply_text(message)

    async def remove_alert(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /remove 命令"""
        user = update.effective_user
        args = context.args

        if len(args) != 1:
            await update.message.reply_text("❌ 请指定要移除的提醒编号。示例：/remove 1")
            return

        try:
            alert_id = int(args[0]) - 1  # 转换为0-based索引
        except ValueError:
            await update.message.reply_text("❌ 无效的提醒编号。")
            return

        # 获取用户提醒列表
        alerts = self.alert_manager.get_user_alerts(user.id)
        if alert_id < 0 or alert_id >= len(alerts):
            await update.message.reply_text("❌ 无效的提醒编号。")
            return

        # 移除提醒
        success = self.alert_manager.remove_alert(user.id, alert_id)
        if success:
            await update.message.reply_text(f"✅ 成功移除提醒 {alert_id + 1}。")
        else:
            await update.message.reply_text("❌ 移除提醒失败。")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理回调查询（按钮点击等）"""
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text=f"你点击了：{query.data}")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理普通消息"""
        text = update.message.text
        await update.message.reply_text(f"你发送了：{text}")

    def start_polling(self):
        """启动机器人"""
        print("启动股票提醒机器人...")
        try:
            self.app.run_polling()
        except Exception as e:
            print(f"机器人启动失败: {e}")
            if "Conflict" in str(e):
                print("检测到冲突：可能是另一个机器人实例正在运行")
                print("请先停止其他机器人实例，然后重新启动")
            raise

    def start_checking_alerts(self):
        """启动定期检查提醒"""
        def check_alerts_loop():
            while True:
                try:
                    print(f"检查提醒... {datetime.now()}")
                    self.alert_manager.check_alerts(self.fetcher, self.bot)
                except Exception as e:
                    print(f"检查提醒时出错: {e}")
                time.sleep(CONFIG["check_interval"])

        # 启动后台线程
        alert_thread = threading.Thread(target=check_alerts_loop, daemon=True)
        alert_thread.start()

if __name__ == "__main__":
    # 创建机器人实例
    bot = StockBot(CONFIG["telegram_token"])

    # 启动提醒检查
    bot.start_checking_alerts()

    # 启动机器人
    bot.start_polling()
