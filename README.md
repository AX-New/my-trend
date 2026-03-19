# my-trend

**股票舆情与热度研究系统** — 多维度采集 A 股市场情绪数据，结合 LLM 分析，输出结构化评分，用于选股信号和相关性研究。

## 系统架构

```
                         ┌─────────────────────────────────────┐
                         │           config.yaml               │
                         │  (数据库 / LLM / 网络 / 调度参数)     │
                         └──────────────┬──────────────────────┘
                                        │
              ┌─────────────┬───────────┼───────────┬─────────────┐
              ▼             ▼           ▼           ▼             ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐
        │  heat/   │ │  news/   │ │  guba/   │ │ analysis/│ │clash_proxy│
        │ 热度采集  │ │ 新闻采集  │ │ 股吧情绪  │ │ LLM分析  │ │ 代理切换   │
        └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └───────────┘
             │            │            │            │
             ▼            ▼            ▼            ▼
        ┌─────────────────────────────────────────────────┐
        │            database.py (SQLAlchemy)              │
        │       batch_upsert / batch_insert_ignore         │
        └──────────────────────┬──────────────────────────┘
                               ▼
                     ┌──────────────────┐
                     │   MySQL (my_trend)│
                     │    12 张数据表     │
                     └──────────────────┘
```

## 数据线

| 数据线 | 包 | 数据源 | 范围 | 频率 |
|--------|----|--------|------|------|
| 人气排名 | `heat` | 东方财富选股 API | 全市场 ~5500 只/天 | 盘中9次+收盘1次 |
| 热度趋势 | `heat` | AkShare detail_em | 个股 366 天历史 | 首次回溯 |
| 热门关键词 | `heat` | AkShare keyword_em | Top100 概念关联 | 日频 |
| 热度飙升Top20 | `heat` | popularity_rank 对比 | 排名上升最多的新股 | 盘中9次（累积发现） |
| 分钟K线 | `heat` | AkShare minute | 热度Top股票1分钟线 | 收盘后1次 |
| 个股新闻 | `news` | 今日头条 | 自选股，7 天滚动 | 日频 |
| 股吧情绪 | `guba` | 股吧帖子 + LLM 分类 | 单股/市场采样/全市场 | 日频 |
| 国际形势 | `analysis` | 今日头条 → LLM 分析 | 全球经济/美联储等 | 日频 |
| 国内形势 | `analysis` | 今日头条 → LLM 分析 | A股政策/中国经济等 | 日频 |
| 个股基本面 | `analysis` | 今日头条 → LLM 多维评分 | 自选股/全量/选股池 | 日频 |
| 行业分析 | `industry` | 今日头条 → LLM 多维评分 | 申万一级 31 个行业 | 日频 |
| 板块分析 | `sector` | 今日头条 → LLM 多维评分 | 东财板块 ~1000 个 | 日频/隔日 |

## 快速开始

```bash
pip install -r requirements.txt
cp config.yaml.example config.yaml   # 填写数据库密码和 LLM API Key

# 热度采集（人气排名，全市场）
python -m heat.main

# 新闻采集（指定股票）
python -m news.main --stocks 600519

# 股吧情绪（单股）
python -m guba.main --stock 600519

# 基本面分析（国际+国内+个股）
python -m analysis.main --all
```

## 命令行

### heat — 热度采集+分析

```bash
python -m heat.main                    # 每日人气排名（全市场 ~5500 只）
python -m heat.main --keyword          # Top100 热门关键词（依赖当日排名）
python -m heat.main --init             # 首次回溯（366天历史趋势，~1.5h）
python -m heat.analyze                 # 热度变化分析（对比昨日排名，取Top20新股）
python -m heat.minute                  # 分钟K线（heat_change_top 入选股）
python -m heat.minute --top100         # 分钟K线（人气排名 Top100）
python -m heat.minute --market         # 分钟K线（Top100 + heat_change_top 合集）
```

### news — 新闻采集

```bash
python -m news.main --stocks 600519           # 指定股票
python -m news.main --stocks 600519,000858    # 多只股票
python -m news.main --all-stocks              # 全部自选股
```

### guba — 股吧情绪

```bash
python -m guba.main --stock 600519            # 单股情绪分析
python -m guba.main --stock 600519,000858     # 多股
python -m guba.main --market                  # 市场采样（4区间40只，并发LLM）
python -m guba.main --all-stocks              # 全市场（~5500只，断点续跑）
python -m guba.main --all-la                  # 选股池（la_pick，断点续跑）
```

### analysis — 基本面分析

```bash
python -m analysis.main --global              # 国际形势分析
python -m analysis.main --domestic            # 国内形势分析
python -m analysis.main --stock 600519        # 个股基本面
python -m analysis.main --stock 600519,000858 # 多只个股
python -m analysis.main --all                 # 国际+国内+stocks.txt个股
python -m analysis.main --all-stocks          # 全量个股（stock_basic 全市场）
python -m analysis.main --all-la              # 选股池（la_pick）
python -m analysis.main --retry               # 重跑最近一次失败的股票
python -m analysis.main --all-stocks --no-resume  # 强制全量重跑
```

### industry — 行业分析

```bash
python -m industry.main                  # 全部31个申万一级行业
python -m industry.main --name 煤炭       # 单个行业
```

### sector — 板块分析

```bash
python -m sector.main                    # 全部板块（dc_index BK0+BK1 ~1000个）
python -m sector.main --name 公用事业      # 单个板块
```

### 日常运行顺序

```bash
python -m heat.main              # 1. 人气排名（盘中9次 + 收盘1次）
python -m heat.analyze           # 2. 热度变化Top20（盘中9次，每次找新股）
python -m heat.main --keyword    # 3. 关键词（依赖步骤1的Top100）
python -m heat.minute --market   # 4. 分钟K线（收盘后，Top100 + 热度Top 合集）
python -m news.main --all-stocks # 5. 新闻采集
python -m guba.main --market     # 6. 股吧情绪采样
python -m analysis.main --all    # 7. 基本面分析
python -m industry.main          # 8. 行业分析（凌晨0点，31个）
python -m sector.main            # 9. 板块分析（凌晨1点，~1000个）
```

> heat 日常串行：`heat.main` → `heat.analyze`（+10min）→ `heat.main --keyword`（收盘后）→ `heat.minute`（收盘后）。`--init` 仅首次运行。

## 项目结构

```
my-trend/
├── config.py                # 共享配置：AppConfig + load_stocks
├── config.yaml              # 运行时配置（数据库/LLM/网络/调度参数）
├── database.py              # 共享数据层：Base + Database + batch_upsert/insert_ignore
├── clash_proxy.py           # Clash 代理动态切换（节点轮换/测速/最优选择）
├── stocks.txt               # 自选股票列表（代码 + 公司名 + 行业）
│
├── heat/                    # 热度包
│   ├── fetcher.py           #   能力层：东财人气排名 + AkShare 3个接口
│   ├── models.py            #   PopularityRank/EmHotRankDetail/EmHotKeyword/HeatChangeTop/HeatStockMinute
│   ├── main.py              #   调度层：人气排名 / 关键词 / 历史回溯
│   ├── analyze.py           #   热度变化分析：对比昨日排名，累积发现Top20新股
│   └── minute.py            #   分钟K线采集：收盘后采集热度Top股票1分钟线
│
├── news/                    # 新闻包
│   ├── fetcher.py           #   能力层：今日头条搜索，curl_cffi 绕反爬
│   ├── models.py            #   Article（url_hash 去重）
│   └── main.py              #   调度层：重试退避 + 20-30s 间隔限流
│
├── guba/                    # 股吧情绪包
│   ├── fetcher.py           #   能力层：抓帖子 + LLM 情绪分类 + 加权评分
│   ├── models.py            #   GubaSentiment + GubaPostDetail
│   └── main.py              #   调度层：单股/市场采样/全市场（并发LLM，断点续跑）
│
├── analysis/                # 基本面分析包
│   ├── analyzer.py          #   能力层：头条搜索 → LLM 多维评分（国际/国内/个股）
│   ├── models.py            #   NewsAnalysis + AnalysisFailure + AnalysisRun
│   └── main.py              #   调度层：游标断点续跑 + 失败重试 + 并发LLM
│
├── industry/                # 行业分析包
│   ├── analyzer.py          #   能力层：头条搜索 → LLM 行业多维评分
│   ├── models.py            #   IndustryAnalysis
│   └── main.py              #   调度层：31个申万行业逐个分析，并发LLM
│
├── sector/                  # 板块分析包
│   ├── analyzer.py          #   能力层：头条搜索 → LLM 板块多维评分
│   ├── models.py            #   SectorAnalysis
│   └── main.py              #   调度层：~1000个东财板块逐个分析，并发LLM
│
├── logs/                    # 运行日志（analysis 自动生成）
└── task/                    # 任务文档
```

## 数据表（12 张）

| 表 | 包 | 说明 |
|----|-----|------|
| `popularity_rank` | heat | 人气排名每日快照，全市场 ~5500 只 |
| `em_hot_rank_detail` | heat | 个股历史趋势+粉丝 366 天 |
| `em_hot_keyword` | heat | 个股热门关键词，Top100 概念关联 |
| `heat_change_top` | heat | 盘中热度飙升Top20（每只股票每天只入选一次，记录入选时间） |
| `heat_stock_minute` | heat | 热度Top股票1分钟K线（收盘后采集，用于回测） |
| `articles` | news | 个股新闻文章，7 天滚动 |
| `guba_sentiment` | guba | 股吧情绪得分（每日每股一条，百分制） |
| `guba_post_detail` | guba | 股吧帖子明细（Top3 热门帖子） |
| `news_analysis` | analysis | LLM 分析结果（国际/国内/个股，百分制多维评分） |
| `analysis_failure` | analysis | 分析失败记录（断点续跑 + 失败重试） |
| `industry_analysis` | industry | 申万行业逐行业 LLM 分析（每行业每日一条） |
| `sector_analysis` | sector | 东财板块逐板块 LLM 分析（每板块每日一条） |

## 核心设计

### 分层架构

```
fetcher.py / analyzer.py   能力层：纯 HTTP/API 调用，不关心调度和限流
         ↓
main.py                    调度层：间隔控制、并发管理、游标断点、入库编排
         ↓
database.py                数据层：batch_upsert / batch_insert_ignore，幂等写入
```

### 并发模型

- **串行搜索 + 并发 LLM**：主线程按顺序发起 HTTP 请求（限流间隔），搜到数据后立即提交到线程池做 LLM 分析
- **Semaphore 控制并发**：analysis 3 线程、guba 10 线程
- **滑动窗口收割**：主线程搜索新股时，同步收割已完成的 LLM future

### 断点续跑

- `AnalysisRun` 表记录运行状态（run_id、游标、失败数）
- 中断后重跑，自动从游标位置继续
- `--no-resume` 强制新建 run

### 反爬策略

- **curl_cffi + chrome120 指纹**：绕过今日头条 TLS 指纹检测
- **Clash 代理轮换**：搜索失败时自动切换节点，测速后选择可用节点
- **自适应退避**：空结果按指数退避重试（20-30s 基准间隔）

### 幂等写入

- `batch_upsert()`：MySQL `ON DUPLICATE KEY UPDATE`，重复记录更新指定字段
- `batch_insert_ignore()`：MySQL `INSERT IGNORE`，重复记录直接跳过
- 分批提交（默认 500 条/批），中途崩溃不丢已提交数据

## 技术栈

- **Python 3.11+** / SQLAlchemy / MySQL
- **HTTP 客户端**：httpx（heat）、curl_cffi（news/guba/analysis）
- **数据接口**：AkShare（热度趋势/关键词）、东方财富 API（人气排名）
- **LLM**：OpenAI-compatible API（火山方舟 doubao-seed-2.0-pro）
- **代理**：Clash REST API 动态切换节点
