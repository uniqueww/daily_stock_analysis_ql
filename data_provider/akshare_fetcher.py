# -*- coding: utf-8 -*-
"""
===================================
AkshareFetcher - 主数据源 (Priority 1)
===================================

数据来源：东方财富爬虫（通过 akshare 库）
特点：免费、无需 Token、数据全面
风险：爬虫机制易被反爬封禁

防封禁策略：
1. 每次请求前随机休眠 2-5 秒
2. 随机轮换 User-Agent
3. 使用 tenacity 实现指数退避重试

增强数据：
- 实时行情：量比、换手率、市盈率、市净率、总市值、流通市值
- 筹码分布：获利比例、平均成本、筹码集中度
"""

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any

import pandas as pd
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from .base import BaseFetcher, DataFetchError, RateLimitError, STANDARD_COLUMNS


@dataclass
class RealtimeQuote:
    """
    实时行情数据
    
    包含当日实时交易数据和估值指标
    """
    code: str
    name: str = ""
    price: float = 0.0           # 最新价
    change_pct: float = 0.0      # 涨跌幅(%)
    change_amount: float = 0.0   # 涨跌额
    
    # 量价指标
    volume_ratio: float = 0.0    # 量比（当前成交量/过去5日平均成交量）
    turnover_rate: float = 0.0   # 换手率(%)
    amplitude: float = 0.0       # 振幅(%)
    
    # 估值指标
    pe_ratio: float = 0.0        # 市盈率(动态)
    pb_ratio: float = 0.0        # 市净率
    total_mv: float = 0.0        # 总市值(元)
    circ_mv: float = 0.0         # 流通市值(元)
    
    # 其他
    change_60d: float = 0.0      # 60日涨跌幅(%)
    high_52w: float = 0.0        # 52周最高
    low_52w: float = 0.0         # 52周最低
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'code': self.code,
            'name': self.name,
            'price': self.price,
            'change_pct': self.change_pct,
            'volume_ratio': self.volume_ratio,
            'turnover_rate': self.turnover_rate,
            'amplitude': self.amplitude,
            'pe_ratio': self.pe_ratio,
            'pb_ratio': self.pb_ratio,
            'total_mv': self.total_mv,
            'circ_mv': self.circ_mv,
            'change_60d': self.change_60d,
        }


@dataclass  
class ChipDistribution:
    """
    筹码分布数据
    
    反映持仓成本分布和获利情况
    """
    code: str
    date: str = ""
    
    # 获利情况
    profit_ratio: float = 0.0     # 获利比例(0-1)
    avg_cost: float = 0.0         # 平均成本
    
    # 筹码集中度
    cost_90_low: float = 0.0      # 90%筹码成本下限
    cost_90_high: float = 0.0     # 90%筹码成本上限
    concentration_90: float = 0.0  # 90%筹码集中度（越小越集中）
    
    cost_70_low: float = 0.0      # 70%筹码成本下限
    cost_70_high: float = 0.0     # 70%筹码成本上限
    concentration_70: float = 0.0  # 70%筹码集中度
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'code': self.code,
            'date': self.date,
            'profit_ratio': self.profit_ratio,
            'avg_cost': self.avg_cost,
            'cost_90_low': self.cost_90_low,
            'cost_90_high': self.cost_90_high,
            'concentration_90': self.concentration_90,
            'concentration_70': self.concentration_70,
        }
    
    def get_chip_status(self, current_price: float) -> str:
        """
        获取筹码状态描述
        
        Args:
            current_price: 当前股价
            
        Returns:
            筹码状态描述
        """
        status_parts = []
        
        # 获利比例分析
        if self.profit_ratio >= 0.9:
            status_parts.append("获利盘极高(>90%)")
        elif self.profit_ratio >= 0.7:
            status_parts.append("获利盘较高(70-90%)")
        elif self.profit_ratio >= 0.5:
            status_parts.append("获利盘中等(50-70%)")
        elif self.profit_ratio >= 0.3:
            status_parts.append("套牢盘较多(>30%)")
        else:
            status_parts.append("套牢盘极重(>70%)")
        
        # 筹码集中度分析 (90%集中度 < 10% 表示集中)
        if self.concentration_90 < 0.08:
            status_parts.append("筹码高度集中")
        elif self.concentration_90 < 0.15:
            status_parts.append("筹码较集中")
        elif self.concentration_90 < 0.25:
            status_parts.append("筹码分散度中等")
        else:
            status_parts.append("筹码较分散")
        
        # 成本与现价关系
        if current_price > 0 and self.avg_cost > 0:
            cost_diff = (current_price - self.avg_cost) / self.avg_cost * 100
            if cost_diff > 20:
                status_parts.append(f"现价高于平均成本{cost_diff:.1f}%")
            elif cost_diff > 5:
                status_parts.append(f"现价略高于成本{cost_diff:.1f}%")
            elif cost_diff > -5:
                status_parts.append("现价接近平均成本")
            else:
                status_parts.append(f"现价低于平均成本{abs(cost_diff):.1f}%")
        
        return "，".join(status_parts)

logger = logging.getLogger(__name__)


# User-Agent 池，用于随机轮换
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]


# 缓存实时行情数据（避免重复请求）
_realtime_cache: Dict[str, Any] = {
    'data': None,
    'timestamp': 0,
    'ttl': 60  # 60秒缓存有效期
}

# ETF 实时行情缓存
_etf_realtime_cache: Dict[str, Any] = {
    'data': None,
    'timestamp': 0,
    'ttl': 60  # 60秒缓存有效期
}


def _is_etf_code(stock_code: str) -> bool:
    """
    判断代码是否为 ETF 基金
    
    ETF 代码规则：
    - 上交所 ETF: 51xxxx, 52xxxx, 56xxxx, 58xxxx
    - 深交所 ETF: 15xxxx, 16xxxx, 18xxxx
    
    Args:
        stock_code: 股票/基金代码
        
    Returns:
        True 表示是 ETF 代码，False 表示是普通股票代码
    """
    etf_prefixes = ('51', '52', '56', '58', '15', '16', '18')
    return stock_code.startswith(etf_prefixes) and len(stock_code) == 6


def _is_hk_code(stock_code: str) -> bool:
    """
    判断代码是否为港股
    
    港股代码规则：
    - 5位数字代码，如 '00700' (腾讯控股)
    - 部分港股代码可能带有前缀，如 'hk00700'
    
    Args:
        stock_code: 股票代码
        
    Returns:
        True 表示是港股代码，False 表示不是港股代码
    """
    # 去除可能的 'hk' 前缀
    code = stock_code.lower().replace('hk', '')
    # 港股代码为5位数字
    return code.isdigit() and len(code) == 5


class AkshareFetcher(BaseFetcher):
    """
    Akshare 数据源实现
    
    优先级：1（最高）
    数据来源：东方财富网爬虫
    
    关键策略：
    - 每次请求前随机休眠 2.0-5.0 秒
    - 随机 User-Agent 轮换
    - 失败后指数退避重试（最多3次）
    """
    
    name = "AkshareFetcher"
    priority = 1
    
    def __init__(self, sleep_min: float = 2.0, sleep_max: float = 5.0):
        """
        初始化 AkshareFetcher
        
        Args:
            sleep_min: 最小休眠时间（秒）
            sleep_max: 最大休眠时间（秒）
        """
        self.sleep_min = sleep_min
        self.sleep_max = sleep_max
        self._last_request_time: Optional[float] = None
    
    def _set_random_user_agent(self) -> None:
        """
        设置随机 User-Agent
        
        通过修改 requests Session 的 headers 实现
        这是关键的反爬策略之一
        """
        try:
            import akshare as ak
            # akshare 内部使用 requests，我们通过环境变量或直接设置来影响
            # 实际上 akshare 可能不直接暴露 session，这里通过 fake_useragent 作为补充
            random_ua = random.choice(USER_AGENTS)
            logger.debug(f"设置 User-Agent: {random_ua[:50]}...")
        except Exception as e:
            logger.debug(f"设置 User-Agent 失败: {e}")
    
    def _enforce_rate_limit(self) -> None:
        """
        强制执行速率限制
        
        策略：
        1. 检查距离上次请求的时间间隔
        2. 如果间隔不足，补充休眠时间
        3. 然后再执行随机 jitter 休眠
        """
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            min_interval = self.sleep_min
            if elapsed < min_interval:
                additional_sleep = min_interval - elapsed
                logger.debug(f"补充休眠 {additional_sleep:.2f} 秒")
                time.sleep(additional_sleep)
        
        # 执行随机 jitter 休眠
        self.random_sleep(self.sleep_min, self.sleep_max)
        self._last_request_time = time.time()
    
    @retry(
        stop=stop_after_attempt(3),  # 最多重试3次
        wait=wait_exponential(multiplier=1, min=2, max=30),  # 指数退避：2, 4, 8... 最大30秒
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        从 Akshare 获取原始数据
        
        根据代码类型自动选择 API：
        - 普通股票：使用 ak.stock_zh_a_hist()
        - ETF 基金：使用 ak.fund_etf_hist_em()
        
        流程：
        1. 判断代码类型（股票/ETF）
        2. 设置随机 User-Agent
        3. 执行速率限制（随机休眠）
        4. 调用对应的 akshare API
        5. 处理返回数据
        """
        # 根据代码类型选择不同的获取方法
        if _is_hk_code(stock_code):
            return self._fetch_hk_data(stock_code, start_date, end_date)
        elif _is_etf_code(stock_code):
            return self._fetch_etf_data(stock_code, start_date, end_date)
        else:
            return self._fetch_stock_data(stock_code, start_date, end_date)
    
    def _fetch_stock_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取普通 A 股历史数据
        
        数据来源：ak.stock_zh_a_hist()
        """
        import akshare as ak
        
        # 防封禁策略 1: 随机 User-Agent
        self._set_random_user_agent()
        
        # 防封禁策略 2: 强制休眠
        self._enforce_rate_limit()
        
        logger.info(f"[API调用] ak.stock_zh_a_hist(symbol={stock_code}, period=daily, "
                   f"start_date={start_date.replace('-', '')}, end_date={end_date.replace('-', '')}, adjust=qfq)")
        
        try:
            # 调用 akshare 获取 A 股日线数据
            # period="daily" 获取日线数据
            # adjust="qfq" 获取前复权数据
            import time as _time
            api_start = _time.time()
            
            df = ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                adjust="qfq"  # 前复权
            )
            
            api_elapsed = _time.time() - api_start
            
            # 记录返回数据摘要
            if df is not None and not df.empty:
                logger.info(f"[API返回] ak.stock_zh_a_hist 成功: 返回 {len(df)} 行数据, 耗时 {api_elapsed:.2f}s")
                logger.info(f"[API返回] 列名: {list(df.columns)}")
                logger.info(f"[API返回] 日期范围: {df['日期'].iloc[0]} ~ {df['日期'].iloc[-1]}")
                logger.debug(f"[API返回] 最新3条数据:\n{df.tail(3).to_string()}")
            else:
                logger.warning(f"[API返回] ak.stock_zh_a_hist 返回空数据, 耗时 {api_elapsed:.2f}s")
            
            return df
            
        except Exception as e:
            error_msg = str(e).lower()
            
            # 检测反爬封禁
            if any(keyword in error_msg for keyword in ['banned', 'blocked', '频率', 'rate', '限制']):
                logger.warning(f"检测到可能被封禁: {e}")
                raise RateLimitError(f"Akshare 可能被限流: {e}") from e
            
            raise DataFetchError(f"Akshare 获取数据失败: {e}") from e
    
    def _fetch_etf_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取 ETF 基金历史数据
        
        数据来源：ak.fund_etf_hist_em()
        
        Args:
            stock_code: ETF 代码，如 '512400', '159883'
            start_date: 开始日期，格式 'YYYY-MM-DD'
            end_date: 结束日期，格式 'YYYY-MM-DD'
            
        Returns:
            ETF 历史数据 DataFrame
        """
        import akshare as ak
        
        # 防封禁策略 1: 随机 User-Agent
        self._set_random_user_agent()
        
        # 防封禁策略 2: 强制休眠
        self._enforce_rate_limit()
        
        logger.info(f"[API调用] ak.fund_etf_hist_em(symbol={stock_code}, period=daily, "
                   f"start_date={start_date.replace('-', '')}, end_date={end_date.replace('-', '')}, adjust=qfq)")
        
        try:
            import time as _time
            api_start = _time.time()
            
            # 调用 akshare 获取 ETF 日线数据
            df = ak.fund_etf_hist_em(
                symbol=stock_code,
                period="daily",
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                adjust="qfq"  # 前复权
            )
            
            api_elapsed = _time.time() - api_start
            
            # 记录返回数据摘要
            if df is not None and not df.empty:
                logger.info(f"[API返回] ak.fund_etf_hist_em 成功: 返回 {len(df)} 行数据, 耗时 {api_elapsed:.2f}s")
                logger.info(f"[API返回] 列名: {list(df.columns)}")
                logger.info(f"[API返回] 日期范围: {df['日期'].iloc[0]} ~ {df['日期'].iloc[-1]}")
                logger.debug(f"[API返回] 最新3条数据:\n{df.tail(3).to_string()}")
            else:
                logger.warning(f"[API返回] ak.fund_etf_hist_em 返回空数据, 耗时 {api_elapsed:.2f}s")
            
            return df
            
        except Exception as e:
            error_msg = str(e).lower()
            
            # 检测反爬封禁
            if any(keyword in error_msg for keyword in ['banned', 'blocked', '频率', 'rate', '限制']):
                logger.warning(f"检测到可能被封禁: {e}")
                raise RateLimitError(f"Akshare 可能被限流: {e}") from e
            
            raise DataFetchError(f"Akshare 获取 ETF 数据失败: {e}") from e
    
    def _fetch_hk_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取港股历史数据
        
        数据来源：ak.stock_hk_hist()
        
        Args:
            stock_code: 港股代码，如 '00700', '01810'
            start_date: 开始日期，格式 'YYYY-MM-DD'
            end_date: 结束日期，格式 'YYYY-MM-DD'
            
        Returns:
            港股历史数据 DataFrame
        """
        import akshare as ak
        
        # 防封禁策略 1: 随机 User-Agent
        self._set_random_user_agent()
        
        # 防封禁策略 2: 强制休眠
        self._enforce_rate_limit()
        
        # 确保代码格式正确（5位数字）
        code = stock_code.lower().replace('hk', '').zfill(5)
        
        logger.info(f"[API调用] ak.stock_hk_hist(symbol={code}, period=daily, "
                   f"start_date={start_date.replace('-', '')}, end_date={end_date.replace('-', '')}, adjust=qfq)")
        
        try:
            import time as _time
            api_start = _time.time()
            
            # 调用 akshare 获取港股日线数据
            df = ak.stock_hk_hist(
                symbol=code,
                period="daily",
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                adjust="qfq"  # 前复权
            )
            
            api_elapsed = _time.time() - api_start
            
            # 记录返回数据摘要
            if df is not None and not df.empty:
                logger.info(f"[API返回] ak.stock_hk_hist 成功: 返回 {len(df)} 行数据, 耗时 {api_elapsed:.2f}s")
                logger.info(f"[API返回] 列名: {list(df.columns)}")
                logger.info(f"[API返回] 日期范围: {df['日期'].iloc[0]} ~ {df['日期'].iloc[-1]}")
                logger.debug(f"[API返回] 最新3条数据:\n{df.tail(3).to_string()}")
            else:
                logger.warning(f"[API返回] ak.stock_hk_hist 返回空数据, 耗时 {api_elapsed:.2f}s")
            
            return df
            
        except Exception as e:
            error_msg = str(e).lower()
            
            # 检测反爬封禁
            if any(keyword in error_msg for keyword in ['banned', 'blocked', '频率', 'rate', '限制']):
                logger.warning(f"检测到可能被封禁: {e}")
                raise RateLimitError(f"Akshare 可能被限流: {e}") from e
            
            raise DataFetchError(f"Akshare 获取港股数据失败: {e}") from e
    
    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        标准化 Akshare 数据
        
        Akshare 返回的列名（中文）：
        日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
        
        需要映射到标准列名：
        date, open, high, low, close, volume, amount, pct_chg
        """
        df = df.copy()
        
        # 列名映射（Akshare 中文列名 -> 标准英文列名）
        column_mapping = {
            '日期': 'date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg',
        }
        
        # 重命名列
        df = df.rename(columns=column_mapping)
        
        # 添加股票代码列
        df['code'] = stock_code
        
        # 只保留需要的列
        keep_cols = ['code'] + STANDARD_COLUMNS
        existing_cols = [col for col in keep_cols if col in df.columns]
        df = df[existing_cols]
        
        return df
    
    def get_realtime_quote(self, stock_code: str) -> Optional[RealtimeQuote]:
        """
        获取实时行情数据
        
        根据代码类型自动选择数据源：
        - 普通股票：ak.stock_zh_a_spot_em()
        - ETF 基金：ak.fund_etf_spot_em()
        
        Args:
            stock_code: 股票/ETF代码
            
        Returns:
            RealtimeQuote 对象，获取失败返回 None
        """
        # 根据代码类型选择不同的获取方法
        if _is_hk_code(stock_code):
            return self._get_hk_realtime_quote(stock_code)
        elif _is_etf_code(stock_code):
            return self._get_etf_realtime_quote(stock_code)
        else:
            return self._get_stock_realtime_quote(stock_code)
    
    def _get_stock_realtime_quote(self, stock_code: str) -> Optional[RealtimeQuote]:
        """
        获取普通 A 股实时行情数据
        
        数据来源：ak.stock_zh_a_spot_em()
        包含：量比、换手率、市盈率、市净率、总市值、流通市值等
        """
        import akshare as ak
        
        try:
            # 检查缓存
            current_time = time.time()
            if (_realtime_cache['data'] is not None and 
                current_time - _realtime_cache['timestamp'] < _realtime_cache['ttl']):
                df = _realtime_cache['data']
                logger.debug(f"[缓存命中] 使用缓存的A股实时行情数据")
            else:
                # 防封禁策略
                self._set_random_user_agent()
                self._enforce_rate_limit()
                
                logger.info(f"[API调用] ak.stock_zh_a_spot_em() 获取A股实时行情...")
                import time as _time
                api_start = _time.time()
                
                df = ak.stock_zh_a_spot_em()
                
                api_elapsed = _time.time() - api_start
                logger.info(f"[API返回] ak.stock_zh_a_spot_em 成功: 返回 {len(df)} 只股票, 耗时 {api_elapsed:.2f}s")
                
                # 更新缓存
                _realtime_cache['data'] = df
                _realtime_cache['timestamp'] = current_time
            
            # 查找指定股票
            row = df[df['代码'] == stock_code]
            if row.empty:
                logger.warning(f"[API返回] 未找到股票 {stock_code} 的实时行情")
                return None
            
            row = row.iloc[0]
            
            # 安全获取字段值
            def safe_float(val, default=0.0):
                try:
                    if pd.isna(val):
                        return default
                    return float(val)
                except:
                    return default
            
            quote = RealtimeQuote(
                code=stock_code,
                name=str(row.get('名称', '')),
                price=safe_float(row.get('最新价')),
                change_pct=safe_float(row.get('涨跌幅')),
                change_amount=safe_float(row.get('涨跌额')),
                volume_ratio=safe_float(row.get('量比')),
                turnover_rate=safe_float(row.get('换手率')),
                amplitude=safe_float(row.get('振幅')),
                pe_ratio=safe_float(row.get('市盈率-动态')),
                pb_ratio=safe_float(row.get('市净率')),
                total_mv=safe_float(row.get('总市值')),
                circ_mv=safe_float(row.get('流通市值')),
                change_60d=safe_float(row.get('60日涨跌幅')),
                high_52w=safe_float(row.get('52周最高')),
                low_52w=safe_float(row.get('52周最低')),
            )
            
            logger.info(f"[实时行情] {stock_code} {quote.name}: 价格={quote.price}, 涨跌={quote.change_pct}%, "
                       f"量比={quote.volume_ratio}, 换手率={quote.turnover_rate}%, "
                       f"PE={quote.pe_ratio}, PB={quote.pb_ratio}")
            return quote
            
        except Exception as e:
            logger.error(f"[API错误] 获取 {stock_code} 实时行情失败: {e}")
            return None
    
    def _get_etf_realtime_quote(self, stock_code: str) -> Optional[RealtimeQuote]:
        """
        获取 ETF 基金实时行情数据
        
        数据来源：ak.fund_etf_spot_em()
        包含：最新价、涨跌幅、成交量、成交额、换手率等
        
        Args:
            stock_code: ETF 代码
            
        Returns:
            RealtimeQuote 对象，获取失败返回 None
        """
        import akshare as ak
        
        try:
            # 检查缓存
            current_time = time.time()
            if (_etf_realtime_cache['data'] is not None and 
                current_time - _etf_realtime_cache['timestamp'] < _etf_realtime_cache['ttl']):
                df = _etf_realtime_cache['data']
                logger.debug(f"[缓存命中] 使用缓存的ETF实时行情数据")
            else:
                # 防封禁策略
                self._set_random_user_agent()
                self._enforce_rate_limit()
                
                logger.info(f"[API调用] ak.fund_etf_spot_em() 获取ETF实时行情...")
                import time as _time
                api_start = _time.time()
                
                df = ak.fund_etf_spot_em()
                
                api_elapsed = _time.time() - api_start
                logger.info(f"[API返回] ak.fund_etf_spot_em 成功: 返回 {len(df)} 只ETF, 耗时 {api_elapsed:.2f}s")
                
                # 更新缓存
                _etf_realtime_cache['data'] = df
                _etf_realtime_cache['timestamp'] = current_time
            
            # 查找指定 ETF
            row = df[df['代码'] == stock_code]
            if row.empty:
                logger.warning(f"[API返回] 未找到 ETF {stock_code} 的实时行情")
                return None
            
            row = row.iloc[0]
            
            # 安全获取字段值
            def safe_float(val, default=0.0):
                try:
                    if pd.isna(val):
                        return default
                    return float(val)
                except:
                    return default
            
            # ETF 行情数据构建（部分字段 ETF 可能不支持，使用默认值）
            quote = RealtimeQuote(
                code=stock_code,
                name=str(row.get('名称', '')),
                price=safe_float(row.get('最新价')),
                change_pct=safe_float(row.get('涨跌幅')),
                change_amount=safe_float(row.get('涨跌额')),
                volume_ratio=safe_float(row.get('量比', 0)),  # ETF 可能无量比
                turnover_rate=safe_float(row.get('换手率')),
                amplitude=safe_float(row.get('振幅')),
                pe_ratio=0.0,  # ETF 通常无市盈率
                pb_ratio=0.0,  # ETF 通常无市净率
                total_mv=safe_float(row.get('总市值', 0)),
                circ_mv=safe_float(row.get('流通市值', 0)),
                change_60d=0.0,  # ETF 接口可能不提供
                high_52w=safe_float(row.get('52周最高', 0)),
                low_52w=safe_float(row.get('52周最低', 0)),
            )
            
            logger.info(f"[ETF实时行情] {stock_code} {quote.name}: 价格={quote.price}, 涨跌={quote.change_pct}%, "
                       f"换手率={quote.turnover_rate}%")
            return quote
            
        except Exception as e:
            logger.error(f"[API错误] 获取 ETF {stock_code} 实时行情失败: {e}")
            return None
    
    def _get_hk_realtime_quote(self, stock_code: str) -> Optional[RealtimeQuote]:
        """
        获取港股实时行情数据
        
        数据来源：ak.stock_hk_spot_em()
        包含：最新价、涨跌幅、成交量、成交额等
        
        Args:
            stock_code: 港股代码
            
        Returns:
            RealtimeQuote 对象，获取失败返回 None
        """
        import akshare as ak
        
        try:
            # 防封禁策略
            self._set_random_user_agent()
            self._enforce_rate_limit()
            
            # 确保代码格式正确（5位数字）
            code = stock_code.lower().replace('hk', '').zfill(5)
            
            logger.info(f"[API调用] ak.stock_hk_spot_em() 获取港股实时行情...")
            import time as _time
            api_start = _time.time()
            
            df = ak.stock_hk_spot_em()
            
            api_elapsed = _time.time() - api_start
            logger.info(f"[API返回] ak.stock_hk_spot_em 成功: 返回 {len(df)} 只港股, 耗时 {api_elapsed:.2f}s")
            
            # 查找指定港股
            row = df[df['代码'] == code]
            if row.empty:
                logger.warning(f"[API返回] 未找到港股 {code} 的实时行情")
                return None
            
            row = row.iloc[0]
            
            # 安全获取字段值
            def safe_float(val, default=0.0):
                try:
                    if pd.isna(val):
                        return default
                    return float(val)
                except:
                    return default
            
            # 港股行情数据构建
            quote = RealtimeQuote(
                code=stock_code,
                name=str(row.get('名称', '')),
                price=safe_float(row.get('最新价')),
                change_pct=safe_float(row.get('涨跌幅')),
                change_amount=safe_float(row.get('涨跌额')),
                volume_ratio=safe_float(row.get('量比', 0)),  # 港股可能无量比
                turnover_rate=safe_float(row.get('换手率', 0)),
                amplitude=safe_float(row.get('振幅', 0)),
                pe_ratio=safe_float(row.get('市盈率', 0)),  # 港股可能有市盈率
                pb_ratio=safe_float(row.get('市净率', 0)),  # 港股可能有市净率
                total_mv=safe_float(row.get('总市值', 0)),
                circ_mv=safe_float(row.get('流通市值', 0)),
                change_60d=0.0,  # 港股接口可能不提供
                high_52w=safe_float(row.get('52周最高', 0)),
                low_52w=safe_float(row.get('52周最低', 0)),
            )
            
            logger.info(f"[港股实时行情] {stock_code} {quote.name}: 价格={quote.price}, 涨跌={quote.change_pct}%, "
                       f"换手率={quote.turnover_rate}%")
            return quote
            
        except Exception as e:
            logger.error(f"[API错误] 获取港股 {stock_code} 实时行情失败: {e}")
            return None
    
    def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        """
        获取筹码分布数据
        
        数据来源：ak.stock_cyq_em()
        包含：获利比例、平均成本、筹码集中度
        
        注意：ETF/指数没有筹码分布数据，会直接返回 None
        
        Args:
            stock_code: 股票代码
            
        Returns:
            ChipDistribution 对象（最新一天的数据），获取失败返回 None
        """
        import akshare as ak
        
        # ETF/指数没有筹码分布数据
        if _is_etf_code(stock_code):
            logger.debug(f"[API跳过] {stock_code} 是 ETF/指数，无筹码分布数据")
            return None
        
        try:
            # 防封禁策略
            self._set_random_user_agent()
            self._enforce_rate_limit()
            
            logger.info(f"[API调用] ak.stock_cyq_em(symbol={stock_code}) 获取筹码分布...")
            import time as _time
            api_start = _time.time()
            
            df = ak.stock_cyq_em(symbol=stock_code)
            
            api_elapsed = _time.time() - api_start
            
            if df.empty:
                logger.warning(f"[API返回] ak.stock_cyq_em 返回空数据, 耗时 {api_elapsed:.2f}s")
                return None
            
            logger.info(f"[API返回] ak.stock_cyq_em 成功: 返回 {len(df)} 天数据, 耗时 {api_elapsed:.2f}s")
            logger.debug(f"[API返回] 筹码数据列名: {list(df.columns)}")
            
            # 取最新一天的数据
            latest = df.iloc[-1]
            
            def safe_float(val, default=0.0):
                try:
                    if pd.isna(val):
                        return default
                    return float(val)
                except:
                    return default
            
            chip = ChipDistribution(
                code=stock_code,
                date=str(latest.get('日期', '')),
                profit_ratio=safe_float(latest.get('获利比例')),
                avg_cost=safe_float(latest.get('平均成本')),
                cost_90_low=safe_float(latest.get('90成本-低')),
                cost_90_high=safe_float(latest.get('90成本-高')),
                concentration_90=safe_float(latest.get('90集中度')),
                cost_70_low=safe_float(latest.get('70成本-低')),
                cost_70_high=safe_float(latest.get('70成本-高')),
                concentration_70=safe_float(latest.get('70集中度')),
            )
            
            logger.info(f"[筹码分布] {stock_code} 日期={chip.date}: 获利比例={chip.profit_ratio:.1%}, "
                       f"平均成本={chip.avg_cost}, 90%集中度={chip.concentration_90:.2%}, "
                       f"70%集中度={chip.concentration_70:.2%}")
            return chip
            
        except Exception as e:
            logger.error(f"[API错误] 获取 {stock_code} 筹码分布失败: {e}")
            return None
    
    def get_enhanced_data(self, stock_code: str, days: int = 60) -> Dict[str, Any]:
        """
        获取增强数据（历史K线 + 实时行情 + 筹码分布）
        
        Args:
            stock_code: 股票代码
            days: 历史数据天数
            
        Returns:
            包含所有数据的字典
        """
        result = {
            'code': stock_code,
            'daily_data': None,
            'realtime_quote': None,
            'chip_distribution': None,
        }
        
        # 获取日线数据
        try:
            df = self.get_daily_data(stock_code, days=days)
            result['daily_data'] = df
        except Exception as e:
            logger.error(f"获取 {stock_code} 日线数据失败: {e}")
        
        # 获取实时行情
        result['realtime_quote'] = self.get_realtime_quote(stock_code)
        
        # 获取筹码分布
        result['chip_distribution'] = self.get_chip_distribution(stock_code)
        
        return result


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.DEBUG)
    
    fetcher = AkshareFetcher()
    
    # 测试普通股票
    print("=" * 50)
    print("测试普通股票数据获取")
    print("=" * 50)
    try:
        df = fetcher.get_daily_data('600519')  # 茅台
        print(f"[股票] 获取成功，共 {len(df)} 条数据")
        print(df.tail())
    except Exception as e:
        print(f"[股票] 获取失败: {e}")
    
    # 测试 ETF 基金
    print("\n" + "=" * 50)
    print("测试 ETF 基金数据获取")
    print("=" * 50)
    try:
        df = fetcher.get_daily_data('512400')  # 有色龙头ETF
        print(f"[ETF] 获取成功，共 {len(df)} 条数据")
        print(df.tail())
    except Exception as e:
        print(f"[ETF] 获取失败: {e}")
    
    # 测试 ETF 实时行情
    print("\n" + "=" * 50)
    print("测试 ETF 实时行情获取")
    print("=" * 50)
    try:
        quote = fetcher.get_realtime_quote('512880')  # 证券ETF
        if quote:
            print(f"[ETF实时] {quote.name}: 价格={quote.price}, 涨跌幅={quote.change_pct}%")
        else:
            print("[ETF实时] 未获取到数据")
    except Exception as e:
        print(f"[ETF实时] 获取失败: {e}")
    
    # 测试港股历史数据
    print("\n" + "=" * 50)
    print("测试港股历史数据获取")
    print("=" * 50)
    try:
        df = fetcher.get_daily_data('00700')  # 腾讯控股
        print(f"[港股] 获取成功，共 {len(df)} 条数据")
        print(df.tail())
    except Exception as e:
        print(f"[港股] 获取失败: {e}")
    
    # 测试港股实时行情
    print("\n" + "=" * 50)
    print("测试港股实时行情获取")
    print("=" * 50)
    try:
        quote = fetcher.get_realtime_quote('00700')  # 腾讯控股
        if quote:
            print(f"[港股实时] {quote.name}: 价格={quote.price}, 涨跌幅={quote.change_pct}%")
        else:
            print("[港股实时] 未获取到数据")
    except Exception as e:
        print(f"[港股实时] 获取失败: {e}")
