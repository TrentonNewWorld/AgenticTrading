# vn.py 模拟行情接入（Loop 1）

当前接入用于在没有券商账号、IB Gateway 和真实行情权限时，先验证
NewWorldTrading 能正确接收 vn.py 的行情对象并完成回测。

## 当前已经接通的流程

```text
vn.py BarData
    -> OHLCV DataFrame
    -> NewWorldTrading 回测引擎
    -> Agent / Buy and Hold / DJIA 三类结果
    -> Dashboard 图表、指标和交易记录
```

模拟器生成确定、可重复的 DJIA-30 小时 K 线。它使用真实的
`vnpy.trader.object.BarData`，但价格是程序生成的，不是真实市场行情。

## 安装

从仓库根目录执行：

```bash
pip install -r requirements.txt
pip install -r requirements-vnpy.txt
```

vn.py 是可选依赖。只使用原来的 Alpaca 数据源时，不需要安装
`requirements-vnpy.txt`。

## 启动

```bash
ENABLE_VNPY_SIMULATION=true \
python -m uvicorn dashboard.backend.app:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/app`，进入 Playground 的 Backtest 页面，在
Market Data 中选择 `vn.py simulated data`。

推荐使用标准验证日期：

```text
2026-04-01 至 2026-04-23
```

模拟模式会自动关闭 LLM 调用，因此不需要 Anthropic 或其他模型 API Key。运行完成后，
图表标题和历史记录会显示 `vn.py simulated data`，用于区分真实数据结果。

也可以直接使用命令行：

```bash
ENABLE_VNPY_SIMULATION=true python dashboard/scripts/backtest_hourly_agent.py \
  --start 2026-04-01 \
  --end 2026-04-23 \
  --data-source vnpy_simulation \
  --no-llm
```

## 如何关闭

不要设置 `ENABLE_VNPY_SIMULATION`，或将它设为 `false`，然后重启服务：

```bash
ENABLE_VNPY_SIMULATION=false \
python -m uvicorn dashboard.backend.app:app --host 127.0.0.1 --port 8000
```

关闭后，前端不显示 vn.py 模拟选项；后端直接请求该数据源会返回 403。Alpaca 始终是
默认数据源，旧请求不传 `data_source` 时行为不变。

## 结果边界

- 模拟价格只用于验证数据结构、策略执行、交易记录和结果展示。
- 回测收益、夏普比率和最大回撤不代表任何真实市场表现。
- 本轮不会连接 IB、TWS、IB Gateway 或真实交易账户。
- 数据源出错时不会自动回退到 Alpaca，避免混淆不同来源的结果。

Loop 2 才会在获得 IB 模拟账户后接入 `vnpy_ib`，并由独立 vn.py Worker 管理
TWS/IB Gateway 的连接、订阅和订单状态。
