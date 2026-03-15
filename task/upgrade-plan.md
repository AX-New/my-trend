# my-trend 升级计划

> 状态: 进行中 | 创建: 2026-03-14 | 更新: 2026-03-15

## 架构定位

两条核心数据线 + 一条补充线：

| 数据线 | 数据源 | 用途 | 状态 |
|--------|--------|------|------|
| **人气排名** | 东方财富选股 API (popularity_rank) | 全市场每日排名，热度核心指标 | ✅ 已完成 |
| **热度趋势** | AkShare (em_hot_rank_detail) | 个股 366 天排名趋势+粉丝特征 | ✅ 已完成 |
| **新闻采集** | Google News / 东方财富 / 新浪 | 基本面舆情补充（非热度指标） | ✅ 已完成 |
| **股吧情绪** | 股吧页面采样 + LLM | 市场情绪分析 | ⬜ 待实现 |

### 为什么只用这两个接口

AkShare 提供了 10 个热度接口（已全部实现在 `akshare_fetcher.py`），但实际有价值的只有两个：

| 接口 | 结论 |
|------|------|
| **popularity_rank（选股 API）** | ✅ 核心。全市场 ~5500 只，每日完整排名+行情数据 |
| **stock_hot_rank_detail_em** | ✅ 补充。366 天历史趋势线+粉丝变化，用于趋势分析 |
| stock_hot_rank_em / up_em | ❌ 只有 Top100，是 popularity_rank 的子集 |
| stock_hot_rank_detail_realtime_em | ❌ 盘中分钟级波动，噪音大，不适合日度分析 |
| stock_hot_keyword_em | ❌ 概念关联，不是热度本身 |
| stock_hot_rank_latest_em | ❌ 和 rank + detail 数据重叠 |
| stock_hot_rank_relate_em | ❌ 关联分析，不是监控指标 |
| stock_hot_follow/tweet/deal_xq | ❌ 雪球数据质量差，数值含义模糊 |

### 核心数据模型

```
popularity_rank（每日快照）
├─ 全市场 ~5500 只股票
├─ 字段: stock_code, date, rank, new_price, change_rate, volume_ratio, turnover_rate, volume, deal_amount
├─ 唯一约束: (stock_code, date)
└─ 数据源: 东方财富选股 API，分页采集 ~110 页

em_hot_rank_detail（历史趋势，init 时一次性回溯）
├─ 单股 366 天历史排名+粉丝
├─ 字段: stock_code, timestamp, rank, new_fans, hardcore_fans
├─ 唯一约束: (stock_code, timestamp)
└─ 数据源: AkShare stock_hot_rank_detail_em
```

## 已完成

### 人气排名采集
- 东方财富选股 API，全市场 ~5500 只股票
- 分两段 filter 分页采集（<=1000 和 >1000），约 110 页
- PopularityRank 模型，按 (stock_code, date) 唯一约束，每日 upsert
- config.yaml 中 popularity 配置块控制开关和分页

### 新闻采集
- 固定关键词（股票代码 + 公司名），不用 LLM query expansion
- StockInfo 从 stocks.txt 解析代码、名称、行业
- 7 天滚动清理，定位为基本面补充

### AkShare 接口全量实现
- `akshare_fetcher.py` 实现了全部 10 个 A 股热度接口（备用）
- `models.py` 建了 8 张表
- `rank_em` / `up_em` 有 emappdata 直调降级，push2 挂掉也能用
- 已集成到 `main.py` pipeline（可通过 config.yaml akshare.enabled 开关）

### 热度每日监控
- 独立脚本 `monitor.py`
- `--init`：回溯 popularity_rank Top200 的 366 天历史趋势（~72K 条，约 3 分钟）
- 每日常规：只跑 `popularity_rank`（在 main.py pipeline 中）
- 热度趋势由 popularity_rank 每日快照自然累积，不需要重复调 detail 接口

## 待实施

### 股吧情绪采样
- 从 popularity_rank 表取三个区间采样：top 1-10, 90-100, 490-500（共 30 只）
- 每只股票抓取股吧页面帖子标题
- 每只单独调用 LLM 分析情绪（30 次调用/轮）
- 三个区间的情绪差异本身是信号

### LLM 批量分析（可选）
- 对自选股的新闻做基本面摘要
- 写入 stock_daily 表

## 文件清单

| 文件 | 职责 |
|------|------|
| `main.py` | 主 pipeline：清理→数据源→人气排名→AkShare→个股新闻→入库 |
| `monitor.py` | 热度监控独立脚本（`--init` 回溯历史趋势） |
| `akshare_fetcher.py` | AkShare 10 接口采集（重点用 detail_em，其余备用） |
| `fetcher.py` | RSS/API 新闻 + 人气排名(选股API)采集 |
| `models.py` | 全部数据表（PopularityRank + AkShare 8 张 + Article/StockDaily） |
| `config.py` | 配置加载（含 AkshareConfig） |
| `config.yaml` | 所有配置项 |
| `analyzer.py` | LLM 分析（备用） |
| `stocks.txt` | 自选股列表（代码+名称+行业） |
