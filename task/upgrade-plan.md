# my-trend 升级计划

> 状态: 已完成 | 创建: 2026-03-14 | 更新: 2026-03-16

## 已完成

### heat 包 —— 热度数据

| 功能 | 命令 | 数据源 | 状态 |
|------|------|--------|------|
| 人气排名 | `heat.main` | 东方财富选股 API ~5500 条/天 | ✅ |
| 热门关键词 | `heat.main --keyword` | AkShare keyword_em ~700 条/天 | ✅ |
| ~~百度热搜~~ | ~~`heat.main --baidu`~~ | ~~AkShare hot_search_baidu~~ | 已删除 |
| 历史回溯 | `heat.main --init` | AkShare detail_em ~200万条（仅首次） | ✅ |

### news 包 —— 新闻资讯

| 功能 | 命令 | 数据源 | 状态 |
|------|------|--------|------|
| 个股新闻 | `news.main --stocks` | 今日头条搜索（主）+ 搜狗（备） | ✅ |

### guba 包 —— 股吧情绪

| 功能 | 命令 | 说明 | 状态 |
|------|------|------|------|
| 单股情绪 | `guba.main --stock` | 抓帖子 → LLM 分类 → 加权评分 | ✅ |
| 市场采样 | `guba.main --market` | 4区间40只，并发LLM（max 10线程） | ✅ |

### analysis 包 —— LLM 分析

| 功能 | 命令 | 说明 | 状态 |
|------|------|------|------|
| 国际形势 | `analysis.main --global` | 头条搜索全球经济/美联储等 → LLM 分析 | ✅ |
| 国内形势 | `analysis.main --domestic` | 头条搜索A股政策/中国经济等 → LLM 分析 | ✅ |
| 个股基本面 | `analysis.main --stock` | 头条搜索公司新闻 → LLM 综合评价 | ✅ |

### 基础设施

| 项目 | 状态 |
|------|------|
| 包拆分（heat/news/guba/analysis） | ✅ |
| 无事务写入（batch_upsert / batch_insert_ignore） | ✅ |
| 统一百分制评分（50分中性，0-100） | ✅ |
| config.yaml 精简（删除 sources/akshare 配置） | ✅ |
| 代码清理（删除备用接口和未使用表） | ✅ |

## 数据表（7张）

| 表 | 包 | 说明 |
|----|-----|------|
| `popularity_rank` | heat | 人气排名每日快照 |
| `em_hot_rank_detail` | heat | 个股历史趋势+粉丝 366 天 |
| `em_hot_keyword` | heat | 个股热门关键词 |
| `articles` | news | 个股新闻文章，7 天滚动 |
| `guba_sentiment` | guba | 股吧情绪得分 |
| `guba_post_detail` | guba | 股吧帖子明细 |
| `news_analysis` | analysis | LLM 分析结果 |
