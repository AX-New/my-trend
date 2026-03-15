# my-trend

股票热度研究系统。核心用人气排名追踪全市场热度，辅以历史趋势回溯和新闻采集，结果存入 MySQL 用于与行情数据做相关性分析。

## 数据线

| 数据线 | 数据源 | 范围 | 说明 |
|--------|--------|------|------|
| **人气排名** | 东方财富选股 API | 全市场 ~5500 只/天 | 热度核心指标 |
| **热度趋势** | AkShare stock_hot_rank_detail_em | 个股 366 天历史 | 排名趋势+粉丝特征，init 时回溯 |
| 新闻采集 | Google News / 东方财富 / 新浪 | 自选股，7 天滚动 | 基本面补充 |
| 股吧情绪 | 股吧采样 + LLM | 排名采样 30 只 | 待实现 |

## 快速开始

```bash
pip install -r requirements.txt

# 复制配置文件并填写数据库密码、LLM key
cp config.yaml.example config.yaml

# 主 pipeline：人气排名 + 新闻采集
python main.py --once --no-analyze

# 热度监控：首次运行（回溯 366 天历史趋势）
python monitor.py --init

# 热度监控：每日常规
python monitor.py

# 定时调度（默认每 15 分钟）
python main.py
```

## 命令行

```bash
# main.py
python main.py --once              # 单次执行
python main.py --no-analyze        # 跳过 LLM 分析
python main.py --stocks 600519,000858
python main.py --all-stocks        # 不分批，一次跑完
python main.py --category news     # 只采集指定板块
python main.py -c path/to/config   # 自定义配置

# monitor.py
python monitor.py --init           # 首次：人气榜+飙升榜+历史趋势回溯
python monitor.py                  # 每日：人气榜+飙升榜+实时排名+热门关键词
python monitor.py -c config.yaml
```

## 项目结构

```
my-trend/
├── main.py              # 主 pipeline（新闻+人气排名+AkShare）
├── monitor.py           # 热度每日监控独立脚本
├── akshare_fetcher.py   # AkShare 10 个热度接口采集
├── fetcher.py           # RSS/API 新闻 + 人气排名采集
├── analyzer.py          # LLM 分析（备用）
├── models.py            # SQLAlchemy ORM（11 张表）
├── database.py          # 数据库连接封装
├── config.py            # 配置 dataclass
├── config.yaml.example  # 配置模板
├── stocks.txt           # 自选股票列表
├── requirements.txt     # Python 依赖
└── task/upgrade-plan.md # 任务计划
```

## 数据表

| 表 | 说明 |
|----|------|
| `popularity_rank` | 人气排名每日快照，全市场 ~5500 只 |
| `em_hot_rank_detail` | 个股历史趋势+粉丝（366天） |
| `em_hot_rank` | 东方财富人气榜 Top100 |
| `em_hot_up` | 东方财富飙升榜 Top100 |
| `em_hot_rank_realtime` | 个股实时排名变动 |
| `em_hot_keyword` | 个股热门关键词 |
| `em_hot_rank_latest` | 个股最新排名详情 |
| `em_hot_rank_relate` | 个股相关股票 |
| `xq_hot_rank` | 雪球三榜（关注/讨论/交易） |
| `articles` | 新闻文章，7 天滚动 |
| `stock_daily` | 每日指标（声量/情绪/热度） |

## 技术栈

Python 3.11+ / SQLAlchemy / MySQL / httpx / AkShare / APScheduler
