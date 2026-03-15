"""配置加载模块，从 config.yaml 读取所有配置项"""

import yaml
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class DatabaseConfig:
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    db: str = "my_trend"
    charset: str = "utf8mb4"

    @property
    def url(self) -> str:
        return (
            f"mysql+pymysql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.db}?charset={self.charset}"
        )


@dataclass
class LLMConfig:
    base_url: str = "http://localhost:18789/v1"
    api_key: str = "sk-xxx"
    model: str = "ark-code-latest"
    max_tokens: int = 1024
    temperature: float = 0.3


@dataclass
class SourceConfig:
    name: str
    url: str
    category: str = "news"  # news/finance/forum/stock
    language: str = "en"
    type: str = "rss"
    enabled: bool = True


@dataclass
class SchedulerConfig:
    interval_minutes: int = 15
    max_articles_per_run: int = 500
    stocks_per_run: int = 10
    max_workers: int = 5


@dataclass
class NetworkConfig:
    timeout: int = 30
    proxy: str = ""
    user_agent: str = "my-trend/1.0"


@dataclass
class StockInfo:
    """股票元信息，从 stocks.txt 解析"""
    code: str
    name: str = ""
    industry: str = ""


@dataclass
class PopularityConfig:
    enabled: bool = True
    page_size: int = 50


@dataclass
class AkshareConfig:
    """AkShare 热度数据配置"""
    enabled: bool = True
    detail_source: str = "rank_top"  # rank_top=人气榜Top100 / stocks_file=stocks.txt
    detail_top_n: int = 20           # detail_source=rank_top 时取前 N 只


@dataclass
class AppConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    sources: list[SourceConfig] = field(default_factory=list)
    stocks: list[str] = field(default_factory=list)
    stocks_file: str = ""
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    popularity: PopularityConfig = field(default_factory=PopularityConfig)
    akshare: AkshareConfig = field(default_factory=AkshareConfig)


def load_config(path: str = None) -> AppConfig:
    """加载配置文件，支持自定义路径"""
    if path is None:
        path = Path(__file__).parent / "config.yaml"
    else:
        path = Path(path)

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # 逐块解析，缺失的用默认值
    db_raw = raw.get("database", {})
    llm_raw = raw.get("llm", {})
    scheduler_raw = raw.get("scheduler", {})
    network_raw = raw.get("network", {})

    sources = []
    for s in raw.get("sources", []):
        sources.append(SourceConfig(**s))

    stocks = raw.get("stocks", [])
    stocks_file = raw.get("stocks_file", "")

    popularity_raw = raw.get("popularity", {})
    akshare_raw = raw.get("akshare", {})

    return AppConfig(
        database=DatabaseConfig(**db_raw),
        llm=LLMConfig(**llm_raw),
        sources=sources,
        stocks=stocks,
        stocks_file=stocks_file,
        scheduler=SchedulerConfig(**scheduler_raw),
        network=NetworkConfig(**network_raw),
        popularity=PopularityConfig(**popularity_raw),
        akshare=AkshareConfig(**akshare_raw),
    )
