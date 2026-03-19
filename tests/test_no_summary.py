"""对比测试：带摘要 vs 不带摘要 的 LLM 分析效果

测试目标：
- 头条搜索结果本身就是摘要，再喂给 LLM 是否多余
- 去掉 summary 只保留 title，LLM 输出质量是否有变化
- 不带 summary 能节省多少 token

用法:
    cd /root/my-claude/my-trend-wt-claude01
    python -m tests.test_no_summary --stock 601789
    python -m tests.test_no_summary --stock 600519
    python -m tests.test_no_summary --global
    python -m tests.test_no_summary --industry 煤炭
"""

import argparse
import json
import logging
import sys
import time
from datetime import date

sys.path.insert(0, ".")

from config import load_config
from analysis.analyzer import (
    _collect_news, _call_llm,
    GLOBAL_QUERIES, GLOBAL_PROMPT,
    DOMESTIC_QUERIES, DOMESTIC_PROMPT,
    STOCK_PROMPT,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("test_no_summary")


def _format_news_title_only(raw_items_text: str) -> str:
    """从已格式化的新闻文本中去掉 summary，只保留标题行"""
    lines_out = []
    for block in raw_items_text.split("\n\n"):
        # 每个 block: "{i}. [{date}] {title}\n{summary}"
        first_line = block.split("\n")[0]
        lines_out.append(first_line)
    return "\n\n".join(lines_out)


def compare_analysis(label: str, queries: list[str], prompt_tpl: str,
                     fmt_kwargs: dict, llm_cfg, max_count: int = 30):
    """对同一组新闻，分别用【带摘要】和【仅标题】两种方式调用 LLM，对比结果"""

    # 1. 收集新闻（带摘要的完整版本）
    logger.info(f"=== [{label}] 搜索新闻 ===")
    articles_with_summary = _collect_news(queries, max_count=max_count)
    if not articles_with_summary:
        logger.error("未搜到任何新闻，无法测试")
        return

    # 2. 生成仅标题版本
    articles_title_only = _format_news_title_only(articles_with_summary)

    # 3. 打印对比
    print("\n" + "=" * 80)
    print(f"[{label}] 新闻内容对比")
    print("=" * 80)

    print(f"\n--- 【带摘要】({len(articles_with_summary)} 字符) ---")
    print(articles_with_summary[:1500])
    if len(articles_with_summary) > 1500:
        print(f"... (省略，共 {len(articles_with_summary)} 字符)")

    print(f"\n--- 【仅标题】({len(articles_title_only)} 字符) ---")
    print(articles_title_only[:1500])
    if len(articles_title_only) > 1500:
        print(f"... (省略，共 {len(articles_title_only)} 字符)")

    saved = len(articles_with_summary) - len(articles_title_only)
    pct = saved / len(articles_with_summary) * 100 if articles_with_summary else 0
    print(f"\n--- 字符节省: {saved} 字符 ({pct:.1f}%) ---")

    # 4. 调用 LLM（带摘要）
    print(f"\n{'=' * 80}")
    print(f"[{label}] LLM 分析对比")
    print("=" * 80)

    prompt_a = prompt_tpl.format(articles=articles_with_summary, **fmt_kwargs)
    print(f"\n>>> 调用 LLM【带摘要】(prompt {len(prompt_a)} 字符)...")
    t0 = time.time()
    result_a = _call_llm(prompt_a, llm_cfg)
    time_a = time.time() - t0

    # 5. 调用 LLM（仅标题）
    prompt_b = prompt_tpl.format(articles=articles_title_only, **fmt_kwargs)
    print(f">>> 调用 LLM【仅标题】(prompt {len(prompt_b)} 字符)...")
    t0 = time.time()
    result_b = _call_llm(prompt_b, llm_cfg)
    time_b = time.time() - t0

    # 6. 对比输出
    print(f"\n{'=' * 80}")
    print(f"[{label}] 结果对比")
    print("=" * 80)

    print(f"\n--- 【带摘要】结果 (耗时 {time_a:.1f}s) ---")
    if result_a:
        print(json.dumps(result_a, ensure_ascii=False, indent=2))
    else:
        print("(LLM 调用失败)")

    print(f"\n--- 【仅标题】结果 (耗时 {time_b:.1f}s) ---")
    if result_b:
        print(json.dumps(result_b, ensure_ascii=False, indent=2))
    else:
        print("(LLM 调用失败)")

    # 7. 评分对比
    if result_a and result_b:
        print(f"\n--- 评分对比 ---")
        score_a = result_a.get("score", "N/A")
        score_b = result_b.get("score", "N/A")
        print(f"  带摘要 score: {score_a}")
        print(f"  仅标题 score: {score_b}")

        sentiment_a = result_a.get("sentiment", "N/A")
        sentiment_b = result_b.get("sentiment", "N/A")
        print(f"  带摘要 sentiment: {sentiment_a}")
        print(f"  仅标题 sentiment: {sentiment_b}")

        summary_a = result_a.get("summary", "")
        summary_b = result_b.get("summary", "")
        print(f"\n  带摘要 summary: {summary_a}")
        print(f"  仅标题 summary: {summary_b}")

        # 子维度评分对比
        scores_a = result_a.get("scores", {})
        scores_b = result_b.get("scores", {})
        if scores_a and scores_b:
            print(f"\n--- 子维度评分对比 ---")
            all_dims = set(list(scores_a.keys()) + list(scores_b.keys()))
            for dim in sorted(all_dims):
                sa = scores_a.get(dim, {}).get("score", "N/A")
                sb = scores_b.get(dim, {}).get("score", "N/A")
                ra = scores_a.get(dim, {}).get("reason", "")
                rb = scores_b.get(dim, {}).get("reason", "")
                print(f"  {dim}: 带摘要={sa}({ra}) vs 仅标题={sb}({rb})")

    print(f"\n--- Prompt 长度对比 ---")
    print(f"  带摘要: {len(prompt_a)} 字符")
    print(f"  仅标题: {len(prompt_b)} 字符")
    print(f"  节省: {len(prompt_a) - len(prompt_b)} 字符 ({(len(prompt_a) - len(prompt_b)) / len(prompt_a) * 100:.1f}%)")


def main():
    parser = argparse.ArgumentParser(description="对比测试：带摘要 vs 仅标题")
    parser.add_argument("--stock", help="个股代码，如 601789")
    parser.add_argument("--stock-name", help="公司名，如 宁波建工（不填则用代码搜索）")
    parser.add_argument("--global", dest="global_", action="store_true", help="测试国际形势")
    parser.add_argument("--domestic", action="store_true", help="测试国内形势")
    parser.add_argument("--industry", help="测试行业分析，如 煤炭")
    parser.add_argument("--sector", help="测试板块分析，如 公用事业")
    args = parser.parse_args()

    cfg = load_config()
    llm = cfg.llm

    if args.global_:
        compare_analysis("国际形势", GLOBAL_QUERIES, GLOBAL_PROMPT, {}, llm)

    elif args.domestic:
        compare_analysis("国内形势", DOMESTIC_QUERIES, DOMESTIC_PROMPT, {}, llm)

    elif args.stock:
        name = args.stock_name or args.stock
        queries = [name, f"{name} 项目", f"{name} 利润", f"{name} 行业前景", f"{name} 产品"]
        compare_analysis(
            f"个股-{name}({args.stock})", queries, STOCK_PROMPT,
            {"code": args.stock, "name": name, "today": date.today()}, llm
        )

    elif args.industry:
        from industry.analyzer import INDUSTRY_PROMPT
        name = args.industry
        queries = [f"{name}行业", f"{name} 政策 产能 龙头 前景"]
        compare_analysis(
            f"行业-{name}", queries, INDUSTRY_PROMPT,
            {"name": name, "today": date.today()}, llm
        )

    elif args.sector:
        from sector.analyzer import SECTOR_PROMPT
        name = args.sector
        queries = [f"{name}板块", f"{name} 政策 资金 龙头 前景"]
        compare_analysis(
            f"板块-{name}", queries, SECTOR_PROMPT,
            {"name": name, "today": date.today()}, llm
        )

    else:
        parser.print_help()
        print("\n请指定至少一个测试类型")


if __name__ == "__main__":
    main()
