# CLAUDE.md

## Project Overview

股票舆情与热度研究系统。四个独立包：热度采集、新闻采集、股吧情绪、分析。结果存入 MySQL，用于与行情数据做相关性分析。

## 当前状态

> **详细计划见 [task/upgrade-plan.md](task/upgrade-plan.md)**

已完成: 人气排名采集、AkShare热度接口(10个代码保留,执行层2个核心)、新闻采集、无事务写入改造、包拆分
待完成: 股吧情绪采样(guba包)、LLM批量分析(analysis包)

## Commands

```bash
# 热度采集（人气排名，全市场 ~5500 只）
python -m heat.main

# 热度回溯（366天历史趋势，仅首次）
python -m heat.main --init

# 新闻采集（单次）
python -m news.main --once

# 新闻采集（定时调度）
python -m news.main

# 新闻采集（指定股票/板块）
python -m news.main --once --stocks 600519,000858
python -m news.main --once --category news

# 分析（待实现）
python -m analysis.main
```

## Architecture

```
database.py       共享 Base + Database + batch_upsert/insert_ignore
config.py         共享配置 + load_stocks

heat/             热度包（独立运行）
  fetcher.py      能力层：popularity_page + akshare 10接口，无限流
  models.py       PopularityRank + AkShare 8张表
  main.py         调度层：1s间隔循环调用，入库

news/             新闻包（独立运行）
  fetcher.py      能力层：RSS/GoogleNews/东方财富/新浪，单次调用
  models.py       Article（url_hash唯一去重）
  main.py         调度层：1s间隔分批，入库

guba/             股吧情绪（待实现）

analysis/         分析包
  analyzer.py     能力层：LLM 单篇/批量分析
  models.py       StockDaily
  main.py         调度层（待实现）
```

## Key Design Decisions

- **能力层 vs 调度层**: fetcher.py 是纯接口调用（无限流），main.py 是调度编排（1s 间隔限流）
- **包独立**: 每个包自带 models.py + main.py，可独立运行，不需要总入口
- **不使用事务**: 无 rollback，每次写入独立提交，分批提交防崩溃丢数据
- **支持重复写入**: MySQL `INSERT ... ON DUPLICATE KEY UPDATE`（upsert）和 `INSERT IGNORE`，通过唯一约束保证幂等
- **写入方法**: `database.batch_upsert()` / `batch_insert_ignore()`，所有写入统一走这两个方法
- **Article 去重**: url_hash（SHA256）唯一约束
- **热度核心**: PopularityRank（全市场 ~5500 只）+ EmHotRankDetail（366天趋势）
- **AkShare 精简**: 10 个接口代码保留备用，执行层只用 2 个核心接口
- **固定关键词**: 搜索词为 股票代码 + 公司名，不使用 LLM query expansion
- **stocks.txt**: 解析代码、公司名、行业，生成 StockInfo
- HTTP 客户端统一 httpx，Google News 需代理，国内接口不需要
- AkShare 用 requests，heat/main.py 临时禁用系统代理（trust_env=False）
