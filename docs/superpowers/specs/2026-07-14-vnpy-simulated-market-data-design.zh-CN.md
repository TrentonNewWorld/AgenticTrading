# vn.py 模拟行情数据接入设计规格

- **日期：** 2026-07-14
- **状态：** 设计已批准，等待书面规格审阅
- **项目：** NewWorldTrading
- **迭代：** vn.py 行情数据接入第 1 轮

## 1. 背景

NewWorldTrading 当前通过 `AlpacaDataLoader` 直接加载美股小时 K 线。主要回测路径与
DJIA-30 股票池、美东交易时间以及 Alpaca 凭据绑定。项目计划使用 vn.py 接入行情，
并在后续通过 Interactive Brokers（IB，盈透证券）接入交易。

目前还没有 IB 模拟账户，也没有安装 TWS 或 IB Gateway。因此，第 1 轮先离线验证
vn.py 的数据契约，再尝试连接真实券商接口。

## 2. 目标

在现有 Dashboard 回测流程中新增一个仅供开发环境使用的 `vn.py Simulation` 数据源。
用户选择该数据源后，可以运行一次确定性的 DJIA-30 回测，并继续查看现有的图表、
指标、基准曲线和交易记录。整个过程不需要 IB、Alpaca 或大模型凭据，也不访问网络。

本轮验证以下数据链路：

```text
vn.py BarData -> 标准 OHLCV DataFrame -> 现有 NewWorldTrading 回测
```

本轮不能证明 IB 账户能够成功连接，也不能证明策略在真实市场中能够盈利。

## 3. 不在本轮范围内

- 连接 TWS、IB Gateway 或 `vnpy_ib` Gateway。
- 订阅实时 Tick 行情。
- 从 IB 查询真实历史行情。
- 发送、撤销或同步真实订单、模拟订单。
- 建设长期运行的 vn.py 独立服务。
- 将股票池扩展到现有 DJIA-30 以外。
- 在模拟数据模式中调用大模型。
- 替换或删除 Alpaca。
- 将模拟数据的收益表现当作真实投资效果。

## 4. 已确认的设计决策

| 决策项 | 选择 | 原因 |
|---|---|---|
| 首期市场 | 美股 | 与当前 NewWorldTrading 产品及未来 `vnpy_ib` 路径一致。 |
| 首个数据模式 | 历史小时 K 线 | 与当前回测输入一致，端到端路径最短。 |
| 凭据 | 第 1 轮不需要 | 目前没有 IB 访问条件。 |
| 交付入口 | 现有 Dashboard 回测页面 | 能得到用户可见的端到端结果。 |
| 数据源选择 | 页面明确选择 | 清楚展示数据来源，也为未来 IB 数据预留入口。 |
| 模拟功能可用范围 | 仅通过开发功能开关启用 | 防止公开用户把模拟数据误认为真实数据。 |
| 决策方式 | 现有规则策略 | 保持离线运行，不产生模型费用。 |
| 股票池 | DJIA-30 | 避免同时进行无关的股票池重构。 |
| 架构 | 先建立数据提供器边界，后续再拆分独立 vn.py 进程 | 保持第 1 轮足够小，同时避免回测逻辑依赖 vn.py 内部实现。 |
| 基准曲线 | 保留 Buy and Hold 和 DJIA 对比 | 模拟模式下 Dashboard 也应保持功能完整。 |

## 5. 备选方案

### 5.1 将 vn.py 逻辑直接写入回测引擎

这是首轮代码最少的方案，但会把数据生成、数据转换和策略执行耦合在一起。真实 IB
接入时还需要重新拆分相同逻辑。由于会制造明确的返工，因此不采用。

### 5.2 立即建立独立 vn.py 服务

该方案具备良好的进程隔离，也更接近未来生产架构。但在尚未连接券商之前，就需要
增加进程管理、通信协议、健康检查和部署工作。对第 1 轮而言过早，因此不采用。

### 5.3 先建立数据提供器边界，后续再拆分真实运行时

第 1 轮将模拟数据提供器放在后端内部，但通过稳定的行情数据接口与回测隔离。第 2 轮
可以通过独立 vn.py Worker 实现 IB 数据提供器，而无需修改消费端。采用此方案。

## 6. 架构

```text
Dashboard
  |
  | POST /backtest/run {data_source: "vnpy_simulation"}
  v
回测 API
  |
  v
MarketDataProvider 工厂
  |-- alpaca          -> 现有 AlpacaDataLoader
  `-- vnpy_simulation -> VnpySimulationProvider
                              |
                              | 创建真实的 vn.py BarData
                              v
                        VnpyBarAdapter
                              |
                              | Dict[str, pd.DataFrame]
                              v
现有技术指标、规则决策、投资组合、基准计算、数据库持久化和 Dashboard 图表
```

回测领域层只消费标准化后的数据提供器输出。它不导入 `IbGateway`，不理解券商凭据，
也不根据 vn.py 字段进行分支判断。

### 6.1 数据提供器契约

数据提供器边界保持当前加载器的接口形状：

```python
class MarketDataProvider(Protocol):
    def fetch_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
    ) -> dict[str, pd.DataFrame]: ...
```

返回的 DataFrame 使用带时区的 `DatetimeIndex`，索引名为 `timestamp`，并且必须包含
以下列：

```text
open, high, low, close, volume
```

`AlpacaDataLoader` 仍然是默认数据提供器。现有请求如果没有传入 `data_source`，行为
保持不变。

### 6.2 vn.py 依赖边界

模拟器使用真实的 `vnpy.trader.object.BarData` 类型，并设置：

- `exchange=Exchange.SMART`
- `interval=Interval.HOUR`
- `gateway_name="VNPY_SIM"`

vn.py 是仅供开发使用的可选依赖。独立的 `requirements-vnpy.txt` 固定
`vnpy==4.4.0`，并重复项目已有的 `numpy==2.2.6` 约束，防止 vn.py 的宽松 NumPy
范围将运行环境升级到现有 Numba/SciPy 不支持的版本。只有选择 `vnpy_simulation` 后
才延迟导入。生产环境的 Alpaca 路径必须在没有安装 vn.py 时也能正常导入和运行。
第 1 轮不安装 `vnpy_ib`。

第 1 轮不创建 `EventEngine`、`MainEngine` 或 `IbGateway`。

## 7. 用户体验

### 7.1 功能开关

仅在设置以下环境变量时启用该选项：

```text
ENABLE_VNPY_SIMULATION=true
```

关闭时，数据源选择器不显示 `vn.py Simulation`。即使直接向后端提交该数据源，后端
也必须拒绝。

前端通过以下不包含秘密信息的新接口读取功能状态：

```text
GET /config/features
-> {"vnpy_simulation_enabled": true | false}
```

浏览器不能根据本地配置自行推断该功能是否可用。

### 7.2 回测设置

现有 Backtest Setup 面板新增 `Market Data Source` 选择器：

```text
Alpaca
vn.py Simulation
```

默认选择 Alpaca。选择模拟模式后，页面持续显示：
`Synthetic vn.py data for integration testing; not real market data.`

同时禁用模型选择器，并显示 `Rule-based (offline)`，避免页面让用户误以为正在使用
所选的付费模型。

请求发送统一的数据源值：

```json
{"data_source": "vnpy_simulation"}
```

API 只允许 `alpaca` 和 `vnpy_simulation` 两个值。

### 7.3 结果来源标记

所选数据源保存在 Agent 运行记录及两个基准运行记录现有的 `metadata` JSON 中，键名为
`data_source`，不新增数据库列。Dashboard 获取的运行元数据增加可选的
`data_source` 字段。历史记录没有该字段时，为保持兼容，按 `alpaca` 解释。

模拟数据来源标记在运行期间和重新加载已保存结果后都必须保持可见。

## 8. 模拟数据

### 8.1 数据覆盖

数据提供器为当前 DJIA-30 常量中的每只股票生成指定时间范围内的小时 K 线。只在
工作日生成数据，并且时间点必须能够通过当前美东交易时间过滤器。

数据提供器固定沿用当前 Alpaca 加载器的日期边界：`start` 包含，`end` 不包含。
因此切换数据源不会改变页面开始、结束日期的含义。

### 8.2 确定性

价格路径通过股票代码、请求日期等规范化输入计算稳定的 SHA-256 种子。不得使用会随
Python 进程变化的 `hash()`。

对于相同股票和日期范围，不同进程及不同测试运行生成的标准化 OHLCV 数值必须完全
一致。

### 8.3 市场阶段

模拟价格路径包含受控的下跌、恢复上涨和横盘阶段。Dashboard 标准演示日期为
`2026-04-01` 到 `2026-04-23`，其中 `end` 不包含。该时间范围必须提供足够的数据来
计算 RSI、SMA20 和 SMA50，并且至少产生一笔规则策略交易。

非常短但仍合法的日期范围可以没有交易，系统不得绕过策略规则强行制造交易。

模拟数据只验证系统接入，不验证投资表现。

### 8.4 K 线约束

每条生成和转换后的数据都必须满足：

- `open`、`high`、`low`、`close` 是有限数且大于零。
- `high >= max(open, close)`。
- `low <= min(open, close)`。
- `volume` 是有限数且不能为负。
- 时间戳带有 `US/Eastern` 时区。
- 标准化后的时间戳严格递增。
- 发现重复时间戳时直接拒绝。

## 9. vn.py 数据转换

适配器执行以下明确映射：

| vn.py `BarData` | NewWorldTrading DataFrame |
|---|---|
| `datetime` | `timestamp` 索引 |
| `open_price` | `open` |
| `high_price` | `high` |
| `low_price` | `low` |
| `close_price` | `close` |
| `volume` | `volume` |

适配器只负责转换和校验。生成逻辑和数据源选择与适配器分离，使转换测试可以使用手工
构造的 `BarData` 测试数据。

## 10. 回测执行

`data_source` 依次通过现有 API、后台运行器、CLI 子进程和 `HourlyBacktester` 构造函数
传递。数据提供器在回测进程中创建。

当 `data_source=vnpy_simulation` 时：

- 无论页面选择什么模型，都强制设置 `use_llm=false`。
- 子进程不能收到 `--use-llm`。
- 不允许请求 Alpaca、IB、Yahoo 或托管模型。
- 复用现有技术指标和规则投资组合决策。
- 正常保存和展示 Agent、Buy and Hold、DJIA 三条曲线。

### 10.1 离线基准计算修正

当前 `BaselineGenerator` 即使已经收到调用者提供的全部 K 线，也会在构造函数中加载
Alpaca 凭据。这个耦合会破坏完全离线运行。

第 1 轮将凭据加载调整为延迟执行：根据已传入的 `bars_by_symbol` 计算基准时，不创建
Alpaca 客户端，也不读取凭据。只有真正从 Alpaca 拉取数据的方法才需要 Alpaca 凭据。
这是定向修正，不改变基准计算算法。

## 11. 异常处理

| 情况 | 必须执行的行为 |
|---|---|
| 未知的 `data_source` | 启动任务前返回 HTTP 422。 |
| 功能开关关闭时请求模拟数据 | 启动任务前返回 HTTP 403。 |
| 缺少 vn.py 可选依赖 | 返回 HTTP 503 和安装说明，不启动任务。 |
| 日期无效或顺序颠倒 | 保持当前请求校验行为。 |
| 没有有效交易时间点 | 明确提示没有交易时间并失败。 |
| 生成或转换的 K 线无效 | 报告股票、时间和违反的规则后失败。 |
| 模拟数据提供器失败 | 将运行标记为失败，绝不能回退到 Alpaca。 |
| Alpaca 数据提供器失败 | 保持当前错误，绝不能回退到模拟数据。 |
| 后台任务异常 | 展示错误，并始终清理全局运行状态。 |

数据来源必须明确。禁止在真实数据与模拟数据之间静默回退。

## 12. 测试

### 12.1 单元测试

- 使用真实 vn.py `BarData` 测试数据逐字段验证转换。
- 拒绝非有限或非正价格、错误的高低价、负成交量、无时区时间和重复时间戳。
- 验证输出按时间排序并带有时区。
- 验证不同实例重复生成的数据保持一致。
- 验证 DJIA-30 全部有数据，周末不生成数据。
- 验证标准演示日期能够生成可计算技术指标的数据。
- 验证数据提供器工厂默认行为和允许值。
- 验证功能开关及缺少依赖时的错误。
- 验证使用已提供 K 线计算基准时不会读取 Alpaca 凭据，也不会创建 Alpaca 客户端。

### 12.2 API 和运行器测试

- 不传 `data_source` 时选择 Alpaca。
- `/config/features` 只暴露布尔类型的功能状态。
- `vnpy_simulation` 正确通过请求、后台运行器、CLI 和回测引擎传递。
- 即使请求指定了大模型，模拟模式也强制使用规则策略。
- 模拟运行保存 `metadata.data_source`，并在运行元数据中返回。
- 运行失败后清理运行状态。
- 不支持或未启用的数据源在创建线程前失败。

### 12.3 集成测试

- 使用遇到网络调用就失败的替身运行标准模拟日期。
- 确认没有调用 Alpaca、IB、Yahoo 或 LLM 客户端。
- 确认运行完成，并生成非空的 Agent 收益曲线。
- 确认 Buy and Hold 和 DJIA 曲线非空。
- 确认标准演示至少生成一笔交易记录。
- 确认所有保存的运行记录都有正确的数据来源标记。

### 12.4 前端测试和人工检查

- 功能开关关闭时隐藏模拟数据选项。
- 功能开启时发送统一的数据源值。
- 模拟模式禁用模型选择器并将运行标记为规则策略。
- 模拟数据提示在运行期间和运行结束后都保持可见。
- 现有进度、图表、指标、运行历史和交易记录功能正常。
- Alpaca 保持默认选项，请求格式保持向后兼容。

## 13. 验收流程

```text
1. 安装 vn.py 开发环境可选依赖。
2. 设置 ENABLE_VNPY_SIMULATION=true。
3. 启动 NewWorldTrading。
4. 打开 Dashboard -> Playground -> Backtest。
5. 选择 vn.py Simulation。
6. 使用标准 DJIA-30 演示日期（2026-04-01 到 2026-04-23）运行回测。
7. 等待回测完成。
8. 确认 Agent、Buy and Hold 和 DJIA 曲线正常显示。
9. 确认指标和至少一笔交易正常显示。
10. 确认页面将运行标记为 vn.py 模拟数据。
11. 确认全程不需要外部凭据，也没有网络请求。
12. 关闭功能开关，确认模拟选项消失，同时 Alpaca 功能保持不变。
```

## 14. 第 2 轮方向

获得 IB 模拟账户，并且 TWS 或 IB Gateway 可用后，建立单独的 vn.py Worker，由它管理
`EventEngine`、`MainEngine` 和 `IbGateway`。Worker 订阅或查询 IB 数据，并对外提供相同
的标准化数据接口。

第 2 轮只替换模拟数据生产端，不重写 Dashboard、标准 DataFrame 契约、技术指标或
回测消费端。实时 Tick 和交易订单属于后续独立迭代，需要分别进行设计和安全审查。
