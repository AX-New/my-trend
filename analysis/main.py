"""分析调度层（待实现）

用法：
  python -m analysis.main   # 对未分析的文章执行 LLM 分析
"""

import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("analysis")


def main():
    parser = argparse.ArgumentParser(description="新闻+行情分析")
    parser.add_argument("-c", "--config", default=None)
    args = parser.parse_args()

    logger.info("analysis.main 待实现（LLM 批量分析 → stock_daily）")


if __name__ == "__main__":
    main()
