# -*- coding: utf-8 -*-
import os
from typing import List, Optional
from dataclasses import dataclass, field


@dataclass
class Config:
    stock_list: List[str] = field(default_factory=list)
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-3-flash-preview"
    gemini_model_fallback: str = "gemini-2.5-flash"
    gemini_request_delay: float = 2.0
    gemini_max_retries: int = 5
    gemini_retry_delay: float = 5.0
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    tavily_api_keys: List[str] = field(default_factory=list)
    serpapi_keys: List[str] = field(default_factory=list)
    max_workers: int = 3
    
    _instance: Optional['Config'] = None
    
    @classmethod
    def get_instance(cls) -> 'Config':
        if cls._instance is None:
            cls._instance = cls._load_from_env()
        return cls._instance
    
    @classmethod
    def _load_from_env(cls) -> 'Config':
        stock_list_str = os.environ.get('STOCK_LIST', '')
        stock_list = [c.strip() for c in stock_list_str.split(',') if c.strip()]
        
        tavily_str = os.environ.get('TAVILY_API_KEYS', '')
        tavily_keys = [k.strip() for k in tavily_str.split(',') if k.strip()]
        
        serpapi_str = os.environ.get('SERPAPI_API_KEYS', '')
        serpapi_keys = [k.strip() for k in serpapi_str.split(',') if k.strip()]
        
        return cls(
            stock_list=stock_list,
            gemini_api_key=os.environ.get('GEMINI_API_KEY'),
            gemini_model=os.environ.get('GEMINI_MODEL', 'gemini-3-flash-preview'),
            gemini_model_fallback=os.environ.get('GEMINI_MODEL_FALLBACK', 'gemini-2.5-flash'),
            gemini_request_delay=float(os.environ.get('GEMINI_REQUEST_DELAY', '2.0')),
            gemini_max_retries=int(os.environ.get('GEMINI_MAX_RETRIES', '5')),
            gemini_retry_delay=float(os.environ.get('GEMINI_RETRY_DELAY', '5.0')),
            openai_api_key=os.environ.get('OPENAI_API_KEY'),
            openai_base_url=os.environ.get('OPENAI_BASE_URL'),
            openai_model=os.environ.get('OPENAI_MODEL', 'gpt-4o-mini'),
            tavily_api_keys=tavily_keys,
            serpapi_keys=serpapi_keys,
            max_workers=int(os.environ.get('MAX_WORKERS', '3')),
        )
    
    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None
    
    def validate(self) -> List[str]:
        warnings = []
        if not self.stock_list:
            warnings.append("未配置 STOCK_LIST")
        if not self.gemini_api_key and not self.openai_api_key:
            warnings.append("未配置 AI API Key")
        return warnings


def get_config() -> Config:
    return Config.get_instance()
