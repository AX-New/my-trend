"""配置加载模块，从 config.yaml 读取所有配置项"""

import logging
import re
import yaml
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


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
class AppConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    stocks: list[str] = field(default_factory=list)
    stocks_file: str = ""
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    popularity: PopularityConfig = field(default_factory=PopularityConfig)


def load_config(path: str = None) -> AppConfig:
    """加载配置文件"""
    if path is None:
        path = Path(__file__).parent / "config.yaml"
    else:
        path = Path(path)

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return AppConfig(
        database=DatabaseConfig(**raw.get("database", {})),
        llm=LLMConfig(**raw.get("llm", {})),
        stocks=raw.get("stocks", []),
        stocks_file=raw.get("stocks_file", ""),
        scheduler=SchedulerConfig(**raw.get("scheduler", {})),
        network=NetworkConfig(**raw.get("network", {})),
        popularity=PopularityConfig(**raw.get("popularity", {})),
    )


def load_stocks(cfg: AppConfig, cli_stocks: str = None) -> list[StockInfo]:
    """加载股票列表，从 stocks.txt 解析代码、名称、行业"""
    if cli_stocks:
        return [StockInfo(code=c.strip()) for c in cli_stocks.split(",") if c.strip()]

    if cfg.stocks_file:
        path = Path(cfg.stocks_file)
        if not path.is_absolute():
            path = Path(__file__).parent / path
        if path.exists():
            stocks = []
            current_industry = ""
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                m = re.match(r'^#\s*──\s*(.+?)\s*──', line)
                if m:
                    current_industry = m.group(1)
                    continue
                parts = line.split("#", 1)
                code = parts[0].strip()
                name = parts[1].strip() if len(parts) > 1 else ""
                if code:
                    stocks.append(StockInfo(
                        code=code, name=name, industry=current_industry,
                    ))
            logger.info(f"从 {path} 加载 {len(stocks)} 只股票")
            return stocks
        logger.warning(f"股票文件不存在: {path}")

    return [StockInfo(code=c) for c in (cfg.stocks or [])]
