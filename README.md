# my-trend

股票舆情与热度研究系统。四个独立包各自采集、分析、入库，结果存入 MySQL 用于选股信号和相关性研究。

## 数据线

| 数据线 | 包 | 数据源 | 范围 |
|--------|----|--------|------|
| 人气排名 | `heat` | 东方财富选股 API | 全市场 ~5500 只/天 |
| 热度趋势 | `heat` | AkShare detail_em | 个股 366 天历史 |
| 热门关键词 | `heat` | AkShare keyword_em | Top100 概念关联 |
| 个股新闻 | `news` | 今日头条（主）+ 搜狗（备） | 自选股，7 天滚动 |
| 股吧情绪 | `guba` | 股吧帖子 + LLM 分类 | 单股/市场采样/全市场 |
| 国际形势 | `analysis` | 今日头条 → LLM 分析 | 全球经济/美联储等 |
| 国内形势 | `analysis` | 今日头条 → LLM 分析 | A股政策/中国经济等 |
| 个股基本面 | `analysis` | 今日头条 → LLM 多维评分 | 自选股/全量 la_pick |

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

### heat — 热度采集

```bash
python -m heat.main                    # 每日人气排名（全市场 ~5500 只）
python -m heat.main --keyword          # Top100 热门关键词（依赖当日排名）
python -m heat.main --init             # 首次回溯（366天历史趋势，~1.5h）
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
python -m guba.main --all                     # 全市场（~5500只，分批100入库）
```

### analysis — 基本面分析

```bash
python -m analysis.main --global              # 国际形势分析
python -m analysis.main --domestic            # 国内形势分析
python -m analysis.main --stock 600519        # 个股基本面
python -m analysis.main --stock 600519,000858 # 多只个股
python -m analysis.main --all                 # 国际+国内+stocks.txt个股
python -m analysis.main --all-stocks          # 国际+国内+la_pick全量个股
python -m analysis.main --retry               # 重跑今天失败的股票
python -m analysis.main --all-stocks --no-resume  # 强制全量重跑
```

### 日常运行顺序

```bash
python -m heat.main              # 1. 人气排名
python -m heat.main --keyword    # 2. 关键词（依赖步骤1的Top100）
python -m news.main --all-stocks # 3. 新闻采集
python -m guba.main --market     # 4. 股吧情绪采样
python -m analysis.main --all    # 5. 基本面分析
```

> heat 日常必须串行：`heat.main` → `heat.main --keyword`，关键词依赖当日排名 Top100。`--init` 仅首次运行。

## 项目结构

```
my-trend/
├── config.py                # 共享配置 + load_stocks
├── database.py              # 共享 Base + batch_upsert / batch_insert_ignore
├── config.yaml              # 数据库 + LLM 配置
├── stocks.txt               # 自选股票列表
│
├── heat/                    # 热度包
│   ├── fetcher.py           #   能力层：东财人气排名 + AkShare 3个接口
│   ├── models.py            #   PopularityRank + EmHotRankDetail + EmHotKeyword
│   └── main.py              #   调度层：人气排名 / 关键词 / 历史回溯
│
├── news/                    # 新闻包
│   ├── fetcher.py           #   能力层：今日头条（主）+ 搜狗（备），curl_cffi 绕反爬
│   ├── models.py            #   Article（url_hash 去重）
│   └── main.py              #   调度层：1s 间隔限流
│
├── guba/                    # 股吧情绪包（独立）
│   ├── fetcher.py           #   能力层：抓帖子 + LLM 情绪分类 + 加权评分
│   ├── models.py            #   GubaSentiment + GubaPostDetail
│   └── main.py              #   调度层：单股 / 市场采样 / 全市场（并发LLM）
│
├── analysis/                # 基本面分析包（独立）
│   ├── analyzer.py          #   能力层：搜新闻 → LLM 多维评分（国际/国内/个股）
│   ├── models.py            #   NewsAnalysis + AnalysisFailure
│   └── main.py              #   调度层：断点续跑 + 失败重试 + 并发LLM
│
└── task/                    # 任务文档
```

## 数据表（8 张）

| 表 | 包 | 说明 |
|----|-----|------|
| `popularity_rank` | heat | 人气排名每日快照，全市场 ~5500 只 |
| `em_hot_rank_detail` | heat | 个股历史趋势+粉丝 366 天 |
| `em_hot_keyword` | heat | 个股热门关键词，Top100 概念关联 |
| `articles` | news | 个股新闻文章，7 天滚动 |
| `guba_sentiment` | guba | 股吧情绪得分（每日每股一条，百分制） |
| `guba_post_detail` | guba | 股吧帖子明细（Top3 热门帖子） |
| `news_analysis` | analysis | LLM 分析结果（国际/国内/个股，百分制多维评分） |
| `analysis_failure` | analysis | 分析失败记录（断点续跑 + 失败重试） |

## 设计原则

- **能力层 vs 调度层**：`fetcher.py` 纯接口调用（无限流），`main.py` 调度编排（间隔控制 + 并发管理）
- **包独立**：`analysis` 和 `guba` 完全独立，各自有 models + main，可单独运行
- **并发模型**：主线程串行抓取（1s 限流），LLM 调用丢入线程池并发（Semaphore 控制上限）
- **断点续跑**：`analysis` 通过查 DB 已完成记录跳过，中断后重跑自动续接
- **失败追踪**：`analysis_failure` 表记录失败阶段和错误，`--retry` 重跑未解决的
- **幂等写入**：MySQL upsert / INSERT IGNORE，重复运行安全
- **统一评分**：百分制（0-100），50 为中性

## 技术栈

Python 3.11+ / SQLAlchemy / MySQL / curl_cffi / AkShare / OpenAI-compatible LLM（火山方舟 doubao-seed-2.0-pro）
