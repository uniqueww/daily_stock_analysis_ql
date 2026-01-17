#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cron: 0 18 * * 1-5
new Env('A股智能分析')
"""

import os
import sys
import time
import logging
from datetime import datetime, date
from pathlib import Path
from typing import List, Optional, Dict, Any

SCRIPT_DIR = Path(__file__).parent.absolute()
os.chdir(SCRIPT_DIR)
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

for lib in ['urllib3', 'google', 'httpx', 'httpcore']:
    logging.getLogger(lib).setLevel(logging.WARNING)


def send_notify(title: str, content: str) -> bool:
    """
    发送通知消息
    
    使用本地 notify.py 模块进行消息推送，支持多种推送渠道：
    - 企业微信、钉钉、飞书
    - Telegram、Bark、PushPlus
    - Server酱、邮件等
    
    Args:
        title: 通知标题
        content: 通知内容
        
    Returns:
        是否发送成功
    """
    try:
        from notify import send
        send(title, content)
        logger.info(f"通知发送成功: {title}")
        return True
    except ImportError:
        logger.warning("未找到 notify.py 模块，跳过推送")
        return False
    except Exception as e:
        logger.warning(f"通知发送失败: {e}")
        return False


def get_env_list(key: str) -> List[str]:
    value = os.environ.get(key, '')
    return [v.strip() for v in value.split(',') if v.strip()] if value else []


def build_context(code: str, df, realtime_quote, chip_data) -> Optional[Dict[str, Any]]:
    if df is None or df.empty:
        return None
    df = df.sort_values('date', ascending=False).reset_index(drop=True)
    today_row = df.iloc[0] if len(df) > 0 else None
    if today_row is None:
        return None
    
    context = {
        'code': code,
        'date': str(today_row.get('date', date.today())),
        'today': {
            'open': today_row.get('open'),
            'high': today_row.get('high'),
            'low': today_row.get('low'),
            'close': today_row.get('close'),
            'volume': today_row.get('volume'),
            'amount': today_row.get('amount'),
            'pct_chg': today_row.get('pct_chg'),
            'ma5': today_row.get('ma5'),
            'ma10': today_row.get('ma10'),
            'ma20': today_row.get('ma20'),
        }
    }
    
    if len(df) > 1:
        yesterday = df.iloc[1]
        context['yesterday'] = {'close': yesterday.get('close'), 'volume': yesterday.get('volume')}
        if yesterday.get('volume') and yesterday['volume'] > 0:
            context['volume_change_ratio'] = round(today_row.get('volume', 0) / yesterday['volume'], 2)
    
    close = today_row.get('close', 0)
    ma5 = today_row.get('ma5', 0)
    ma10 = today_row.get('ma10', 0)
    ma20 = today_row.get('ma20', 0)
    if close and ma5 and ma10 and ma20:
        if close > ma5 > ma10 > ma20 > 0:
            context['ma_status'] = "多头排列"
        elif close < ma5 < ma10 < ma20:
            context['ma_status'] = "空头排列"
        else:
            context['ma_status'] = "震荡整理"
    
    if realtime_quote:
        context['stock_name'] = realtime_quote.name or f'股票{code}'
        context['realtime'] = {
            'name': realtime_quote.name,
            'price': realtime_quote.price,
            'volume_ratio': realtime_quote.volume_ratio,
            'turnover_rate': realtime_quote.turnover_rate,
            'pe_ratio': getattr(realtime_quote, 'pe_ratio', None),
            'pb_ratio': getattr(realtime_quote, 'pb_ratio', None),
        }
    
    if chip_data:
        context['chip'] = {
            'profit_ratio': chip_data.profit_ratio,
            'avg_cost': chip_data.avg_cost,
            'concentration_90': chip_data.concentration_90,
            'concentration_70': getattr(chip_data, 'concentration_70', None),
        }
    
    return context


def generate_report(results: List, report_date: str) -> str:
    buy_count = sum(1 for r in results if r.operation_advice in ['买入', '加仓', '强烈买入'])
    sell_count = sum(1 for r in results if r.operation_advice in ['卖出', '减仓', '强烈卖出'])
    hold_count = len(results) - buy_count - sell_count
    
    lines = [
        f"# 🎯 {report_date} 决策仪表盘",
        "",
        f"> 共分析 **{len(results)}** 只 | 🟢买入:{buy_count} 🟡观望:{hold_count} 🔴卖出:{sell_count}",
        "",
        "---",
        "",
    ]
    
    sorted_results = sorted(results, key=lambda x: x.sentiment_score, reverse=True)
    
    for r in sorted_results:
        emoji = r.get_emoji()
        name = r.name if r.name and not r.name.startswith('股票') else f'股票{r.code}'
        
        lines.extend([
            f"## {emoji} {name} ({r.code})",
            "",
            f"**{r.operation_advice}** | 评分 {r.sentiment_score} | {r.trend_prediction}",
            "",
        ])
        
        if r.dashboard:
            core = r.dashboard.get('core_conclusion', {})
            if core.get('one_sentence'):
                lines.append(f"> {core['one_sentence']}")
                lines.append("")
            
            battle = r.dashboard.get('battle_plan', {})
            sniper = battle.get('sniper_points', {})
            if sniper:
                lines.append("**狙击点位**")
                if sniper.get('ideal_buy'):
                    lines.append(f"- 🎯 买入: {sniper['ideal_buy']}")
                if sniper.get('stop_loss'):
                    lines.append(f"- 🛑 止损: {sniper['stop_loss']}")
                if sniper.get('take_profit'):
                    lines.append(f"- 🎊 目标: {sniper['take_profit']}")
                lines.append("")
            
            intel = r.dashboard.get('intelligence', {})
            risks = intel.get('risk_alerts', [])
            if risks:
                lines.append("**⚠️ 风险**")
                for risk in risks[:2]:
                    lines.append(f"- {risk[:60]}")
                lines.append("")
        
        if r.buy_reason:
            lines.append(f"**操作理由**: {r.buy_reason[:100]}")
            lines.append("")
        
        lines.append("---")
        lines.append("")
    
    lines.append(f"*生成时间: {datetime.now().strftime('%H:%M:%S')}*")
    return "\n".join(lines)


def run_stock_analysis(stock_list: List[str]) -> List:
    from config import Config, get_config
    from data_provider import DataFetcherManager
    from data_provider.akshare_fetcher import AkshareFetcher
    from analyzer import GeminiAnalyzer
    from search_service import SearchService
    from stock_analyzer import StockTrendAnalyzer
    
    Config.reset_instance()
    
    fetcher_manager = DataFetcherManager()
    akshare_fetcher = AkshareFetcher()
    trend_analyzer = StockTrendAnalyzer()
    analyzer = GeminiAnalyzer()
    search_service = SearchService(
        tavily_keys=get_env_list('TAVILY_API_KEYS'),
        serpapi_keys=get_env_list('SERPAPI_API_KEYS'),
    )
    
    results = []
    
    for i, code in enumerate(stock_list, 1):
        logger.info(f"\n[{i}/{len(stock_list)}] 处理: {code}")
        
        try:
            df, source = fetcher_manager.get_daily_data(code, days=30)
            if df is None or df.empty:
                logger.warning(f"[{code}] 数据为空")
                continue
            logger.info(f"[{code}] 数据获取成功 ({source})")
            
            realtime_quote = None
            stock_name = f'股票{code}'
            try:
                realtime_quote = akshare_fetcher.get_realtime_quote(code)
                if realtime_quote and realtime_quote.name:
                    stock_name = realtime_quote.name
                    logger.info(f"[{code}] {stock_name} 价格: {realtime_quote.price}")
            except Exception as e:
                logger.warning(f"[{code}] 实时行情失败: {e}")
            
            chip_data = None
            try:
                chip_data = akshare_fetcher.get_chip_distribution(code)
            except:
                pass
            
            trend_result = None
            try:
                trend_result = trend_analyzer.analyze(df, code)
            except:
                pass
            
            news_context = None
            if search_service.is_available:
                try:
                    intel = search_service.search_comprehensive_intel(code, stock_name, max_searches=2)
                    if intel:
                        news_context = search_service.format_intel_report(intel, stock_name)
                except Exception as e:
                    logger.warning(f"[{code}] 新闻搜索失败: {e}")
            
            context = build_context(code, df, realtime_quote, chip_data)
            if context:
                if trend_result:
                    context['trend_analysis'] = {
                        'trend_status': trend_result.trend_status.value,
                        'ma_alignment': trend_result.ma_alignment,
                        'bias_ma5': trend_result.bias_ma5,
                        'bias_ma10': trend_result.bias_ma10,
                        'buy_signal': trend_result.buy_signal.value,
                        'signal_score': trend_result.signal_score,
                        'signal_reasons': trend_result.signal_reasons,
                        'risk_factors': trend_result.risk_factors,
                    }
                
                logger.info(f"[{code}] AI分析中...")
                result = analyzer.analyze(context, news_context=news_context)
                if result:
                    results.append(result)
                    logger.info(f"[{code}] ✅ {result.operation_advice} 评分{result.sentiment_score}")
        
        except Exception as e:
            logger.error(f"[{code}] 处理失败: {e}")
    
    return results


def run_market_review() -> Optional[str]:
    from config import get_config
    from market_analyzer import MarketAnalyzer
    from search_service import SearchService
    from analyzer import GeminiAnalyzer
    
    logger.info("开始大盘复盘...")
    
    search_service = SearchService(
        tavily_keys=get_env_list('TAVILY_API_KEYS'),
        serpapi_keys=get_env_list('SERPAPI_API_KEYS'),
    )
    analyzer = GeminiAnalyzer()
    
    market = MarketAnalyzer(search_service=search_service, analyzer=analyzer)
    report = market.run_daily_review()
    
    if report:
        logger.info("大盘复盘完成")
    return report


def save_report(content: str, filename: str) -> str:
    report_dir = SCRIPT_DIR / "reports"
    report_dir.mkdir(exist_ok=True)
    filepath = report_dir / filename
    filepath.write_text(content, encoding='utf-8')
    logger.info(f"报告已保存: {filepath}")
    return str(filepath)


def main():
    print("=" * 50)
    print("📈 A股智能分析系统 - 青龙版")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    
    stock_list = get_env_list('STOCK_LIST')
    if not stock_list:
        logger.error("❌ 未配置 STOCK_LIST")
        sys.exit(1)
    logger.info(f"✅ 自选股: {', '.join(stock_list)}")
    
    openai_key = os.environ.get('OPENAI_API_KEY', '')
    gemini_key = os.environ.get('GEMINI_API_KEY', '')
    if not openai_key and not gemini_key:
        logger.error("❌ 未配置 AI API Key")
        sys.exit(1)
    
    if openai_key:
        logger.info(f"✅ API: {os.environ.get('OPENAI_BASE_URL', 'OpenAI')} ({os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')})")
    else:
        logger.info("✅ API: Gemini")
    
    start_time = time.time()
    report_date = datetime.now().strftime('%Y-%m-%d')
    full_report = ""
    summary = ""
    
    market_only = os.environ.get('MARKET_REVIEW_ONLY', '').lower() in ('true', '1', 'yes')
    
    if not market_only:
        results = run_stock_analysis(stock_list)
        
        if results:
            full_report = generate_report(results, report_date)
            save_report(full_report, f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
            
            buy_count = sum(1 for r in results if r.operation_advice in ['买入', '加仓', '强烈买入'])
            sell_count = sum(1 for r in results if r.operation_advice in ['卖出', '减仓', '强烈卖出'])
            hold_count = len(results) - buy_count - sell_count
            
            summary_lines = [
                f"📊 {report_date} 决策仪表盘",
                f"共{len(results)}只 | 🟢买入:{buy_count} 🟡观望:{hold_count} 🔴卖出:{sell_count}",
                "",
            ]
            for r in sorted(results, key=lambda x: x.sentiment_score, reverse=True):
                summary_lines.append(f"{r.get_emoji()} {r.name}({r.code}): {r.operation_advice} {r.sentiment_score}分")
            summary = "\n".join(summary_lines)
    
    market_enabled = os.environ.get('MARKET_REVIEW_ENABLED', 'true').lower() in ('true', '1', 'yes')
    if market_enabled or market_only:
        market_report = run_market_review()
        if market_report:
            save_report(f"# 📊 大盘复盘\n\n{market_report}", f"market_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
            if market_only:
                summary = f"📊 {report_date} 大盘复盘\n\n{market_report[:500]}..."
    
    elapsed = time.time() - start_time
    logger.info(f"\n✅ 完成! 耗时: {elapsed:.1f}秒")
    
    if summary:
        send_notify(f"📈 A股分析 {datetime.now().strftime('%m-%d %H:%M')}", summary)
        print("\n" + "=" * 50)
        print(summary)
    
    print("\n✅ 脚本执行完成")


if __name__ == "__main__":
    main()
