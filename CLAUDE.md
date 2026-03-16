# CLAUDE.md

## Project Overview

股票舆情与热度研究系统。四个独立包：热度采集、新闻采集、股吧情绪、LLM分析。结果存入 MySQL，用于选股信号和相关性研究。

## Commands

```bash
# 热度采集（人气排名，全市场 ~5500 只）
python -m heat.main

# 热门关键词（Top100，必须先跑人气排名）
python -m heat.main --keyword

# 热度回溯（366天历史趋势，仅首次）
python -m heat.main --init

# 个股新闻采集
python -m news.main --stocks 600519,000858
python -m news.main --all-stocks

# 股吧情绪分析
python -m guba.main --stock 600519,000858
python -m guba.main --market

# LLM 分析
python -m analysis.main --global              # 国际形势
python -m analysis.main --domestic            # 国内形势
python -m analysis.main --stock 601789        # 个股基本面
python -m analysis.main --all                 # 全部
```

### 执行顺序约束

- **heat 日常必须串行**：`heat.main` → `heat.main --keyword`，关键词依赖当日人气排名 Top100
- `--init` 仅首次运行，独立执行

## Architecture

```
config.py         共享配置 + load_stocks
database.py       共享 Base + Database + batch_upsert/insert_ignore

heat/             热度包
  fetcher.py      能力层：popularity_page + akshare 接口
  models.py       PopularityRank + EmHotRankDetail + EmHotKeyword
  main.py         调度层：1s间隔循环调用，入库

news/             新闻包
  fetcher.py      能力层：今日头条搜索（主）+ 搜狗（备），curl_cffi 绕反爬
  models.py       Article（url_hash 唯一去重）
  main.py         调度层：1s间隔分批，入库

guba/             股吧情绪
  fetcher.py      能力层：股吧帖子抓取 + LLM 情绪分类
  models.py       GubaSentiment + GubaPostDetail
  main.py         调度层：单股/市场采样（4区间40只，并发LLM）

analysis/         LLM 分析
  analyzer.py     能力层：头条搜索 + LLM 分析（国际/国内/个股）
  models.py       NewsAnalysis（三种类型：global/domestic/stock）
  main.py         调度层
```

## Database Tables

| 表 | 包 | 说明 |
|----|-----|------|
| `popularity_rank` | heat | 人气排名每日快照，全市场 ~5500 只 |
| `em_hot_rank_detail` | heat | 个股历史趋势+粉丝 366 天 |
| `em_hot_keyword` | heat | 个股热门关键词，Top100 概念关联 |
| `articles` | news | 个股新闻文章，7 天滚动 |
| `guba_sentiment` | guba | 股吧情绪得分（每日每股一条，百分制） |
| `guba_post_detail` | guba | 股吧帖子明细 |
| `news_analysis` | analysis | LLM 分析结果（国际/国内/个股，百分制） |

## Key Design Decisions

- **能力层 vs 调度层**: fetcher.py 是纯接口调用，main.py 是调度编排（1s 间隔限流）
- **包独立**: 每个包自带 models.py + main.py，可独立运行
- **不使用事务**: 无 rollback，每次写入独立提交
- **写入方法**: `database.batch_upsert()` / `batch_insert_ignore()`
- **新闻搜索**: 今日头条优先，搜狗 fallback，curl_cffi + chrome120 指纹绕反爬
- **评分统一百分制**: 50 分中性，0-100 范围
- **搜索关键词**: 公司名 + 项目/利润/前景，单词搜索更精准
- **股吧并发**: Semaphore(10) 控制 LLM 并发，主线程顺序抓帖子
- **stocks.txt**: 解析代码、公司名、行业，生成 StockInfo
- HTTP 客户端: httpx（heat）/ curl_cffi（news/guba/analysis）
- AkShare 用 requests，heat/main.py 临时禁用系统代理
