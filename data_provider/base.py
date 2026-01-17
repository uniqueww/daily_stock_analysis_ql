# -*- coding: utf-8 -*-
import logging
import random
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

STANDARD_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']


class DataFetchError(Exception):
    pass


class RateLimitError(DataFetchError):
    pass


class DataSourceUnavailableError(DataFetchError):
    pass


class BaseFetcher(ABC):
    name: str = "BaseFetcher"
    priority: int = 99
    
    @abstractmethod
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        pass
    
    @abstractmethod
    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        pass
    
    def get_daily_data(
        self, 
        stock_code: str, 
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30
    ) -> pd.DataFrame:
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')
        
        if start_date is None:
            from datetime import timedelta
            start_dt = datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=days * 2)
            start_date = start_dt.strftime('%Y-%m-%d')
        
        logger.info(f"[{self.name}] 获取 {stock_code}: {start_date} ~ {end_date}")
        
        try:
            raw_df = self._fetch_raw_data(stock_code, start_date, end_date)
            if raw_df is None or raw_df.empty:
                raise DataFetchError(f"[{self.name}] 未获取到 {stock_code} 数据")
            
            df = self._normalize_data(raw_df, stock_code)
            df = self._clean_data(df)
            df = self._calculate_indicators(df)
            
            logger.info(f"[{self.name}] {stock_code} 获取成功，共 {len(df)} 条")
            return df
            
        except Exception as e:
            logger.error(f"[{self.name}] 获取 {stock_code} 失败: {str(e)}")
            raise DataFetchError(f"[{self.name}] {stock_code}: {str(e)}") from e
    
    def _clean_data(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
        
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        df = df.dropna(subset=['close', 'volume'])
        df = df.sort_values('date', ascending=True).reset_index(drop=True)
        return df
    
    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['ma5'] = df['close'].rolling(window=5, min_periods=1).mean()
        df['ma10'] = df['close'].rolling(window=10, min_periods=1).mean()
        df['ma20'] = df['close'].rolling(window=20, min_periods=1).mean()
        
        avg_volume_5 = df['volume'].rolling(window=5, min_periods=1).mean()
        df['volume_ratio'] = df['volume'] / avg_volume_5.shift(1)
        df['volume_ratio'] = df['volume_ratio'].fillna(1.0)
        
        for col in ['ma5', 'ma10', 'ma20', 'volume_ratio']:
            if col in df.columns:
                df[col] = df[col].round(2)
        return df
    
    @staticmethod
    def random_sleep(min_seconds: float = 1.0, max_seconds: float = 3.0) -> None:
        time.sleep(random.uniform(min_seconds, max_seconds))


class DataFetcherManager:
    def __init__(self, fetchers: Optional[List[BaseFetcher]] = None):
        self._fetchers: List[BaseFetcher] = []
        if fetchers:
            self._fetchers = sorted(fetchers, key=lambda f: f.priority)
        else:
            self._init_default_fetchers()
    
    def _init_default_fetchers(self) -> None:
        from .akshare_fetcher import AkshareFetcher
        self._fetchers = [AkshareFetcher()]
        logger.info(f"已初始化数据源: {', '.join([f.name for f in self._fetchers])}")
    
    def get_daily_data(
        self, 
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30
    ) -> Tuple[pd.DataFrame, str]:
        errors = []
        
        for fetcher in self._fetchers:
            try:
                logger.info(f"尝试 [{fetcher.name}] 获取 {stock_code}...")
                df = fetcher.get_daily_data(stock_code, start_date, end_date, days)
                if df is not None and not df.empty:
                    return df, fetcher.name
            except Exception as e:
                errors.append(f"[{fetcher.name}]: {str(e)}")
                continue
        
        raise DataFetchError(f"所有数据源获取 {stock_code} 失败:\n" + "\n".join(errors))
