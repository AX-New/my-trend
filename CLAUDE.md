# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

股票舆情与热度研究系统。三条数据线：人气排名（热度核心）、新闻采集（基本面补充）、股吧情绪采样（待实现）。结果存入 MySQL，用于与行情数据做相关性分析。

## 当前状态

> **详细计划见 [task/upgrade-plan.md](task/upgrade-plan.md)**

已完成: 人气排名采集(东方财富选股API)、固定关键词新闻搜索、7天滚动清理、StockInfo解析、pipeline整合、AkShare热度数据(10接口)、热度每日监控(monitor.py)
待完成: 股吧情绪采样(top1-10/90-100/490-500共30只，LLM分析)

## Commands

```bash
# 安装依赖
pip install -r requirements.txt

# 单次执行（抓取→存储，跳过分析）
python main.py --once --no-analyze

# 启动定时调度（默认每15分钟）
python main.py

# 指定股票
python main.py --once --stocks 600519,000858

# 只采集指定板块
python main.py --once --category news    # news/finance/forum/stock

# 单次处理全部股票（不分批）
python main.py --once --all-stocks --no-analyze

# 使用自定义配置文件
python main.py -c path/to/config.yaml
```

## Architecture

数据流：
```
1. cleanup_old_articles() — 清理 >7 天文章
2. fetch_source() — 常规 RSS/API 数据源
3. fetch_popularity_rank() — 东方财富人气排名（全市场 ~5500 只）
4. run_akshare_pipeline() — AkShare 热度数据（10 接口）
   4a. 批量: 东方财富人气榜/飙升榜 Top100 + 雪球关注/讨论/交易排行
   4b. 单股: 历史趋势、实时排名、热门关键词、最新排名、相关股票
5. fetch_stock_news_batch() — 个股新闻（代码+公司名搜索，轮转分批）
6. 入库 articles（URL 去重）
```

- **main.py** — 入口，pipeline 编排，定时调度，轮转分批，人气排名/AkShare热度/文章入库
- **akshare_fetcher.py** — AkShare 热度数据采集：10 个 A 股热度接口（东方财富人气榜/飙升榜/单股详情 + 雪球三榜）
- **fetcher.py** — 数据抓取：RSS、Google News RSS、东方财富搜索(JSONP)、新浪 JSON API、人气排名(选股API分页)、固定关键词生成
- **analyzer.py** — LLM 调用：单篇分析、批量分析提示词（备用）
- **models.py** — SQLAlchemy ORM：Article、StockDaily、PopularityRank + AkShare 8 张表（EmHotRank/Up/Detail/Realtime/Keyword/Latest/Relate + XqHotRank）
- **database.py** — SQLAlchemy engine/session 封装，自动建表
- **config.py** — dataclass 配置：StockInfo、PopularityConfig、AkshareConfig、AppConfig 等
- **config.yaml** — 数据库、LLM、数据源、人气排名、AkShare、调度、网络配置

## Key Design Decisions

- **热度 vs 基本面**: 人气排名（PopularityRank）是核心热度指标，新闻采集只做基本面补充，不用于热度分析
- **固定关键词**: 搜索词为 股票代码 + 公司名，不使用 LLM query expansion，零成本
- **去重**: 同日标题相似度 >0.85 合并，dup_count 记录重复数；URL 精确去重兜底
- **存储**: articles 7 天滚动清理；popularity_rank 按日期累积
- **人气排名**: 东方财富选股 API，分两段 filter（<=1000 和 >1000），~110 页分页，覆盖全市场 ~5500 只
- **stocks.txt**: 解析代码、公司名（# 注释）、行业（# ── XX ── 标题），生成 StockInfo
- **AkShare 热度**: 10 个接口分批量（无参数，快照式入库）和单股（需代码，去重式入库），rank_top 模式默认取人气榜 Top20 做单股详情
- HTTP 客户端统一 httpx，Google News 需代理，国内接口不需要
- AkShare 用 requests，pipeline 中临时禁用系统代理（trust_env=False）
