# my-trend

股票热度研究系统。四个独立包各自采集、分析、入库，结果存入 MySQL 用于与行情数据做相关性分析。

## 数据线

| 数据线 | 包 | 数据源 | 范围 | 状态 |
|--------|----|--------|------|------|
| **人气排名** | `heat` | 东方财富选股 API | 全市场 ~5500 只/天 | ✅ |
| **热度趋势** | `heat` | AkShare detail_em | 个股 366 天历史 | ✅ |
| **热门关键词** | `heat` | AkShare keyword_em | Top100 概念关联 | ✅ |
| 新闻采集 | `news` | Google News / 东方财富 / 新浪 | 自选股，7 天滚动 | ✅ |
| 股吧情绪 | `guba` | 股吧采样 + LLM | 排名采样 30 只 | 待实现 |
| 分析 | `analysis` | LLM 批量分析 | 自选股新闻 | 待实现 |

## 快速开始

```bash
pip install -r requirements.txt
cp config.yaml.example config.yaml   # 填写数据库密码

# 热度采集（人气排名，全市场）
python -m heat.main

# Top100 热门关键词
python -m heat.main --keyword

# 热度回溯（366天历史趋势，仅首次）
python -m heat.main --init

# 新闻采集（单次）
python -m news.main --once
```

## 命令行

```bash
# heat — 热度
python -m heat.main                    # 每日人气排名（全市场 ~5500 只）
python -m heat.main --keyword          # Top100 热门关键词
python -m heat.main --init             # 首次回溯（从 my_stock.stock_basic 读全量，~1.5h）
python -m heat.main -c config.yaml     # 指定配置

# news — 新闻
python -m news.main --once             # 单次采集
python -m news.main                    # 定时调度（默认15分钟）
python -m news.main --once --stocks 600519,000858
python -m news.main --once --category news
python -m news.main --once --all-stocks

# analysis — 分析（待实现）
python -m analysis.main
```

### 日常运行顺序

```bash
python -m heat.main            # 1. 先跑人气排名
python -m heat.main --keyword  # 2. 再跑关键词（依赖当日排名 Top100）
```

`--init` 仅首次运行一次，从 `my_stock.stock_basic` 读取全部在市股票（~5500 只），逐只采集 366 天历史趋势，并回写到 `popularity_rank` 表。

## 项目结构

```
my-trend/
├── database.py              # 共享 Base + batch_upsert / batch_insert_ignore
├── config.py                # 共享配置 + load_stocks
├── config.yaml.example      # 配置模板
├── stocks.txt               # 自选股票列表
│
├── heat/                    # 热度包
│   ├── fetcher.py           #   能力层：popularity_page + akshare 10 接口
│   ├── models.py            #   PopularityRank + EmHotRankDetail + EmHotKeyword + 备用 6 张表
│   └── main.py              #   调度层：人气排名 / 关键词 / 历史回溯
│
├── news/                    # 新闻包
│   ├── fetcher.py           #   能力层：RSS / GoogleNews / 东方财富 / 新浪
│   ├── models.py            #   Article（url_hash 去重）
│   └── main.py              #   调度层：1s 间隔限流
│
├── guba/                    # 股吧情绪（待实现）
│
├── analysis/                # 分析包
│   ├── analyzer.py          #   能力层：LLM 单篇/批量分析
│   ├── models.py            #   StockDaily
│   └── main.py              #   调度层（待实现）
│
└── task/upgrade-plan.md     # 任务计划
```

## 设计原则

- **能力层 vs 调度层**：`fetcher.py` 是纯接口调用（无限流），`main.py` 是调度编排（1s 间隔）
- **包独立**：每个包自带 models + main，可独立运行
- **无事务**：不使用 rollback，每次写入独立提交，分批提交防崩溃
- **幂等写入**：MySQL upsert / INSERT IGNORE，重复运行不报错不重复

## 编码原则

1. 代码简单
2. 注释详实，对于复杂的逻辑要有整体的注释，不能拆散写在单行
3. 需要有完善的日志处理逻辑，能够展示程序运行的进度



## git 提交格式

- `[ADD] - {线程ID} - {描述}` — 新增
- `[FIX] - {线程ID} - {描述}` — 修复
- `[MOD] - {线程ID} - {描述}` — 修改
- `[DEL] - {线程ID} - {描述}` — 删除



## 数据表

| 表 | 包 | 说明 |
|----|-----|------|
| `popularity_rank` | heat | 人气排名每日快照，全市场 ~5500 只（核心） |
| `em_hot_rank_detail` | heat | 个股历史趋势+粉丝 366 天（核心） |
| `em_hot_keyword` | heat | 个股热门关键词，Top100 概念关联+热度（核心） |
| `em_hot_rank` | heat | 东方财富人气榜 Top100（备用） |
| `em_hot_up` | heat | 东方财富飙升榜 Top100（备用） |
| `em_hot_rank_realtime` | heat | 个股实时排名变动（备用） |
| `em_hot_rank_latest` | heat | 个股最新排名详情（备用） |
| `em_hot_rank_relate` | heat | 个股相关股票（备用） |
| `xq_hot_rank` | heat | 雪球三榜（备用） |
| `articles` | news | 新闻文章，7 天滚动 |
| `stock_daily` | analysis | 每日指标（声量/情绪/热度） |

## 技术栈

Python 3.11+ / SQLAlchemy / MySQL / httpx / AkShare / APScheduler
