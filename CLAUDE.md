# CLAUDE.md

## Project Overview

股票舆情与热度研究系统。四个独立包（heat/news/guba/analysis）各自采集、分析、入库，结果存入 MySQL，用于选股信号和相关性研究。

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
python -m analysis.main --all-la              # 选股池
python -m analysis.main --retry               # 重跑失败

# 行业分析（申万一级31个行业）
python -m industry.main                       # 全部行业
python -m industry.main --name 煤炭            # 单个行业

# 板块分析（东财板块 BK0+BK1 ~1000个）
python -m sector.main                         # 全部板块
python -m sector.main --name 公用事业           # 单个板块
```

### 执行顺序约束

- **heat 日常必须串行**：`heat.main` → `heat.main --keyword`，关键词依赖当日人气排名 Top100
- `--init` 仅首次运行，独立执行

## Architecture

### 分层设计

```
┌─────────────────────────────────────────────────────────┐
│                     调度层 (main.py)                      │
│  间隔控制 · 并发管理 · 游标断点 · 入库编排 · 重试策略       │
├─────────────────────────────────────────────────────────┤
│                 能力层 (fetcher.py / analyzer.py)          │
│  纯 HTTP/API 调用 · 数据解析 · LLM Prompt · 不关心调度     │
├─────────────────────────────────────────────────────────┤
│                  数据层 (database.py)                      │
│  SQLAlchemy · batch_upsert · batch_insert_ignore · 幂等   │
├─────────────────────────────────────────────────────────┤
│                  配置层 (config.py)                        │
│  config.yaml 加载 · AppConfig dataclass · stocks.txt 解析  │
├─────────────────────────────────────────────────────────┤
│                  网络层 (clash_proxy.py)                   │
│  Clash API 节点轮换 · 测速筛选 · 最优选择 · 自动恢复       │
└─────────────────────────────────────────────────────────┘
```

### 包结构

```
config.py         共享配置 + load_stocks
database.py       共享 Base + Database + batch_upsert/insert_ignore
clash_proxy.py    Clash 代理动态切换（节点轮换/测速/最优选择）

heat/             热度包
  fetcher.py      能力层：popularity_page + akshare 接口
  models.py       PopularityRank + EmHotRankDetail + EmHotKeyword
  main.py         调度层：1s间隔循环调用，入库

news/             新闻包
  fetcher.py      能力层：今日头条搜索，curl_cffi 绕反爬
  models.py       Article（url_hash 唯一去重）
  main.py         调度层：重试退避 + 20-30s 间隔限流

guba/             股吧情绪
  fetcher.py      能力层：股吧帖子抓取 + LLM 情绪分类 + 加权评分
  models.py       GubaSentiment + GubaPostDetail
  main.py         调度层：单股/市场采样/全市场（并发LLM，断点续跑）

analysis/         LLM 分析
  analyzer.py     能力层：头条搜索 + LLM 分析（国际/国内/个股）
  models.py       NewsAnalysis + AnalysisFailure + AnalysisRun
  main.py         调度层：游标断点续跑 + 失败重试 + 并发LLM

industry/         行业分析
  analyzer.py     能力层：头条搜索 + LLM 行业多维评分
  models.py       IndustryAnalysis
  main.py         调度层：31个申万行业逐个分析，并发LLM

sector/           板块分析
  analyzer.py     能力层：头条搜索 + LLM 板块多维评分
  models.py       SectorAnalysis
  main.py         调度层：~1000个东财板块逐个分析，并发LLM
```

### 并发模型

- **串行搜索 + 并发 LLM**：主线程按顺序发起 HTTP 请求（限流间隔），搜到数据后提交线程池做 LLM 分析
- **Semaphore 控制上限**：analysis MAX_LLM_WORKERS=3，guba MAX_LLM_WORKERS=10
- **滑动窗口收割**：主线程搜索新股时同步回收已完成的 LLM future，避免堆积

### 断点续跑机制

- `AnalysisRun` 表：run_id + 游标 + 失败数 + 来源标识
- 中断后重跑自动从游标位置继续，`--no-resume` 强制新建
- `AnalysisFailure` 表记录失败阶段和错误，`--retry` 重跑未解决的

### 代理切换

- `clash_proxy.py`：通过 Clash REST API 管理代理节点
- 有序节点列表 + 游标轮换，测速不通过则跳过
- analysis 搜索失败时自动轮换 IP + 重建 session
- `restore_auto()` 恢复自动选择

## Database Tables

| 表 | 包 | 说明 |
|----|-----|------|
| `popularity_rank` | heat | 人气排名每日快照，全市场 ~5500 只 |
| `em_hot_rank_detail` | heat | 个股历史趋势+粉丝 366 天 |
| `em_hot_keyword` | heat | 个股热门关键词，Top100 概念关联 |
| `articles` | news | 个股新闻文章，7 天滚动 |
| `guba_sentiment` | guba | 股吧情绪得分（每日每股一条，百分制） |
| `guba_post_detail` | guba | 股吧帖子明细 |
| `news_analysis` | analysis | LLM 分析结果（国际/国内/个股，百分制多维评分） |
| `analysis_failure` | analysis | 分析失败记录（断点续跑 + 失败重试） |
| `industry_analysis` | industry | 申万行业逐行业 LLM 分析（每行业每日一条） |
| `sector_analysis` | sector | 东财板块逐板块 LLM 分析（每板块每日一条） |

## Key Design Decisions

- **能力层 vs 调度层**: fetcher.py 是纯接口调用（无限流），main.py 是调度编排（间隔控制 + 并发管理）
- **包独立**: 每个包自带 models.py + main.py，可独立运行
- **不使用事务**: 无 rollback，每次写入独立提交
- **写入方法**: `database.batch_upsert()` / `batch_insert_ignore()`，分批提交（500条/批）
- **新闻搜索**: 今日头条，curl_cffi + chrome120 指纹绕反爬
- **评分统一百分制**: 50 分中性，0-100 范围
- **搜索关键词**: 公司名 + 项目/利润/前景，单词搜索更精准
- **股吧并发**: Semaphore(10) 控制 LLM 并发，主线程顺序抓帖子
- **stocks.txt**: 解析代码、公司名、行业，生成 StockInfo
- HTTP 客户端: httpx（heat）/ curl_cffi（news/guba/analysis）
- AkShare 用 requests，heat/main.py 临时禁用系统代理
- **代理切换**: clash_proxy.py 通过 Clash REST API 动态切换代理节点，搜索失败自动轮换
