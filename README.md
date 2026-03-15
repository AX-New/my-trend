# my-trend

股票舆情与热度研究系统。采集人气排名、金融新闻，为行情分析提供热度和基本面数据支撑。

## 三条数据线

| 数据线 | 数据源 | 范围 | 频率 | 用途 |
|--------|--------|------|------|------|
| 人气排名 | 东方财富选股 API | 全市场 ~5500 只 | 每天 | 热度指标（核心） |
| 股吧情绪 | 股吧页面采样 | 排名采样 30 只 | 每天 | 市场情绪分析（待实现） |
| 新闻采集 | Google News / 东方财富 / 新浪 | 自选 200-300 只 | 每天，存 7 天滚动 | 基本面舆情补充 |

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置数据库和网络（编辑 config.yaml）
# 添加自选股票（编辑 stocks.txt）

# 单次执行全部采集
python main.py --once --no-analyze

# 启动定时调度（默认每 15 分钟）
python main.py
```

## 命令行参数

```bash
python main.py [OPTIONS]

--once              单次执行后退出（不启动定时调度）
--no-analyze        跳过 LLM 分析，只采集入库
--stocks 600519,000858  指定股票代码（逗号分隔）
--all-stocks        单次模式下处理全部股票（不分批）
--category news     只采集指定板块（news/finance/forum/stock）
-c path/to/config   使用自定义配置文件
```

## 项目结构

```
my-trend/
├── main.py          # 入口，pipeline 编排，定时调度
├── fetcher.py       # 数据抓取：RSS、搜索 API、人气排名
├── analyzer.py      # LLM 分析（情绪分析、摘要）
├── models.py        # SQLAlchemy ORM 模型
├── database.py      # 数据库连接封装
├── config.py        # 配置 dataclass
├── config.yaml      # 配置文件
├── stocks.txt       # 自选股票列表（代码 + 名称 + 行业分类）
└── requirements.txt # Python 依赖
```

## 数据表

| 表 | 说明 |
|----|------|
| `articles` | 新闻文章，7 天滚动保留，URL 去重 |
| `popularity_rank` | 人气排名每日快照，全市场 ~5500 只 |
| `stock_daily` | 每日指标时间序列（声量、情绪、热度） |

## Pipeline 流程

```
1. 清理 > 7 天过期文章
2. 采集常规 RSS/API 数据源
3. 采集人气排名（东方财富选股 API，~110 页分页）
4. 采集个股新闻（轮转分批，代码+公司名搜索，多源并发）
5. 逐条去重入库
```

## stocks.txt 格式

```
# ── 行业名 ──
600519  # 公司名
000858  # 公司名
```

行业分类用 `# ── XX ──` 标记，股票代码后 `#` 注释为公司名。系统自动解析用于搜索关键词生成。

## 配置

编辑 `config.yaml`，主要配置项：

- `database` — MySQL 连接信息
- `llm` — LLM API 配置（火山引擎豆包 / OpenAI 兼容）
- `sources` — RSS/API 数据源列表
- `popularity` — 人气排名采集开关和分页大小
- `scheduler` — 调度间隔、每轮股票数、并发数
- `network` — 超时、代理、UA

## 技术栈

Python 3.11+ / SQLAlchemy / MySQL / httpx / feedparser / APScheduler
