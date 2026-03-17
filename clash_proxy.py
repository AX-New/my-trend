"""
Clash 代理动态切换模块

从日本/美国/新加坡 VIP1 节点池中切换代理。
维护有序节点列表+游标，rotate_proxy() 从游标位置顺序轮换，
测速不通过则跳过，转完一圈抛异常。懒加载，首次调用时初始化。
"""

import logging

import httpx
from urllib.parse import quote

logger = logging.getLogger(__name__)

CLASH_API = "http://127.0.0.1:9090"
SELECTOR_GROUP = "🚀 节点选择"
AUTO_SELECT = "♻️ 自动选择"
MAX_DELAY = 600
DELAY_TEST_URL = "http://www.gstatic.com/generate_204"
DELAY_TIMEOUT = 3000  # ms
PROXY_URL = "http://127.0.0.1:7890"

REGION_KEYWORDS = ["日本", "美国", "新加坡"]

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# 节点列表 + 游标（懒加载）
_node_list: list[str] | None = None
_cursor: int = 0


def _is_candidate(name: str) -> bool:
    return "VIP1" in name and any(k in name for k in REGION_KEYWORDS)


def _fetch_candidates() -> list[str]:
    """从 Clash API 拉取候选节点列表（保持原始顺序）"""
    r = httpx.get(f"{CLASH_API}/proxies/{quote(SELECTOR_GROUP)}")
    r.raise_for_status()
    all_proxies = r.json().get("all", [])
    return [p for p in all_proxies if _is_candidate(p)]


def _ensure_init():
    """懒加载：首次调用时初始化节点列表"""
    global _node_list, _cursor
    if _node_list is None:
        _node_list = _fetch_candidates()
        _cursor = 0
        logger.info("初始化节点列表，共 %d 个候选节点", len(_node_list))


def reset_node_list():
    """重置节点列表，下次 rotate 时重新拉取"""
    global _node_list, _cursor
    _node_list = None
    _cursor = 0


def get_current_proxy() -> str:
    r = httpx.get(f"{CLASH_API}/proxies/{quote(SELECTOR_GROUP)}")
    r.raise_for_status()
    return r.json().get("now", "")


def test_delay(name: str) -> int | None:
    """测试节点延迟，不可用返回 None"""
    try:
        r = httpx.get(
            f"{CLASH_API}/proxies/{quote(name)}/delay",
            params={"timeout": DELAY_TIMEOUT, "url": DELAY_TEST_URL},
            timeout=10,
        )
        return r.json().get("delay")
    except Exception:
        return None


def switch_to(name: str) -> bool:
    r = httpx.put(
        f"{CLASH_API}/proxies/{quote(SELECTOR_GROUP)}",
        json={"name": name},
    )
    return r.status_code == 204


def rotate_proxy() -> str:
    """
    顺序轮换代理：从游标位置向后遍历节点列表，
    测速通过就切换并停在该位置，下次从下一个继续。
    跳过当前节点和不可用节点，转完一圈抛异常。
    """
    global _cursor
    _ensure_init()

    original = get_current_proxy()
    n = len(_node_list)
    if n == 0:
        raise RuntimeError("候选节点列表为空")

    logger.info("轮换代理，当前: %s，游标: %d/%d", original, _cursor, n)

    for _ in range(n):
        name = _node_list[_cursor % n]
        _cursor = (_cursor + 1) % n  # 无论成功失败，游标都前进

        if name == original:
            continue

        delay = test_delay(name)
        if delay is not None and delay <= MAX_DELAY:
            switch_to(name)
            logger.info("已切换: %s -> %s (%dms)，游标停在 %d", original, name, delay, _cursor)
            return name
        else:
            logger.debug("跳过不可用节点: %s (delay=%s)", name, delay)

    raise RuntimeError(f"所有 {n} 个候选节点不可用，当前保持: {original}")


def select_best_proxy() -> str:
    """
    从所有候选中选延迟最低的节点切换（全量测速，不走游标）。
    全部不可用时恢复原节点并抛出 RuntimeError。
    """
    original = get_current_proxy()
    candidates = _fetch_candidates()
    logger.info("选择最优代理，候选: %d 个", len(candidates))

    available = []
    for name in candidates:
        delay = test_delay(name)
        if delay is not None and delay <= MAX_DELAY:
            available.append((name, delay))

    if not available:
        switch_to(original)
        raise RuntimeError(f"所有候选节点不可用，已恢复: {original}")

    available.sort(key=lambda x: x[1])
    best_name, best_delay = available[0]
    switch_to(best_name)
    logger.info("已选择最优: %s (%dms)", best_name, best_delay)
    return best_name


def restore_auto() -> None:
    """恢复自动选择"""
    switch_to(AUTO_SELECT)
    logger.info("已恢复自动选择")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(f"当前: {get_current_proxy()}")
    print("-" * 40)

    # 演示：轮换3次
    for i in range(3):
        try:
            node = rotate_proxy()
            print(f"第{i+1}次轮换 -> {node}")
        except RuntimeError as e:
            print(f"第{i+1}次轮换失败: {e}")
            break

    print("-" * 40)
    restore_auto()
    print(f"最终: {get_current_proxy()}")
