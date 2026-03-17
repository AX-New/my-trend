# 代理热插拔与动态参数调节 — 设计文档

> 文档版本：v1.0 | 日期：2026-03-17

## 1. 背景与问题

### 1.1 当前代理架构

当前系统通过 `clash_proxy.py` 管理代理，只有 `analysis` 包使用代理（头条搜索走 Clash 代理），`news` 包直连，`heat` 和 `guba` 包不涉及代理。

**现状痛点：**

| 问题 | 影响 |
|------|------|
| 代理逻辑硬编码在 `analyzer.py` 中 | `news` 包无法复用代理切换能力 |
| IP 切换时必须重建整个 session | 增加延迟，丢失 cookie 状态 |
| 限流参数（间隔、重试次数）写死为常量 | 无法根据运行时反馈动态调整 |
| 没有"不使用代理"的降级路径 | Clash 不可用时整个 analysis 模块瘫痪 |
| 切换策略单一（顺序轮换） | 无法根据场景选择最优策略 |

### 1.2 设计目标

1. **代理热插拔**：运行时动态切换代理模式（代理/直连/禁用），不重启进程
2. **策略可插拔**：轮换、最优、随机等策略可运行时切换
3. **动态参数调节**：限流间隔、重试次数、并发数等参数可运行时调整
4. **降级容错**：代理不可用时自动降级为直连，恢复后自动切回
5. **跨包统一**：所有包共享同一套代理管理和参数调节机制

## 2. 整体架构

```
┌──────────────────────────────────────────────────────┐
│                    调度层 (main.py)                    │
│              使用 ProxyManager 获取请求配置              │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │ ProxyManager │  │ ParamManager │  │HealthCheck │  │
│  │  代理热插拔    │  │  动态参数     │  │  健康检测   │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬─────┘  │
│         │                 │                 │        │
│  ┌──────▼─────────────────▼─────────────────▼─────┐  │
│  │              RuntimeConfig (单例)                │  │
│  │    统一管理运行时状态 + 动态参数 + 事件通知          │  │
│  └──────────────────────┬────────────────────────┘  │
│                         │                            │
├─────────────────────────┼────────────────────────────┤
│                         ▼                            │
│  ┌──────────────────────────────────────────────┐    │
│  │         config.yaml (proxy 配置段)             │    │
│  │  + 运行时 override 文件 (proxy_override.yaml) │    │
│  └──────────────────────────────────────────────┘    │
│                                                      │
├──────────────────────────────────────────────────────┤
│                  网络层 (clash_proxy.py)               │
│       Clash REST API · 节点轮换 · 测速 · 切换          │
└──────────────────────────────────────────────────────┘
```

## 3. 核心模块设计

### 3.1 ProxyManager — 代理热插拔

#### 3.1.1 代理模式

```python
class ProxyMode(Enum):
    CLASH = "clash"          # 通过 Clash 代理（当前模式）
    DIRECT = "direct"        # 直连（不走代理）
    DISABLED = "disabled"    # 禁用网络请求（维护模式）
```

#### 3.1.2 切换策略

```python
class RotateStrategy(Enum):
    SEQUENTIAL = "sequential"  # 顺序轮换（当前模式）
    BEST = "best"              # 全量测速选最优
    RANDOM = "random"          # 随机选择
    STICKY = "sticky"          # 粘性：不主动切换，仅失败时换
```

#### 3.1.3 ProxyManager 接口

```python
class ProxyManager:
    """代理管理器 — 运行时可切换模式和策略"""

    def __init__(self, config: ProxyConfig):
        self._mode = config.mode              # 当前模式
        self._strategy = config.strategy      # 切换策略
        self._fallback_to_direct = config.fallback_to_direct  # 降级开关
        self._fail_count = 0                  # 连续失败计数
        self._fail_threshold = config.fail_threshold  # 降级阈值

    # ── 核心接口 ──

    def get_proxy_url(self) -> str | None:
        """获取当前代理 URL，直连模式返回 None"""

    def on_request_success(self):
        """请求成功回调：重置失败计数，如在降级状态则尝试恢复"""

    def on_request_fail(self):
        """请求失败回调：累加失败计数，达阈值自动切换/降级"""

    def rotate(self) -> str:
        """按当前策略切换代理，返回新节点名"""

    # ── 热插拔接口 ──

    def set_mode(self, mode: ProxyMode):
        """运行时切换代理模式"""

    def set_strategy(self, strategy: RotateStrategy):
        """运行时切换轮换策略"""

    @property
    def status(self) -> dict:
        """返回当前状态（模式/策略/节点/失败数/是否降级）"""
```

#### 3.1.4 降级与恢复流程

```
                    请求失败
                       │
                 fail_count += 1
                       │
            fail_count >= threshold?
                  │          │
                 YES         NO
                  │          │
                  ▼          ▼
          ┌─────────────┐  继续当前模式
          │ 自动降级判断  │
          └──────┬──────┘
                 │
          CLASH → DIRECT     # 代理不可用，降级直连
          DIRECT → raise     # 直连也失败，抛异常
                 │
                 ▼
          降级后每 N 次请求
          尝试探测代理可用性
                 │
              可用？
            │       │
           YES      NO
            │       │
            ▼       ▼
         自动恢复  保持降级
```

#### 3.1.5 config.yaml 新增配置段

```yaml
proxy:
  mode: clash                    # clash / direct / disabled
  strategy: sequential           # sequential / best / random / sticky
  fallback_to_direct: true       # 代理失败时自动降级为直连
  fail_threshold: 3              # 连续失败 N 次触发切换/降级
  recovery_interval: 50          # 降级后每 N 次请求探测一次恢复
  clash:
    api: "http://127.0.0.1:9090"
    proxy_url: "http://127.0.0.1:7890"
    selector_group: "🚀 节点选择"
    auto_select: "♻️ 自动选择"
    max_delay: 600               # 测速超过此值视为不可用
    delay_timeout: 3000          # 测速超时(ms)
    regions: ["日本", "美国", "新加坡"]
    vip_filter: "VIP1"
```

### 3.2 ParamManager — 动态参数调节

#### 3.2.1 可调参数清单

| 参数 | 当前值 | 所在模块 | 说明 |
|------|--------|----------|------|
| `search_delay_min` | 20s (news) / 5s (analysis) | news/analysis | 搜索间隔下限 |
| `search_delay_max` | 30s (news) / 10s (analysis) | news/analysis | 搜索间隔上限 |
| `max_retries` | 3 | news/analysis | 搜索最大重试 |
| `max_empty_retries` | 3 | analysis | 空结果最大轮换次数 |
| `max_llm_workers` | 3 (analysis) / 10 (guba) | analysis/guba | LLM 并发线程数 |
| `llm_retry` | 3 | analysis/guba | LLM 调用最大重试 |
| `guba_delay` | 3s | guba | 股吧采集间隔 |
| `heat_delay` | 1s | heat | 热度采集间隔 |
| `batch_size` | 500 | database | 批量写入大小 |

#### 3.2.2 调节机制

**方案：文件监听 + 合并覆盖**

```
config.yaml (基础配置，不变)
    +
proxy_override.yaml (运行时覆盖，可热修改)
    =
最终生效配置
```

```python
class ParamManager:
    """动态参数管理器 — 监听 override 文件变化，合并到运行时配置"""

    OVERRIDE_FILE = "proxy_override.yaml"
    POLL_INTERVAL = 10  # 每 10 秒检查一次文件变化

    def __init__(self, base_config: dict):
        self._base = base_config
        self._override = {}
        self._effective = {**base_config}
        self._file_mtime = 0
        self._callbacks: list[Callable] = []

    def get(self, key: str, default=None):
        """获取当前生效的参数值"""
        return self._effective.get(key, default)

    def register_callback(self, fn: Callable[[str, Any, Any], None]):
        """注册参数变化回调：fn(key, old_value, new_value)"""

    def check_update(self):
        """检查 override 文件是否变化，有变化则重新加载合并"""

    def _merge(self):
        """base + override 合并，override 优先"""
```

#### 3.2.3 override 文件示例

```yaml
# proxy_override.yaml
# 运行时修改此文件，10秒内自动生效，无需重启

proxy:
  mode: direct                   # 临时切换为直连
  fail_threshold: 5              # 放宽降级阈值

params:
  search_delay_min: 30           # 加大搜索间隔（被限流时）
  search_delay_max: 45
  max_llm_workers: 2             # 降低 LLM 并发（节省额度）
```

#### 3.2.4 自适应调节（可选增强）

除了手动 override，系统可基于运行时指标自动调节：

```
指标采集                    规则引擎                    参数调节
┌────────────┐         ┌──────────────┐         ┌──────────────┐
│ 搜索成功率  │────────▶│ 成功率 < 50%  │────────▶│ delay × 1.5  │
│ 搜索延迟    │         │ 延迟 > 10s    │         │ retries + 1  │
│ LLM 失败率  │         │ LLM失败>30%  │         │ workers - 1  │
│ 代理切换频率 │         │ 切换>5次/min │         │ mode=direct  │
└────────────┘         └──────────────┘         └──────────────┘
```

**建议首期不做自适应，先实现手动 override，积累运行数据后再加规则。**

### 3.3 HealthCheck — 健康检测

```python
class HealthCheck:
    """定期探测代理和目标站点可用性"""

    def check_clash_api(self) -> bool:
        """Clash API 是否可达"""

    def check_proxy_connectivity(self) -> bool:
        """通过代理访问测试 URL 是否成功"""

    def check_toutiao(self) -> bool:
        """今日头条搜索接口是否正常（非限流）"""

    def run_all(self) -> dict:
        """返回 {clash_api: bool, proxy: bool, toutiao: bool}"""
```

## 4. 改造范围

### 4.1 需要修改的文件

| 文件 | 改动内容 |
|------|----------|
| `config.py` | 新增 `ProxyConfig` dataclass，加载 proxy 配置段 |
| `clash_proxy.py` | 重构为 `ProxyManager` 类，保留原有节点管理逻辑 |
| `analysis/analyzer.py` | 从硬编码代理改为使用 `ProxyManager` |
| `news/fetcher.py` | 接入 `ProxyManager`（当前直连，改为可选代理） |
| `config.yaml` | 新增 proxy 配置段 |

### 4.2 不需要改动的文件

| 文件 | 原因 |
|------|------|
| `heat/fetcher.py` | 使用东财/AkShare API，不需要代理 |
| `guba/fetcher.py` | 使用股吧 API，不需要代理（东财国内 API） |
| `database.py` | 数据层，与网络无关 |
| `*/models.py` | 数据模型，与网络无关 |

### 4.3 新增文件

| 文件 | 说明 |
|------|------|
| `proxy_manager.py` | ProxyManager + ParamManager + HealthCheck |
| `proxy_override.yaml` | 运行时参数覆盖文件（.gitignore） |

## 5. 各包接入方案

### 5.1 analysis 包（主要改造）

**当前**：`analyzer.py` 硬编码 `CLASH_PROXY`，失败时直接调用 `rotate_proxy()`

**改造后**：

```python
# analyzer.py
from proxy_manager import get_proxy_manager

def _get_session() -> cffi_req.Session:
    pm = get_proxy_manager()
    proxy = pm.get_proxy_url()
    return cffi_req.Session(
        impersonate="chrome120",
        proxy=proxy,  # None 表示直连
    )

def _search(query, timeout=15):
    pm = get_proxy_manager()
    while True:
        try:
            results = _search_toutiao(query, timeout)
            if results:
                pm.on_request_success()
                return results
            # 空结果处理...
        except Exception:
            pm.on_request_fail()
            if pm.mode == ProxyMode.DISABLED:
                raise
        pm.rotate()
        _reset_session()
```

### 5.2 news 包（轻度接入）

**当前**：直连，无代理

**改造后**：通过 `ProxyManager` 获取代理配置，模式为 `direct` 时行为不变

```python
# news/fetcher.py
from proxy_manager import get_proxy_manager

def _get_session():
    pm = get_proxy_manager()
    proxy = pm.get_proxy_url()  # direct 模式返回 None
    return cffi_req.Session(impersonate="chrome120", proxy=proxy)
```

### 5.3 heat / guba 包（不改动）

这两个包访问国内 API（东财/股吧），不需要代理。保持现状。

## 6. 动态参数在调度层的使用

**当前**：延迟/重试等参数是模块级常量

```python
# 当前 analysis/analyzer.py
DELAY_MIN = 5
DELAY_MAX = 10
```

**改造后**：从 ParamManager 获取

```python
# 改造后
from proxy_manager import get_param_manager

def _search(query, timeout=15):
    pm = get_param_manager()
    delay_min = pm.get("search_delay_min", 5)
    delay_max = pm.get("search_delay_max", 10)
    # ...
    time.sleep(random.uniform(delay_min, delay_max))
```

**注意**：ParamManager 的 `get()` 方法返回的是当前生效值（base + override 合并后），override 文件变化时自动更新，无需重启。

## 7. 实施计划

### Phase 1：ProxyManager 核心（1-2天）

1. 新建 `proxy_manager.py`，实现 `ProxyManager` 类
2. 迁移 `clash_proxy.py` 的节点管理逻辑到 `ProxyManager`
3. `config.py` 新增 `ProxyConfig`
4. `analysis/analyzer.py` 接入 `ProxyManager`
5. 验证：analysis 模块功能不变，代理模式可切换

### Phase 2：降级容错（1天）

1. 实现降级（CLASH → DIRECT）和恢复逻辑
2. 实现 `HealthCheck`
3. 验证：关闭 Clash 后 analysis 自动降级为直连

### Phase 3：ParamManager 动态参数（1天）

1. 实现 `ParamManager`，支持 override 文件热加载
2. analysis/news/guba 调度层参数改为从 `ParamManager` 获取
3. 验证：修改 `proxy_override.yaml` 后参数实时生效

### Phase 4：news 包接入（0.5天）

1. `news/fetcher.py` 接入 `ProxyManager`
2. 验证：news 模块可选代理/直连

## 8. 兼容性保证

### 8.1 向后兼容

- 默认配置下行为与当前完全一致
- 不配置 `proxy` 段 = 使用当前硬编码的默认值
- `clash_proxy.py` 保留原有接口不删除，标记为 deprecated

### 8.2 不影响的模块

- `heat`：使用东财 API + AkShare，不走代理
- `guba`：使用股吧 API，不走代理
- `database`：数据层，无网络行为
- 所有 `models.py`：纯数据定义

### 8.3 配置迁移

已有 `config.yaml` 无需改动，新增 `proxy` 段为可选。未配置时使用默认值：

```yaml
proxy:
  mode: clash
  strategy: sequential
  fallback_to_direct: true
  fail_threshold: 3
```

## 9. 风险与注意事项

| 风险 | 应对 |
|------|------|
| ParamManager 文件监听性能 | 10s 轮询检查 mtime，开销可忽略 |
| override 文件格式错误 | 解析失败保持上次生效值，记录 warning 日志 |
| 多进程同时写 override | 仅支持单进程修改（运维手动编辑） |
| session 重建丢失 cookie | 头条搜索无登录态依赖，重建 session 不影响功能 |
| 降级为直连后被限流 | 直连 IP 固定，需配合加大间隔；首期建议直连仅作为短时容错 |

## 10. 不做的事情（首期）

- **不做**自适应参数调节（等积累运行数据后再做）
- **不做** Web 管理界面（通过 override 文件足够）
- **不做**多代理商支持（仅 Clash）
- **不做** heat/guba 包的代理接入（无需求）
- **不做**代理池管理（依赖 Clash 节点池）
