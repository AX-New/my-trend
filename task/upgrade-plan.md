# my-trend 升级计划

> 状态: 已完成 | 创建: 2026-03-14 | 更新: 2026-03-20

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

### industry 包 —— 行业分析（2026-03-19 新增）

| 功能 | 命令 | 说明 | 状态 |
|------|------|------|------|
| 全部行业 | `industry.main` | 申万L1 31个行业，头条搜新闻 → 豆包LLM多维评分 | ✅ |
| 单个行业 | `industry.main --name 煤炭` | 指定行业分析 | ✅ |
| 腾讯云 crontab | 每日 0:00 | 凌晨全量扫描31个行业 | ✅ |

### sector 包 —— 板块分析（2026-03-19 新增）

| 功能 | 命令 | 说明 | 状态 |
|------|------|------|------|
| 全部板块 | `sector.main` | 东财dc_index BK0+BK1 ~1010个，头条搜新闻 → 豆包LLM多维评分 | ✅ |
| 单个板块 | `sector.main --name 公用事业` | 指定板块分析 | ✅ |
| 腾讯云 crontab | 每日 1:00 | 凌晨全量扫描~1010个板块 | ✅ |

### 数据同步（my-stock sync_remote.py 扩展）

| 上传表 | 时间列 | 说明 | 状态 |
|--------|--------|------|------|
| dc_index | trade_date | 东财板块列表（sector 依赖） | ✅ |
| dc_concept | trade_date | 东财题材库 | ✅ |
| sw_daily | trade_date | 申万行业行情（industry 依赖） | ✅ |
| index_classify | 全量 | 申万行业分类（industry 依赖） | ✅ |

### 基础设施

| 项目 | 状态 |
|------|------|
| 包拆分（heat/news/guba/analysis） | ✅ |
| 无事务写入（batch_upsert / batch_insert_ignore） | ✅ |
| 统一百分制评分（50分中性，0-100） | ✅ |
| config.yaml 精简（删除 sources/akshare 配置） | ✅ |
| 代码清理（删除备用接口和未使用表） | ✅ |

## 数据表（10张）

| 表 | 包 | 说明 |
|----|-----|------|
| `popularity_rank` | heat | 人气排名每日快照 |
| `em_hot_rank_detail` | heat | 个股历史趋势+粉丝 366 天 |
| `em_hot_keyword` | heat | 个股热门关键词 |
| `articles` | news | 个股新闻文章，7 天滚动 |
| `guba_sentiment` | guba | 股吧情绪得分 |
| `guba_post_detail` | guba | 股吧帖子明细 |
| `news_analysis` | analysis | LLM 分析结果 |
| `analysis_failure` | analysis | 分析失败记录 |
| `industry_analysis` | industry | 申万行业逐行业 LLM 分析 |
| `sector_analysis` | sector | 东财板块逐板块 LLM 分析 |
