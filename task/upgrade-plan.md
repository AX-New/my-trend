# my-trend 升级计划

> 状态: 进行中 | 创建: 2026-03-14 | 更新: 2026-03-15

## 已完成

### heat 包 —— 热度数据

| 功能 | 命令 | 数据源 | 数据量 | 状态 |
|------|------|--------|--------|------|
| 人气排名 | `heat.main` | 东方财富选股 API | ~5500 条/天 | ✅ |
| 热门关键词 | `heat.main --keyword` | AkShare keyword_em | ~700 条/天 | ✅ |
| 百度热搜 | `heat.main --baidu` | AkShare hot_search_baidu | 36 条/天 | ✅ |
| 历史回溯 | `heat.main --init` | AkShare detail_em | ~200万条（仅首次） | ✅ |

执行顺序：`heat.main` → `heat.main --keyword`（关键词依赖当日排名 Top100），`--baidu` 和 `--init` 独立。

### news 包 —— 新闻资讯

| 功能 | 命令 | 数据源 | 数据量 | 状态 |
|------|------|--------|--------|------|
| 24小时资讯 | `news.main --global` | 东方财富 stock_info_global_em | ~200 条/次 | ✅ |
| 个股资讯 | `news.main` | Google News + 东方财富 + 新浪 | 按股票数 | ✅ |

### 基础设施

| 项目 | 状态 |
|------|------|
| 包拆分（heat/news/guba/analysis） | ✅ |
| 无事务写入（batch_upsert / batch_insert_ignore） | ✅ |
| APScheduler 移除，统一单次执行 | ✅ |
| config.yaml 创建 | ✅ |

## 待实现

| 优先级 | 功能 | 说明 |
|--------|------|------|
| 1 | **news: 新闻资讯** | 从 my-news 项目 `news_cache.news_items` 读取已去重已摘要的新闻 |
| 2 | **news: 行业资讯** | 配置行业关键词，数据源待定 |
| 3 | **guba: 股吧情绪采样** | popularity_rank 三区间采样 30 只 → 股吧帖子 → LLM 情绪分析 |
| 4 | **analysis: LLM 批量分析** | 自选股新闻基本面摘要，写入 stock_daily |

## 数据表

| 表 | 包 | 说明 | 状态 |
|----|-----|------|------|
| `popularity_rank` | heat | 人气排名每日快照，全市场 ~5500 只 | 核心 |
| `em_hot_rank_detail` | heat | 个股历史趋势+粉丝 366 天 | 核心 |
| `em_hot_keyword` | heat | 个股热门关键词，Top100 概念关联 | 核心 |
| `baidu_hot_search` | heat | 百度热搜 A股/港股/美股 Top12 | 核心 |
| `global_news` | news | 24小时全球资讯 | 核心 |
| `articles` | news | 个股新闻文章，7 天滚动 | 核心 |
| `stock_daily` | analysis | 每日指标（声量/情绪/热度） | 待实现 |
| `em_hot_rank` | heat | 东方财富人气榜 Top100 | 备用 |
| `em_hot_up` | heat | 东方财富飙升榜 Top100 | 备用 |
| `em_hot_rank_realtime` | heat | 个股实时排名变动 | 备用 |
| `em_hot_rank_latest` | heat | 个股最新排名详情 | 备用 |
| `em_hot_rank_relate` | heat | 个股相关股票 | 备用 |
| `xq_hot_rank` | heat | 雪球三榜 | 备用 |
