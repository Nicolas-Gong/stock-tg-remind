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
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests
import telegram
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('stock_bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_CONFIG = {
    "telegram_token": "YOUR_TELEGRAM_BOT_TOKEN",
    "data_file": "stock_data.json",
    "cache_file": "stock_cache.json",
    "name_cache_file": "stock_names.json",  # 股票名称缓存文件
    "check_interval": 60,  # 检查间隔（秒）
    "timeout": 10,  # 请求超时时间
    "cache_expiry_seconds": 30,  # 缓存过期时间（秒）
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
            logger.error(f"加载配置文件失败: {e}")
            logger.info("使用默认配置...")
            return DEFAULT_CONFIG
    else:
        logger.warning("未找到config.json文件，使用默认配置...")
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
        # 美股：美东时间 9:30-16:00，根据冬令时/夏令时转换为北京时间
        # 夏令时（3月-11月）：北京时间 21:30(今晚) - 04:00(明早)
        # 冬令时（11月-次年3月）：北京时间 22:30(今晚) - 05:00(明早)

        # 判断是否为冬令时（11月到次年3月）
        month = now.month
        is_winter_time = month >= 11 or month <= 3

        if is_winter_time:
            # 冬令时：美东时间比北京时间晚13小时，交易时间北京时间22:30-次日05:00
            us_start = datetime.strptime("22:30", "%H:%M").time()
            us_end = datetime.strptime("05:00", "%H:%M").time()
        else:
            # 夏令时：美东时间比北京时间晚12小时，交易时间北京时间21:30-次日04:00
            us_start = datetime.strptime("21:30", "%H:%M").time()
            us_end = datetime.strptime("04:00", "%H:%M").time()

        # 美股交易跨天，需要特殊处理
        if current_time >= us_start or current_time <= us_end:
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
            logger.error(f"保存名称缓存失败: {e}")

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
            logger.error(f"保存缓存失败: {e}")

    def get_stock_data(self, stock_code: str) -> Optional[Dict]:
        """获取股票数据（优先从缓存）"""
        if stock_code in self.cache:
            cached_data = self.cache[stock_code]
            # 检查缓存是否过期
            timestamp = cached_data.get('timestamp')
            if timestamp:
                try:
                    cached_time = datetime.fromisoformat(timestamp)
                    if datetime.now() - cached_time < timedelta(seconds=CONFIG["cache_expiry_seconds"]):
                        return cached_data['data']
                    else:
                        # 缓存过期，删除
                        del self.cache[stock_code]
                        self._save_cache()
                except (ValueError, TypeError):
                    # 时间戳格式错误，删除缓存
                    del self.cache[stock_code]
                    self._save_cache()
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
        """从腾讯财经API获取股票数据（单个股票）"""
        # 先尝试从缓存获取
        cached_data = self.cache.get_stock_data(stock_code)
        if cached_data:
            return cached_data

        # 单个股票获取（兼容旧接口）
        return self._fetch_batch_stock_data([stock_code]).get(stock_code)

    def fetch_batch_stock_data(self, stock_codes: List[str]) -> Dict[str, Optional[Dict]]:
        """批量从腾讯财经API获取多个股票数据"""
        if not stock_codes:
            return {}

        # 检查缓存中已有的数据
        result = {}
        uncached_codes = []

        for code in stock_codes:
            cached_data = self.cache.get_stock_data(code)
            if cached_data:
                result[code] = cached_data
            else:
                uncached_codes.append(code)

        # 如果所有数据都在缓存中，直接返回
        if not uncached_codes:
            return result

        # 批量获取未缓存的数据
        batch_result = self._fetch_batch_stock_data(uncached_codes)

        # 合并结果
        result.update(batch_result)
        return result

    def _fetch_batch_stock_data(self, stock_codes: List[str]) -> Dict[str, Optional[Dict]]:
        """内部批量获取股票数据"""
        if not stock_codes:
            return {}

        # 构建批量API请求
        api_parts = []
        for stock_code in stock_codes:
            # 构建市场前缀
            if stock_code.startswith('6'):
                market_prefix = "sh"
            elif stock_code.startswith('0') or stock_code.startswith('3'):
                market_prefix = "sz"
            elif stock_code.isdigit() and len(stock_code) == 5:
                # 港股代码（5位数字）
                market_prefix = "hk"
            elif stock_code.replace('.', '').isalpha():
                # 美股代码（字母）
                market_prefix = "us"
            else:
                # 默认当作上海股票
                market_prefix = "sh"

            api_parts.append(f"{market_prefix}{stock_code}")

        # 腾讯财经API支持一次请求多个股票，用逗号分隔
        api_url = f"https://sqt.gtimg.cn/?q={','.join(api_parts)}&fmt=json"

        try:
            # 发送HTTP请求
            response = self.session.get(api_url, timeout=10)
            response.raise_for_status()

            # 解析批量响应
            return self._parse_batch_api_response(response.text, stock_codes)

        except Exception as e:
            logger.error(f"批量获取股票数据失败: {e}")
            # 返回空结果
            return {code: None for code in stock_codes}

    def _get_market_prefix(self, stock_code: str) -> str:
        """根据股票代码获取市场前缀"""
        if stock_code.startswith('6'):
            return "sh"
        elif stock_code.startswith('0') or stock_code.startswith('3'):
            return "sz"
        elif stock_code.isdigit() and len(stock_code) == 5:
            return "hk"
        elif stock_code.replace('.', '').isalpha():
            return "us"
        else:
            return "sh"

    def _parse_single_stock_data(self, json_data: Dict, stock_code: str) -> Optional[Dict]:
        """解析单个股票的数据"""
        try:
            market_prefix = self._get_market_prefix(stock_code)
            key = f"{market_prefix}{stock_code}"

            # 检查是否有我们需要的股票数据
            if key not in json_data:
                logger.warning(f"未找到股票数据: {key}")
                return None

            fields = json_data[key]

            if len(fields) < 40:  # 确保有足够的数据字段
                logger.warning(f"数据字段不完整: {len(fields)}")
                return None

            # 解析股票数据
            # 新接口字段位置：
            # [0]: 类型/状态, [1]: 股票名称, [2]: 股票代码
            # [3]: 当前价格, [4]: 昨收, [5]: 今开, [6]: 成交量
            # [7-32]: 其他数据, [33]: 最高价, [34]: 最低价
            stock_data = {
                "code": fields[2],  # 股票代码
                "name": fields[1],  # 股票名称
                "current_price": float(fields[3]),  # 当前价格
                "prev_close": float(fields[4]),  # 昨收
                "open_price": float(fields[5]),  # 今开
                "volume": int(fields[6]) if fields[6] else 0,  # 成交量
                "timestamp": datetime.now().isoformat()
            }

            # 添加可选字段（如果存在）
            if len(fields) > 33:
                stock_data["high_price"] = float(fields[33]) if fields[33] else 0  # 最高价
            if len(fields) > 34:
                stock_data["low_price"] = float(fields[34]) if fields[34] else 0  # 最低价

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
                self.name_cache.set_stock_name(stock_code, stock_data["name"])

            return stock_data

        except (ValueError, IndexError, KeyError) as e:
            logger.error(f"解析股票数据时出错: {e}")
            return None

    def _parse_batch_api_response(self, raw_data: str, requested_codes: List[str]) -> Dict[str, Optional[Dict]]:
        """解析腾讯财经API的批量JSON响应数据"""
        result = {}

        try:
            # 解析JSON响应
            json_data = json.loads(raw_data)

            for stock_code in requested_codes:
                stock_data = self._parse_single_stock_data(json_data, stock_code)
                if stock_data:
                    # 缓存数据
                    self.cache.set_stock_data(stock_code, stock_data)
                result[stock_code] = stock_data

        except json.JSONDecodeError as e:
            logger.error(f"解析批量股票数据时出错: {e}")
            logger.debug(f"原始数据: {raw_data[:200]}...")  # 只打印前200字符用于调试
            # 返回空结果
            result = {code: None for code in requested_codes}

        return result

    def _parse_api_response(self, raw_data: str, target_code: str) -> Optional[Dict]:
        """解析腾讯财经API的JSON响应数据（单个股票）"""
        try:
            json_data = json.loads(raw_data)
            return self._parse_single_stock_data(json_data, target_code)
        except json.JSONDecodeError as e:
            logger.error(f"解析股票数据时出错: {e}")
            logger.debug(f"原始数据: {raw_data[:200]}...")  # 只打印前200字符用于调试
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
                    data = json.load(f)
                    # 确保所有必要的字段都存在
                    data.setdefault("alerts", [])
                    data.setdefault("last_alert_times", {})
                    data.setdefault("alert_states", {})
                    data.setdefault("price_history", {})
                    data.setdefault("alert_history", [])
                    return data
            except (json.JSONDecodeError, IOError):
                return {"alerts": [], "last_alert_times": {}, "alert_states": {}, "price_history": {}, "alert_history": []}
        return {"alerts": [], "last_alert_times": {}, "alert_states": {}, "price_history": {}, "alert_history": []}

    def _save_alerts(self):
        """保存提醒数据"""
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.alerts, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"保存提醒数据失败: {e}")

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

    def can_send_daily_change_alert(self, alert: Dict, change_percent: float) -> bool:
        """
        检查是否可以发送今日涨跌提醒
        逻辑：只有当涨跌幅从低于阈值变为高于阈值时才发送提醒一次
        """
        user_id = alert["user_id"]
        stock_code = alert["stock_code"]
        threshold = alert["threshold"]
        threshold_direction = alert.get("threshold_direction", "both")
        alert_type = alert["alert_type"]

        # 为每个提醒创建唯一的状态key
        key = f"{user_id}_{stock_code}_{alert_type}_{threshold}_{threshold_direction}"

        # 获取上次的状态
        last_state = self.alerts.get("alert_states", {}).get(key, {})

        # 当前是否满足触发条件
        currently_triggered = False
        if threshold_direction == "both":
            currently_triggered = abs(change_percent) >= threshold
        elif threshold_direction == "up":
            currently_triggered = change_percent >= threshold
        elif threshold_direction == "down":
            currently_triggered = change_percent <= -threshold

        # 上次是否已经触发过
        previously_triggered = last_state.get("triggered", False)

        # 只有当状态从"未触发"变为"已触发"时才发送提醒
        can_send = currently_triggered and not previously_triggered

        # 更新状态
        if not self.alerts.get("alert_states"):
            self.alerts["alert_states"] = {}

        self.alerts["alert_states"][key] = {
            "triggered": currently_triggered,
            "last_change_percent": change_percent,
            "last_update": datetime.now().isoformat(),
            "alert_id": alert.get("id", f"{stock_code}_{alert_type}")
        }
        self._save_alerts()

        return can_send

    def get_last_price_for_alert(self, alert: Dict) -> Optional[float]:
        """
        获取提醒的上次检查价格，用于计算价格变化幅度
        """
        user_id = alert["user_id"]
        stock_code = alert["stock_code"]
        alert_type = alert["alert_type"]

        key = f"{user_id}_{stock_code}_{alert_type}_last_price"
        last_price_data = self.alerts.get("price_history", {}).get(key)

        if last_price_data:
            # 检查是否在有效时间内（稍微超过检查间隔，以防误差）
            last_update = datetime.fromisoformat(last_price_data["timestamp"])
            max_age = timedelta(minutes=alert.get("interval_minutes", 5) + 2)  # 多2分钟容错
            if datetime.now() - last_update < max_age:
                return last_price_data["price"]

        return None

    def update_last_price_for_alert(self, alert: Dict, current_price: float):
        """
        更新提醒的上次检查价格
        """
        user_id = alert["user_id"]
        stock_code = alert["stock_code"]
        alert_type = alert["alert_type"]

        key = f"{user_id}_{stock_code}_{alert_type}_last_price"

        if not self.alerts.get("price_history"):
            self.alerts["price_history"] = {}

        self.alerts["price_history"][key] = {
            "price": current_price,
            "timestamp": datetime.now().isoformat()
        }
        self._save_alerts()

    async def send_alert_message(self, bot: telegram.Bot, chat_id: int, message: str):
        """异步发送提醒消息"""
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=telegram.constants.ParseMode.HTML
            )
            # 记录提醒历史
            self.record_alert_history(chat_id, message)
            return True
        except Exception as e:
            logger.error(f"发送提醒失败: {e}")
            return False

    def record_alert_history(self, user_id: int, message: str):
        """记录提醒历史"""
        alert_record = {
            "user_id": user_id,
            "message": message,
            "timestamp": datetime.now().isoformat()
        }
        self.alerts["alert_history"].append(alert_record)
        # 保留最近100条记录
        if len(self.alerts["alert_history"]) > 100:
            self.alerts["alert_history"] = self.alerts["alert_history"][-100:]
        self._save_alerts()

    def check_alerts_sync(self, fetcher: StockDataFetcher):
        """同步检查提醒并返回需要发送的消息列表（已废弃，使用异步版本）"""
        # 此方法已废弃，保留用于向后兼容
        logger.warning("警告：check_alerts_sync方法已废弃，请使用异步的check_alerts_async方法")
        return []


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

    async def setup_bot_commands(self):
        """设置机器人命令列表（在输入/时显示）"""
        commands = [
            telegram.BotCommand("start", "开始使用机器人"),
            telegram.BotCommand("help", "查看帮助信息"),
            telegram.BotCommand("add", "添加股票提醒"),
            telegram.BotCommand("list", "查看我的提醒列表"),
            telegram.BotCommand("remove", "删除股票提醒"),
        ]

        try:
            await self.bot.set_my_commands(commands)
            logger.info("Bot commands设置成功")
        except Exception as e:
            logger.error(f"设置Bot commands失败: {e}")

    def create_main_menu(self) -> InlineKeyboardMarkup:
        """创建主菜单键盘"""
        keyboard = [
            [
                InlineKeyboardButton("➕ 添加提醒", callback_data="menu_add"),
                InlineKeyboardButton("📋 查看提醒", callback_data="menu_list"),
            ],
            [
                InlineKeyboardButton("🗑️ 删除提醒", callback_data="menu_remove"),
                InlineKeyboardButton("❓ 帮助", callback_data="menu_help"),
            ],
            [
                InlineKeyboardButton("ℹ️ 关于", callback_data="menu_about"),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    def create_persistent_menu(self) -> ReplyKeyboardMarkup:
        """创建常驻菜单键盘"""
        keyboard = [
            [
                KeyboardButton("📋 查看提醒"),
                KeyboardButton("➕ 添加提醒"),
            ],
            [
                KeyboardButton("🗑️ 删除提醒"),
                KeyboardButton("❓ 帮助"),
            ]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /start 命令"""
        user = update.effective_user
        welcome_text = (
            f"👋 你好，{user.first_name}！\n"
            "我是股票价格提醒机器人。\n\n"
            "📱 请选择以下功能："
        )

        # 发送欢迎消息和主菜单
        reply_markup = self.create_main_menu()
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)

        # 设置常驻菜单
        persistent_menu = self.create_persistent_menu()
        await update.message.reply_text(
            "💡 现在您可以使用下方的常驻菜单快速操作：",
            reply_markup=persistent_menu
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

        # 验证股票代码是否存在
        await update.message.reply_text("🔍 验证股票代码中...")
        stock_data = self.fetcher.fetch_stock_data(stock_code)
        if not stock_data:
            await update.message.reply_text(
                f"❌ 股票代码 '{stock_code}' 无效或不存在。\n"
                "请检查股票代码是否正确。"
            )
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

            reply_markup = self.create_main_menu()
            await update.message.reply_text(
                f"✅ 成功添加提醒！\n"
                f"股票：{stock_display}\n"
                f"类型：{alert_type}\n"
                f"阈值：{threshold_str}（{direction_text}）\n"
                f"时间间隔：{interval_minutes}分钟",
                reply_markup=reply_markup
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

        # 按股票代码分组提醒
        stock_groups = {}
        for i, alert in enumerate(alerts):
            stock_code = alert['stock_code']
            if stock_code not in stock_groups:
                stock_groups[stock_code] = []
            stock_groups[stock_code].append((i, alert))

        message = "📋 你的股票提醒列表：\n\n"
        total_alerts = len(alerts)

        for stock_code, alert_list in stock_groups.items():
            # 获取股票名称
            stock_name = self.name_cache.get_stock_name(stock_code)
            if not stock_name:
                # 如果缓存中没有，尝试获取一次
                stock_data = self.fetcher.fetch_stock_data(stock_code)
                if stock_data:
                    stock_name = stock_data.get('name', '')

            stock_display = f"{stock_name} ({stock_code})" if stock_name else stock_code

            message += f"📈 {stock_display}\n"

            # 显示该股票的所有提醒
            alert_descriptions = []
            for alert_index, alert in alert_list:
                # 获取阈值方向显示
                threshold_direction = alert.get('threshold_direction', 'both')
                direction_symbols = {
                    'both': '±',
                    'up': '+',
                    'down': '-'
                }
                threshold_display = f"{direction_symbols[threshold_direction]}{alert['threshold']}"

                alert_type = alert['alert_type']
                interval_minutes = alert['interval_minutes']

                alert_desc = f"{alert_type}({threshold_display}%, {interval_minutes}分钟)"
                alert_descriptions.append(f"{alert_index + 1}. {alert_desc}")

            message += f"   提醒设置：{', '.join(alert_descriptions)}\n\n"

        # 显示最近提醒历史（按股票分组）
        user_alert_history = [h for h in self.alert_manager.alerts.get("alert_history", []) if h["user_id"] == user.id]
        if user_alert_history:
            # 按股票分组提醒历史
            stock_alert_history = {}
            for history in user_alert_history[-20:]:  # 显示最近20条
                # 从消息中提取股票代码
                message_lines = history["message"].split('\n')
                stock_line = next((line for line in message_lines if '📈 股票:' in line), '')
                if stock_line:
                    # 提取股票代码（格式：📈 股票: 名称 (代码)）
                    try:
                        stock_part = stock_line.split('(')[-1].rstrip(')')
                        stock_code = stock_part.strip()
                        if stock_code not in stock_alert_history:
                            stock_alert_history[stock_code] = []
                        stock_alert_history[stock_code].append(history)
                    except:
                        pass

            if stock_alert_history:
                message += "\n📅 最近提醒记录：\n"
                for stock_code, histories in stock_alert_history.items():
                    # 获取股票名称
                    stock_name = self.name_cache.get_stock_name(stock_code)
                    stock_display = f"{stock_name} ({stock_code})" if stock_name else stock_code

                    message += f"📈 {stock_display}：提醒了 {len(histories)} 次\n"

                    # 显示最近3次提醒时间
                    for i, history in enumerate(histories[-3:]):
                        try:
                            alert_time = datetime.fromisoformat(history["timestamp"])
                            time_str = alert_time.strftime("%m-%d %H:%M")
                            # 从消息中提取提醒类型
                            msg_lines = history["message"].split('\n')
                            alert_type_line = next((line for line in msg_lines if '🔔' in line), '')
                            if '涨跌幅提醒' in alert_type_line:
                                alert_type = "今日涨跌"
                            elif '价格变化提醒' in alert_type_line:
                                alert_type = "价格变化"
                            else:
                                alert_type = "提醒"
                            message += f"   • {time_str} {alert_type}\n"
                        except:
                            pass
                    message += "\n"

        message += f"📊 总计：{len(stock_groups)}只股票，{total_alerts}个提醒设置\n"
        message += "💡 使用「🗑️ 删除提醒」功能可以移除不需要的提醒。"
        reply_markup = self.create_main_menu()
        await update.message.reply_text(message, reply_markup=reply_markup)

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
            reply_markup = self.create_main_menu()
            await update.message.reply_text(f"✅ 成功移除提醒 {alert_id + 1}。", reply_markup=reply_markup)
        else:
            await update.message.reply_text("❌ 移除提醒失败。")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理回调查询（按钮点击等）"""
        query = update.callback_query
        await query.answer()

        callback_data = query.data

        if callback_data == "menu_add":
            # 显示添加提醒说明
            text = (
                "➕ 添加股票提醒\n\n"
                "请使用以下命令格式添加提醒：\n\n"
                "📝 基础格式：\n"
                "`/add 股票代码 提醒类型 阈值 时间间隔`\n\n"
                "📊 示例：\n"
                "`/add 600000 价格变化 2 5`\n"
                "`/add 000001 今日涨跌 5`\n\n"
                "🎯 参数说明：\n"
                "• 股票代码：如 600000、000001\n"
                "• 提醒类型：价格变化 / 今日涨跌\n"
                "• 阈值：百分比（如 2 表示 2%）\n"
                "• 时间间隔：分钟（可选，默认5分钟）\n\n"
                "💡 阈值格式：\n"
                "±2 或 2 = 双向提醒\n"
                "+2 = 只涨提醒\n"
                "-2 = 只跌提醒"
            )
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ 返回主菜单", callback_data="menu_main")]
            ])

        elif callback_data == "menu_list":
            # 显示提醒列表
            user = update.effective_user
            alerts = self.alert_manager.get_user_alerts(user.id)

            if not alerts:
                text = "📋 你还没有添加任何提醒。\n\n请先使用「➕ 添加提醒」功能添加新的股票提醒。"
            else:
                # 按股票代码分组提醒
                stock_groups = {}
                for i, alert in enumerate(alerts):
                    stock_code = alert['stock_code']
                    if stock_code not in stock_groups:
                        stock_groups[stock_code] = []
                    stock_groups[stock_code].append((i, alert))

                text = "📋 你的股票提醒列表：\n\n"
                total_alerts = len(alerts)

                for stock_code, alert_list in stock_groups.items():
                    # 获取股票名称
                    stock_name = self.name_cache.get_stock_name(stock_code)
                    if not stock_name:
                        # 如果缓存中没有，尝试获取一次
                        stock_data = self.fetcher.fetch_stock_data(stock_code)
                        if stock_data:
                            stock_name = stock_data.get('name', '')

                    stock_display = f"{stock_name} ({stock_code})" if stock_name else stock_code

                    text += f"📈 {stock_display}\n"

                    # 显示该股票的所有提醒
                    alert_descriptions = []
                    for alert_index, alert in alert_list:
                        # 获取阈值方向显示
                        threshold_direction = alert.get('threshold_direction', 'both')
                        direction_symbols = {
                            'both': '±',
                            'up': '+',
                            'down': '-'
                        }
                        threshold_display = f"{direction_symbols[threshold_direction]}{alert['threshold']}"

                        alert_type = alert['alert_type']
                        interval_minutes = alert['interval_minutes']

                        alert_desc = f"{alert_type}({threshold_display}%, {interval_minutes}分钟)"
                        alert_descriptions.append(f"{alert_index + 1}. {alert_desc}")

                    text += f"   提醒设置：{', '.join(alert_descriptions)}\n\n"

                # 显示最近提醒历史（按股票分组）
                user_alert_history = [h for h in self.alert_manager.alerts.get("alert_history", []) if h["user_id"] == user.id]
                if user_alert_history:
                    # 按股票分组提醒历史
                    stock_alert_history = {}
                    for history in user_alert_history[-20:]:  # 显示最近20条
                        # 从消息中提取股票代码
                        message_lines = history["message"].split('\n')
                        stock_line = next((line for line in message_lines if '📈 股票:' in line), '')
                        if stock_line:
                            # 提取股票代码（格式：📈 股票: 名称 (代码)）
                            try:
                                stock_part = stock_line.split('(')[-1].rstrip(')')
                                stock_code = stock_part.strip()
                                if stock_code not in stock_alert_history:
                                    stock_alert_history[stock_code] = []
                                stock_alert_history[stock_code].append(history)
                            except:
                                pass

                    if stock_alert_history:
                        text += "\n📅 最近提醒记录：\n"
                        for stock_code, histories in stock_alert_history.items():
                            # 获取股票名称
                            stock_name = self.name_cache.get_stock_name(stock_code)
                            stock_display = f"{stock_name} ({stock_code})" if stock_name else stock_code

                            text += f"📈 {stock_display}：提醒了 {len(histories)} 次\n"

                            # 显示最近3次提醒时间
                            for i, history in enumerate(histories[-3:]):
                                try:
                                    alert_time = datetime.fromisoformat(history["timestamp"])
                                    time_str = alert_time.strftime("%m-%d %H:%M")
                                    # 从消息中提取提醒类型
                                    msg_lines = history["message"].split('\n')
                                    alert_type_line = next((line for line in msg_lines if '🔔' in line), '')
                                    if '涨跌幅提醒' in alert_type_line:
                                        alert_type = "今日涨跌"
                                    elif '价格变化提醒' in alert_type_line:
                                        alert_type = "价格变化"
                                    else:
                                        alert_type = "提醒"
                                    text += f"   • {time_str} {alert_type}\n"
                                except:
                                    pass
                            text += "\n"

                text += f"📊 总计：{len(stock_groups)}只股票，{total_alerts}个提醒设置\n"
                text += "💡 使用「🗑️ 删除提醒」功能可以移除不需要的提醒。"

            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ 返回主菜单", callback_data="menu_main")]
            ])

        elif callback_data == "menu_remove":
            # 显示删除提醒说明
            text = (
                "🗑️ 删除股票提醒\n\n"
                "请使用以下命令删除提醒：\n\n"
                "📝 命令格式：\n"
                "`/remove 提醒编号`\n\n"
                "📊 示例：\n"
                "`/remove 1` - 删除第一个提醒\n"
                "`/remove 2` - 删除第二个提醒\n\n"
                "💡 查看提醒列表：\n"
                "先使用「📋 查看提醒」功能查看提醒编号，然后再删除。"
            )
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ 返回主菜单", callback_data="menu_main")]
            ])

        elif callback_data == "menu_help":
            # 显示帮助信息
            text = (
                "❓ 股票提醒机器人帮助\n\n"
                "📖 功能介绍：\n"
                "• 实时监控股票价格变化\n"
                "• 支持多种提醒条件设置\n"
                "• 智能交易时间判断\n"
                "• 多市场股票支持\n\n"
                "🎯 提醒类型：\n"
                "• 价格变化：监控短期价格波动\n"
                "• 今日涨跌：监控当日整体涨跌幅\n\n"
                "📊 支持市场：\n"
                "• 🇨🇳 A股市场（上海、深圳）\n"
                "• 🇭🇰 港股市场\n"
                "• 🇺🇸 美股市场\n\n"
                "⏰ 交易时间：\n"
                "• A股：周一至周五 9:30-11:30, 13:00-15:00\n"
                "• 港股：周一至周五 9:30-12:00, 13:00-16:00\n"
                "• 美股：周一至周五 21:30-04:00（北京时间）"
            )
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ 返回主菜单", callback_data="menu_main")]
            ])

        elif callback_data == "menu_about":
            # 显示关于信息
            text = (
                "ℹ️ 关于股票提醒机器人\n\n"
                "🤖 版本：v2.0\n"
                "📅 更新时间：2024年12月\n\n"
                "💡 特性：\n"
                "• 🚀 高性能异步处理\n"
                "• 💾 智能数据缓存\n"
                "• 🔄 实时价格监控\n"
                "• 📱 用户友好界面\n"
                "• 🛡️ 稳定可靠运行\n\n"
                "📊 数据来源：腾讯财经API\n"
                "⚡ 检查频率：每60秒\n"
                "💾 缓存有效期：30秒\n\n"
                "🌟 感谢使用！"
            )
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ 返回主菜单", callback_data="menu_main")]
            ])

        elif callback_data == "menu_main":
            # 返回主菜单
            user = update.effective_user
            text = (
                f"👋 你好，{user.first_name}！\n"
                "我是股票价格提醒机器人。\n\n"
                "📱 请选择以下功能："
            )
            reply_markup = self.create_main_menu()

        else:
            text = f"❌ 未知操作：{callback_data}"
            reply_markup = self.create_main_menu()

        await query.edit_message_text(text=text, reply_markup=reply_markup)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理普通消息"""
        text = update.message.text

        # 处理常驻菜单按钮点击
        if text == "📋 查看提醒":
            await self.list_alerts(update, context)
        elif text == "➕ 添加提醒":
            # 显示添加提醒说明
            text = (
                "➕ 添加股票提醒\n\n"
                "请使用以下命令格式添加提醒：\n\n"
                "📝 基础格式：\n"
                "`/add 股票代码 提醒类型 阈值 时间间隔`\n\n"
                "📊 示例：\n"
                "`/add 600000 价格变化 2 5`\n"
                "`/add 000001 今日涨跌 5`\n\n"
                "🎯 参数说明：\n"
                "• 股票代码：如 600000、000001\n"
                "• 提醒类型：价格变化 / 今日涨跌\n"
                "• 阈值：百分比（如 2 表示 2%）\n"
                "• 时间间隔：分钟（可选，默认5分钟）\n\n"
                "💡 阈值格式：\n"
                "±2 或 2 = 双向提醒\n"
                "+2 = 只涨提醒\n"
                "-2 = 只跌提醒"
            )
            await update.message.reply_text(text)
        elif text == "🗑️ 删除提醒":
            # 显示删除提醒说明
            text = (
                "🗑️ 删除股票提醒\n\n"
                "请使用以下命令删除提醒：\n\n"
                "📝 命令格式：\n"
                "`/remove 提醒编号`\n\n"
                "📊 示例：\n"
                "`/remove 1` - 删除第一个提醒\n"
                "`/remove 2` - 删除第二个提醒\n\n"
                "💡 查看提醒列表：\n"
                "先使用「📋 查看提醒」功能查看提醒编号，然后再删除。"
            )
            await update.message.reply_text(text)
        elif text == "❓ 帮助":
            await self.help(update, context)
        else:
            # 处理其他普通消息
            await update.message.reply_text(f"你发送了：{text}\n\n💡 使用下方的菜单按钮来操作机器人功能。")

    def start_polling(self):
        """启动机器人"""
        logger.info("启动股票提醒机器人...")

        try:
            self.app.run_polling()
        except Exception as e:
            logger.error(f"机器人启动失败: {e}")
            if "Conflict" in str(e):
                logger.warning("检测到冲突：可能是另一个机器人实例正在运行")
                logger.warning("请先停止其他机器人实例，然后重新启动")
            raise

    async def check_alerts_async(self):
        """异步检查提醒（使用批量获取和状态跟踪）"""
        try:
            # 监控日志暂时注释，只保留启动日志
            # current_time = datetime.now()
            # current_time_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
            # logger.info(f"[{current_time_str}] 开始检查提醒，共 {len(self.alert_manager.alerts['alerts'])} 个提醒")

            # 收集需要检查的股票代码（去重）
            stock_codes_to_check = list(set(alert["stock_code"] for alert in self.alert_manager.alerts["alerts"]))
            # logger.info(f"[{current_time_str}] 需要检查的股票数量: {len(stock_codes_to_check)}")

            # 批量获取股票数据
            stock_data_batch = self.fetcher.fetch_batch_stock_data(stock_codes_to_check)
            # logger.info(f"[{current_time_str}] 成功获取 {len([s for s in stock_data_batch.values() if s is not None])} 个股票数据")

            # 收集需要发送提醒的消息
            alerts_to_send = []

            for alert in self.alert_manager.alerts["alerts"]:
                stock_code = alert["stock_code"]
                stock_data = stock_data_batch.get(stock_code)

                # 检查是否在交易时间内
                is_trading = is_trading_time(stock_code)
                # logger.info(f"[{current_time_str}] 检查 {stock_code} 是否在交易时间内: {is_trading}")
                if not is_trading:
                    continue

                if not stock_data:
                    # logger.warning(f"[{current_time_str}] 获取 {stock_code} 数据失败")
                    continue

                # logger.info(f"[{current_time_str}] {stock_code} 价格: {stock_data.get('current_price', 0)}, 涨跌幅: {stock_data.get('change_percent', 0)}%")

                # 检查提醒条件
                alert_triggered = False
                message = ""

                if alert["alert_type"] == "价格变化":
                    # 价格变化提醒 - 计算最近N分钟内的价格变化幅度
                    current_price = stock_data.get("current_price", 0)
                    last_price = self.alert_manager.get_last_price_for_alert(alert)

                    if last_price and last_price > 0:
                        # 计算价格变化幅度
                        price_change = current_price - last_price
                        change_percent = (price_change / last_price) * 100
                        change_percent = round(change_percent, 2)

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

                            # 更新价格历史
                            self.alert_manager.update_last_price_for_alert(alert, current_price)

                            # 获取更详细的股票信息
                            prev_close = stock_data.get("prev_close", 0)
                            daily_change = stock_data.get("change_percent", 0)
                            volume = stock_data.get("volume", 0)
                            high_price = stock_data.get("high_price", 0)
                            low_price = stock_data.get("low_price", 0)

                            alert_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                            message = (f"🔔 价格变化提醒\n"
                                       f"⏰ 提醒时间: {alert_time}\n"
                                       f"📈 股票: {stock_data['name']} ({stock_data['code']})\n"
                                       f"💰 当前价格: ¥{current_price}\n"
                                       f"📊 {direction_desc}: {abs(change_percent)}% (¥{abs(price_change):.2f})\n"
                                       f"🎯 阈值: {alert['threshold']}%\n"
                                       f"📅 昨收: ¥{prev_close} ({'+' if daily_change >= 0 else ''}{daily_change}%)\n"
                                       f"📈 今日最高: ¥{high_price}\n"
                                       f"📉 今日最低: ¥{low_price}\n"
                                       f"📊 成交量: {volume:,} 手")
                    else:
                        # 如果没有历史价格，记录当前价格作为基准
                        self.alert_manager.update_last_price_for_alert(alert, current_price)

                elif alert["alert_type"] == "今日涨跌":
                    # 今日涨跌幅提醒 - 使用新的状态跟踪逻辑
                    change_percent = stock_data.get("change_percent", 0)
                    can_send_daily = self.alert_manager.can_send_daily_change_alert(alert, change_percent)

                    if can_send_daily:
                        alert_triggered = True
                        threshold_direction = alert.get("threshold_direction", "both")
                        direction = "上涨" if change_percent > 0 else "下跌"
                        direction_desc = {
                            'both': f"今日{direction}幅",
                            'up': "今日涨幅",
                            'down': "今日跌幅"
                        }[threshold_direction]

                        # 获取更详细的股票信息
                        current_price = stock_data.get("current_price", 0)
                        prev_close = stock_data.get("prev_close", 0)
                        volume = stock_data.get("volume", 0)
                        high_price = stock_data.get("high_price", 0)
                        low_price = stock_data.get("low_price", 0)
                        price_change = stock_data.get("change", 0)

                        alert_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                        message = (f"🔔 今日涨跌幅提醒\n"
                                   f"⏰ 提醒时间: {alert_time}\n"
                                   f"📈 股票: {stock_data['name']} ({stock_data['code']})\n"
                                   f"💰 当前价格: ¥{current_price}\n"
                                   f"📊 {direction_desc}: {abs(change_percent)}% (¥{abs(price_change):.2f})\n"
                                   f"🎯 阈值: {alert['threshold']}%\n"
                                   f"📅 昨收: ¥{prev_close}\n"
                                   f"📈 今日最高: ¥{high_price}\n"
                                   f"📉 今日最低: ¥{low_price}\n"
                                   f"📊 成交量: {volume:,} 手")

                        # logger.info(f"[{current_time_str}] {stock_code} 今日涨跌提醒触发: 涨跌幅={change_percent}%, 阈值={alert['threshold']}%")

                # 检查是否可以发送提醒（价格变化类型使用时间间隔，今日涨跌类型使用状态跟踪）
                if alert_triggered:
                    if alert["alert_type"] == "价格变化":
                        can_send = self.alert_manager.can_send_alert(alert)
                        # logger.info(f"[{current_time_str}] {stock_code} 价格变化提醒，检查发送权限: {can_send}")
                    else:  # 今日涨跌类型已经通过状态跟踪检查过了
                        can_send = True
                        # logger.info(f"[{current_time_str}] {stock_code} 今日涨跌提醒，状态跟踪通过")

                    if can_send:
                        alerts_to_send.append((alert["user_id"], message, stock_code))
                        # logger.info(f"[{current_time_str}] {stock_code} 准备发送提醒消息: {message[:50]}...")
                    else:
                        # logger.info(f"[{current_time_str}] {stock_code} 因时间间隔限制跳过提醒")
                        pass

            # 批量发送提醒消息
            if alerts_to_send:
                # logger.info(f"[{current_time_str}] 开始批量发送 {len(alerts_to_send)} 条提醒消息")
                for chat_id, message, stock_code in alerts_to_send:
                    try:
                        success = await self.alert_manager.send_alert_message(self.bot, chat_id, message)
                        if success:
                            # logger.info(f"[{current_time_str}] {stock_code} 提醒消息发送成功")
                            pass
                        else:
                            # logger.warning(f"[{current_time_str}] {stock_code} 提醒消息发送失败")
                            pass
                    except Exception as e:
                        # logger.error(f"[{current_time_str}] {stock_code} 发送提醒异常: {e}")
                        pass
                # logger.info(f"[{current_time_str}] 批量发送完成")

        except Exception as e:
            logger.error(f"异步检查提醒时出错: {e}", exc_info=True)

    async def check_alerts_job(self, context):
        """Job队列调用的提醒检查函数"""
        await self.check_alerts_async()

    def start_checking_alerts(self):
        """启动定期检查提醒"""
        # 使用Telegram Application的job_queue来处理定期任务
        self.app.job_queue.run_repeating(
            self.check_alerts_job,
            interval=CONFIG["check_interval"],
            first=10  # 10秒后开始第一次检查
        )
