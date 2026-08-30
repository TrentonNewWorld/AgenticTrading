# vn.py 模拟行情数据接入实施计划

> 本计划按 loop engineering 执行：每个任务都是一个独立的“失败测试 -> 最小实现 ->
> 验证 -> 提交”闭环。必须按顺序完成，不在同一轮混入后续任务。

**目标：** 在现有 Dashboard 回测页面增加开发环境专用的 `vn.py Simulation` 数据源，
使用真实 vn.py `BarData` 生成确定性的 DJIA-30 小时 K 线，并在完全离线、无 LLM 的
条件下完成现有 Agent、Buy and Hold、DJIA 三条曲线的回测和展示。

**架构：** 回测引擎改为依赖统一 `MarketDataProvider`。Alpaca 保持默认实现；
`VnpySimulationProvider` 生成真实 `BarData`，再通过独立适配器转换成现有 OHLCV
DataFrame。真实 IB、`MainEngine` 和 `IbGateway` 不进入本轮。

**技术栈：** Python 3.10+、vn.py 4.4.0、pandas、FastAPI/Pydantic、pytest、原生
HTML/CSS/JavaScript。

**设计规格：**

- 中文：`docs/superpowers/specs/2026-07-14-vnpy-simulated-market-data-design.zh-CN.md`
- 英文：`docs/superpowers/specs/2026-07-14-vnpy-simulated-market-data-design.md`

## 全局约束

- 从仓库根目录运行命令。
- 不修改 DJIA-30、规则策略阈值或基准算法。
- `alpaca` 保持默认数据源，旧请求不传 `data_source` 时行为不变。
- 模拟模式禁止 Alpaca、IB、Yahoo 和 LLM 网络请求，测试使用 fail-on-call 替身。
- vn.py 必须延迟导入；未安装 vn.py 时 Alpaca 路径仍能导入和运行。
- 不在主 `requirements.txt` 中加入 vn.py，使用独立可选依赖文件。
- 不在真实数据和模拟数据之间静默回退。
- 所有新增路由同步更新 `test_app_composition.py` 的冻结路由集合。
- 每个任务完成后只提交该任务相关文件，保持提交可回滚。

---

## Task 1：建立统一行情数据接口和数据源工厂

**文件：**

- 新建：`dashboard/backend/infrastructure/market_data/provider.py`
- 新建：`dashboard/backend/tests/infrastructure/market_data/test_provider.py`

**对外接口：**

```python
ALPACA = "alpaca"
VNPY_SIMULATION = "vnpy_simulation"
SUPPORTED_DATA_SOURCES = (ALPACA, VNPY_SIMULATION)

class MarketDataProvider(Protocol):
    def fetch_bars(self, symbols, start, end) -> dict[str, pd.DataFrame]: ...

def vnpy_simulation_enabled() -> bool: ...
def validate_market_data_source(data_source: str) -> None: ...
def create_market_data_provider(data_source: str) -> MarketDataProvider: ...
```

同时定义可区分的异常：

```python
class UnsupportedMarketDataSource(ValueError): ...
class MarketDataSourceDisabled(RuntimeError): ...
class MarketDataDependencyError(RuntimeError): ...
```

- [ ] **Step 1：写失败测试**

覆盖：

- 不传或传 `alpaca` 时工厂返回 `AlpacaDataLoader`。
- 未知数据源抛出 `UnsupportedMarketDataSource`。
- `ENABLE_VNPY_SIMULATION` 只有规范化后的 `true/1/yes/on` 启用。
- 功能关闭时验证 `vnpy_simulation` 抛出 `MarketDataSourceDisabled`。
- 导入 `provider.py` 不会导入 `vnpy`。

- [ ] **Step 2：运行测试，确认失败**

```bash
pytest dashboard/backend/tests/infrastructure/market_data/test_provider.py -v
```

预期：FAIL，模块尚不存在。

- [ ] **Step 3：实现最小接口和 Alpaca 默认分支**

`vnpy_simulation` 分支使用函数内延迟导入，Task 3 创建具体提供器前可以通过测试替身
验证工厂分支，不提前实现模拟逻辑。

- [ ] **Step 4：运行测试**

```bash
pytest dashboard/backend/tests/infrastructure/market_data/test_provider.py -v
```

预期：PASS。

- [ ] **Step 5：提交**

```bash
git add dashboard/backend/infrastructure/market_data/provider.py \
  dashboard/backend/tests/infrastructure/market_data/test_provider.py
git commit -m "refactor(market-data): add provider boundary"
```

---

## Task 2：实现 vn.py BarData 转换与校验

**文件：**

- 新建：`requirements-vnpy.txt`
- 新建：`dashboard/backend/infrastructure/market_data/vnpy_adapter.py`
- 新建：`dashboard/backend/tests/infrastructure/market_data/test_vnpy_adapter.py`

**接口：**

```python
def bars_to_frame(bars: Sequence[BarData]) -> pd.DataFrame: ...
```

- [ ] **Step 1：增加可选依赖并安装**

`requirements-vnpy.txt` 固定 vn.py，并重复主依赖中的 NumPy 版本，防止解析器将 NumPy
升级到现有 Numba/SciPy 不支持的版本：

```text
numpy==2.2.6
vnpy==4.4.0
```

运行：

```bash
pip install -r requirements-vnpy.txt
```

- [ ] **Step 2：使用真实 BarData 写失败测试**

用 `Exchange.SMART`、`Interval.HOUR`、`gateway_name="VNPY_SIM"` 构造真实
`BarData`，覆盖：

- 字段逐项映射为 `open/high/low/close/volume`。
- 索引名为 `timestamp`，带 `US/Eastern` 时区并升序排列。
- 拒绝无时区时间、重复时间、非有限价格、非正价格、错误 high/low、负成交量。
- 空列表返回具有固定列和空 `DatetimeIndex` 的 DataFrame。

- [ ] **Step 3：运行测试，确认失败**

```bash
pytest dashboard/backend/tests/infrastructure/market_data/test_vnpy_adapter.py -v
```

- [ ] **Step 4：实现显式转换，不做字符串拼接式解析**

转换后统一调用一个内部校验函数。错误信息必须包含 symbol、timestamp 和违反的约束。

- [ ] **Step 5：运行测试并提交**

```bash
pytest dashboard/backend/tests/infrastructure/market_data/test_vnpy_adapter.py -v
git add requirements-vnpy.txt \
  dashboard/backend/infrastructure/market_data/vnpy_adapter.py \
  dashboard/backend/tests/infrastructure/market_data/test_vnpy_adapter.py
git commit -m "feat(market-data): adapt vnpy bars to OHLCV frames"
```

---

## Task 3：实现确定性的 DJIA-30 vn.py 模拟数据提供器

**文件：**

- 新建：`dashboard/backend/infrastructure/market_data/vnpy_simulation.py`
- 新建：`dashboard/backend/tests/infrastructure/market_data/test_vnpy_simulation.py`
- 修改：`dashboard/backend/infrastructure/market_data/provider.py`
- 修改：`dashboard/backend/tests/infrastructure/market_data/test_provider.py`

**接口：**

```python
class VnpySimulationProvider:
    def fetch_bars(self, symbols, start, end) -> dict[str, pd.DataFrame]: ...
```

- [ ] **Step 1：写生成器失败测试**

覆盖：

- 为传入的每个 DJIA-30 symbol 返回数据。
- `start` 包含，`end` 不包含。
- 只生成周一至周五以及当前回测接受的美东交易时间点。
- 同一输入两次结果完全相同。
- 使用子进程再次生成，结果摘要仍一致，证明没有使用 Python `hash()`。
- 所有行通过 Task 2 的不变量校验。
- `2026-04-01` 到 `2026-04-23` 有足够数据计算 SMA50。

- [ ] **Step 2：运行测试，确认失败**

```bash
pytest dashboard/backend/tests/infrastructure/market_data/test_vnpy_simulation.py -v
```

- [ ] **Step 3：实现稳定价格路径**

每只股票使用 `sha256(symbol + start + end)` 派生基准价格、成交量和微小扰动。全局收益
路径依次包含：

1. 至少 20 根平稳预热 K 线；
2. 足以触发 `RSI < 30` 且 `close < SMA20` 的受控下跌；
3. 足以触发卖出条件的恢复上涨；
4. 收尾横盘。

每根 K 线使用前一根 close 作为 open，再根据 return 计算 close；high/low 在两者外侧
增加稳定价差，volume 始终非负。生成真实 `BarData` 后统一交给 Task 2 适配器。

- [ ] **Step 4：接入工厂并测试缺少依赖错误**

`create_market_data_provider("vnpy_simulation")` 只有在功能开关开启后才延迟导入。
`ModuleNotFoundError` 转成 `MarketDataDependencyError`，错误包含
`pip install -r requirements-vnpy.txt`。

- [ ] **Step 5：运行测试并提交**

```bash
pytest dashboard/backend/tests/infrastructure/market_data/test_provider.py \
  dashboard/backend/tests/infrastructure/market_data/test_vnpy_simulation.py -v
git add dashboard/backend/infrastructure/market_data/provider.py \
  dashboard/backend/infrastructure/market_data/vnpy_simulation.py \
  dashboard/backend/tests/infrastructure/market_data/test_provider.py \
  dashboard/backend/tests/infrastructure/market_data/test_vnpy_simulation.py
git commit -m "feat(market-data): generate deterministic vnpy simulation bars"
```

---

## Task 4：解除基准计算对 Alpaca 凭据的无条件依赖

**文件：**

- 修改：`dashboard/backend/baseline_generator.py`
- 修改：`dashboard/backend/tests/test_market_data_errors.py`
- 新建：`dashboard/backend/tests/test_baseline_generator_offline.py`

- [ ] **Step 1：写失败测试**

覆盖：

- 无任何 Alpaca 环境变量或凭据文件时，`BaselineGenerator()` 可以创建。
- 向 `generate_buyhold_baseline` 和 `generate_index_baseline` 传入 DataFrame 时正常计算，
  且不会创建 Alpaca client。
- 真正调用 `_fetch_bars_for_symbol` 时仍会延迟读取凭据；缺少凭据继续抛出
  `MarketDataUnavailableError`，不能退化为 `SystemExit`。

- [ ] **Step 2：运行测试，确认现有构造函数失败**

```bash
pytest dashboard/backend/tests/test_baseline_generator_offline.py \
  dashboard/backend/tests/test_market_data_errors.py -v
```

- [ ] **Step 3：最小重构**

从 `__init__` 移除 `_load_credentials()`。新增幂等 `_ensure_credentials()`，只在真实
Alpaca fetch 路径调用。基准算法本身不修改。

- [ ] **Step 4：运行相关测试并提交**

```bash
pytest dashboard/backend/tests/test_baseline_generator_offline.py \
  dashboard/backend/tests/test_market_data_errors.py \
  dashboard/backend/tests/backtesting/test_engine_move.py -v
git add dashboard/backend/baseline_generator.py \
  dashboard/backend/tests/test_market_data_errors.py \
  dashboard/backend/tests/test_baseline_generator_offline.py
git commit -m "fix(baselines): calculate from supplied bars offline"
```

---

## Task 5：将数据源贯穿回测引擎和 CLI

**文件：**

- 修改：`dashboard/backend/domain/backtesting/engine.py`
- 修改：`dashboard/scripts/backtest_hourly_agent.py`
- 修改：`dashboard/backend/tests/backtesting/test_engine_move.py`
- 新建：`dashboard/backend/tests/backtesting/test_vnpy_simulation_engine.py`

- [ ] **Step 1：写失败测试**

覆盖：

- `HourlyBacktester(..., data_source="alpaca")` 保持默认行为。
- 选择 `vnpy_simulation` 时通过工厂创建提供器并强制 `use_llm=False`。
- `load_data()` 使用统一 provider，不再直接构造 `AlpacaDataLoader`。
- Agent、Buy and Hold、DJIA 三类 `insert_run` 调用的 metadata 都包含实际
  `data_source`。
- 标准日期运行后 Agent 曲线、两条基准曲线非空，并且至少有一条 trade。
- fail-on-call 的 LLM、Alpaca、Yahoo 替身没有被调用。

- [ ] **Step 2：运行测试，确认失败**

```bash
pytest dashboard/backend/tests/backtesting/test_vnpy_simulation_engine.py -v
```

- [ ] **Step 3：改造引擎**

- 构造函数新增 `data_source: str = "alpaca"`。
- 使用 `create_market_data_provider(data_source)`。
- 模拟模式无条件覆盖 `use_llm=False`。
- 将 `_llm_run_metadata()` 改为合并式运行 metadata，保留原有 LLM 配置并加入
  `data_source`。
- 两个基准运行也写入相同来源 metadata。
- 错误文本从 “No data returned from Alpaca” 改为包含实际 provider 的通用信息。

- [ ] **Step 4：改造 CLI**

新增：

```text
--data-source {alpaca,vnpy_simulation}
```

将该值传给 `HourlyBacktester`，日志打印实际来源。模拟模式即使收到 `--use-llm` 也由
引擎强制关闭。

- [ ] **Step 5：更新原有测试补丁点**

原来 monkeypatch `engine_mod.AlpacaDataLoader` 的构造测试改为 patch provider 工厂；
保留旧脚本 re-export 和 custom-algo 子类兼容性断言。

- [ ] **Step 6：运行测试并提交**

```bash
pytest dashboard/backend/tests/backtesting/test_engine_move.py \
  dashboard/backend/tests/backtesting/test_vnpy_simulation_engine.py -v
git add dashboard/backend/domain/backtesting/engine.py \
  dashboard/scripts/backtest_hourly_agent.py \
  dashboard/backend/tests/backtesting/test_engine_move.py \
  dashboard/backend/tests/backtesting/test_vnpy_simulation_engine.py
git commit -m "feat(backtest): run against selected market data provider"
```

---

## Task 6：扩展回测 API、后台子进程和功能配置接口

**文件：**

- 修改：`dashboard/backend/api/routers/backtests.py`
- 修改：`dashboard/backend/api/routers/config.py`
- 修改：`dashboard/backend/tests/test_backtests_router.py`
- 修改：`dashboard/backend/tests/test_app_composition.py`
- 新建：`dashboard/backend/tests/test_market_data_features.py`

- [ ] **Step 1：写失败测试**

覆盖：

- `GET /config/features` 返回且只返回 `vnpy_simulation_enabled: bool`。
- 未开启开关时请求模拟数据返回 HTTP 403，且没有创建后台线程。
- 缺少 vn.py 时返回 HTTP 503 和安装指令。
- 未知数据源通过请求模型返回 HTTP 422。
- 不传数据源时仍启动 Alpaca，保持兼容。
- 模拟模式传给后台 runner，并生成包含 `--data-source vnpy_simulation --no-llm` 的命令。
- Alpaca 命令继续包含 `--use-llm`，除本次数据源改动外保持原样。
- 新路由同步加入 `EXPECTED_CONFIG_ROUTES` 和 `EXPECTED_FULL_CONTRACT`。

- [ ] **Step 2：运行测试，确认失败**

```bash
pytest dashboard/backend/tests/test_market_data_features.py \
  dashboard/backend/tests/test_backtests_router.py \
  dashboard/backend/tests/test_app_composition.py -v
```

- [ ] **Step 3：扩展请求模型和校验顺序**

`BacktestRunRequest` 增加 `data_source`。body 覆盖 query，默认 `alpaca`。校验顺序：

1. Pydantic/日期/现有费用保护校验；
2. 数据源功能开关和依赖预检；
3. rate limit；
4. 创建后台线程。

禁用功能映射 HTTP 403，缺少依赖映射 HTTP 503。不能通过创建 provider 来预检
Alpaca，以免改变现有“后台任务内加载凭据”的行为。

- [ ] **Step 4：贯穿后台 runner**

`run_backtest_background` 新增 `data_source` 参数并记录在 `backtest_status`。子进程命令
始终加入 `--data-source`；模拟模式加入 `--no-llm`，其他模式保持 `--use-llm`。
`finally` 继续清理临时文件和全局运行状态。

- [ ] **Step 5：新增功能接口并提交**

```bash
pytest dashboard/backend/tests/test_market_data_features.py \
  dashboard/backend/tests/test_backtests_router.py \
  dashboard/backend/tests/test_app_composition.py -v
git add dashboard/backend/api/routers/backtests.py \
  dashboard/backend/api/routers/config.py \
  dashboard/backend/tests/test_backtests_router.py \
  dashboard/backend/tests/test_app_composition.py \
  dashboard/backend/tests/test_market_data_features.py
git commit -m "feat(api): select guarded backtest data source"
```

---

## Task 7：在运行查询中暴露并保持数据来源

**文件：**

- 修改：`dashboard/backend/api/routers/backtests.py`
- 修改：`dashboard/backend/tests/test_backtests_router.py`
- 修改：`dashboard/backend/tests/test_agent_runs_metadata.py`

- [ ] **Step 1：写失败测试**

覆盖 `/api/backtest/runs`、`/runs`、`/runs/{run_id}` 和 `/runs/latest/metrics`：

- `metadata.data_source=vnpy_simulation` 映射到顶层 `data_source`。
- 历史记录缺少 metadata 时映射为 `alpaca`。
- baseline run 的来源与 Agent run 一致。

- [ ] **Step 2：运行测试，确认失败**

```bash
pytest dashboard/backend/tests/test_backtests_router.py \
  dashboard/backend/tests/test_agent_runs_metadata.py -v
```

- [ ] **Step 3：增加统一的响应转换函数**

`RunMetadata` 增加 `data_source: str = "alpaca"`。所有 `RunMetadata(**run)` 调用先经过
同一 helper，从解析后的 `run["metadata"]` 提取来源。禁止在多个 endpoint 各写一份
回退逻辑。

- [ ] **Step 4：运行测试并提交**

```bash
pytest dashboard/backend/tests/test_backtests_router.py \
  dashboard/backend/tests/test_agent_runs_metadata.py -v
git add dashboard/backend/api/routers/backtests.py \
  dashboard/backend/tests/test_backtests_router.py \
  dashboard/backend/tests/test_agent_runs_metadata.py
git commit -m "feat(backtest): expose run data provenance"
```

---

## Task 8：在 Dashboard 增加数据源控件和模拟数据标识

**文件：**

- 修改：`dashboard/frontend/app.html`
- 修改：`dashboard/frontend/app.js`
- 修改：`dashboard/frontend/styles.css`
- 新建：`dashboard/backend/tests/integrations/test_vnpy_simulation_frontend.py`

- [ ] **Step 1：写前端源码契约测试**

当前前端没有 JS 测试框架，沿用 `tests/integrations/test_frontend_no_mock_data.py` 的
源码级测试模式，覆盖：

- HTML 存在 `marketDataSourceSelect`、`vnpySimulationNotice`、
  `backtestDataSourceBadge`。
- `app.js` 请求 `/config/features`。
- 模拟 option 只在后端功能状态为 true 时加入。
- `runBacktest()` 将 `data_source` 写入 body。
- 模拟模式禁用 `modelSelect` 并显示 `Rule-based (offline)`。
- 加载历史 run 时根据 `run.data_source` 更新结果 badge，而不是只依赖当前选择器状态。

- [ ] **Step 2：运行测试，确认失败**

```bash
pytest dashboard/backend/tests/integrations/test_vnpy_simulation_frontend.py -v
```

- [ ] **Step 3：实现控件**

在 Backtest Setup 的 Agent 与 Model 控件附近增加数据源 select。Alpaca 永远存在且默认
选中；只有 `/config/features` 返回 true 才加入 `vn.py Simulation`。

选择模拟模式时：

- 显示 “Synthetic vn.py data for integration testing; not real market data.”；
- 禁用模型 select，并显示 `Rule-based (offline)`；
- 不清除用户此前选择的模型，切回 Alpaca 后恢复；
- 提交 `data_source=vnpy_simulation`。

结果区域增加紧凑 badge，通过 `window.SELECTED_RUN.data_source` 更新，确保刷新页面后
模拟来源仍然可见。运行选择器标签可追加 `Simulated`，但不改变原有日期和收益格式。

- [ ] **Step 4：补充克制的样式**

沿用现有 `control-group`、`control-select` 和 section header 视觉系统。提示使用清晰的
中性警告色，不创建新的卡片或嵌套卡片；移动端必须换行，不得挤压 Run Backtest 按钮。

- [ ] **Step 5：运行测试并提交**

```bash
pytest dashboard/backend/tests/integrations/test_vnpy_simulation_frontend.py \
  dashboard/backend/tests/test_djia30_universe.py \
  dashboard/backend/tests/test_app_composition.py -v
git add dashboard/frontend/app.html dashboard/frontend/app.js \
  dashboard/frontend/styles.css \
  dashboard/backend/tests/integrations/test_vnpy_simulation_frontend.py
git commit -m "feat(frontend): select and label vnpy simulation data"
```

---

## Task 9：端到端离线验收、文档和全量回归

**文件：**

- 新建：`docs/integrations/vnpy-simulation.md`
- 修改：`docs/README.md`

- [ ] **Step 1：增加离线端到端测试**

将真实 Alpaca、IB、Yahoo、HTTP 和 LLM client 替换为调用即失败的替身。使用
`2026-04-01` 到 `2026-04-23` 运行完整模拟流程，断言：

- 30 个股票都有数据；
- Agent、Buy and Hold、DJIA 曲线非空；
- 至少一笔交易；
- 三类运行均记录 `vnpy_simulation`；
- LLM calls 和估算费用均为 0；
- 无外部客户端被调用。

- [ ] **Step 2：写使用文档**

文档只描述 Loop 1：

```bash
pip install -r requirements.txt
pip install -r requirements-vnpy.txt
ENABLE_VNPY_SIMULATION=true python -m uvicorn dashboard.backend.app:app --reload
```

说明模拟数据用途、标准日期、无真实收益含义、如何关闭功能，以及 Loop 2 才会连接
IB/TWS。

- [ ] **Step 3：运行聚焦测试**

```bash
pytest dashboard/backend/tests/infrastructure/market_data/ \
  dashboard/backend/tests/backtesting/ \
  dashboard/backend/tests/test_baseline_generator_offline.py \
  dashboard/backend/tests/test_market_data_features.py \
  dashboard/backend/tests/test_backtests_router.py \
  dashboard/backend/tests/integrations/test_vnpy_simulation_frontend.py -v
```

- [ ] **Step 4：运行完整后端测试**

```bash
pytest dashboard/backend/tests/ -v
```

预期：全部 PASS，不允许以跳过现有测试换取通过。

- [ ] **Step 5：启动本地服务并进行浏览器验收**

```bash
ENABLE_VNPY_SIMULATION=true \
python -m uvicorn dashboard.backend.app:app --host 127.0.0.1 --port 8000
```

使用浏览器分别在桌面和移动视口验证：

- 数据源控件、提示和模型禁用状态不重叠；
- 点击 Run Backtest 后状态正常推进；
- 曲线、指标、交易记录和模拟来源 badge 正常显示；
- 刷新后来源 badge 仍然存在；
- 关闭功能开关后模拟 option 消失，Alpaca 仍是默认选项。

将必要的桌面和移动端截图保存到 `/tmp/newworldtrading-vnpy-qa/` 供本地审阅，不提交到
版本库；同时检查浏览器控制台无新增错误。

- [ ] **Step 6：提交文档和最终修正**

```bash
git add docs/integrations/vnpy-simulation.md docs/README.md
git commit -m "docs: explain offline vnpy simulation workflow"
```

- [ ] **Step 7：最终检查**

```bash
git status --short
git log --oneline -10
```

预期：工作区干净；实现由上述小提交组成，没有临时文件、凭据或生成数据进入版本库。

## 完成定义

只有同时满足以下条件，本轮才算完成：

1. 功能开关控制模拟 option 和后端访问。
2. 模拟器使用真实 vn.py `BarData`，不是字段相似的本地替身。
3. 标准日期的 DJIA-30 数据稳定可复现。
4. 页面能够离线完成 Agent 和两条基准回测并显示至少一笔交易。
5. 所有结果明确标记 `vnpy_simulation`。
6. 任何模拟错误都不会回退到 Alpaca。
7. Alpaca 默认路径和旧 API 请求保持兼容。
8. 完整测试集通过，桌面和移动端人工验收通过。
