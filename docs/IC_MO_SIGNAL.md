## IC＋MO 当前信号上线（2026-11-27）

授权范围：只替换 `futures-preview.html` 的原收盘信号与预计当前持仓两个模块；会员持仓、图表、筛选、其它 Tab 与共享母版不变。

- 规则：采用研究 X0506 的 45 天／5% 虚值／Delta75%，只改 A 高于6500恒2手；其余冻结字段见 `ic_mo_engine.CFG`。
- 原 `ic_strategy.py`、`data/ic_strategy.json` 历史和既有工作流保留，不倒改。新包 `data/ic_mo_strategy.json` 是独立连续研究模拟账户，不代表真实账户。
- 初始状态严格复现 2022-07-22 起400万元研究账户到2026-11-25；逐步延续2026-11-26及之后，官方CSV今收盘/结算/Delta/量仓及同日指数齐全才推进。2026年10月至11月逐日现金、权益、期货净收益、D状态、持仓和逐笔成交数值已和原研究实现交叉核验。
- `ic_mo_update.py --mode close` 增量补齐已收盘交易日并保存当日正式目标，已有数据幂等；`--mode preview` 仅14:45–15:00采集同日且180秒内的指数/IC/MO时间戳报价，快照保留，不把预判执行到正式账户。
- 盘中MO Delta缺少可用官方实时接口，使用真实权利金隐波反解并明确“估算”。收盘使用官方Delta。尾盘只观察同约更新并展示触发条件，不假定仅改价格即可获得重新通过资金和容量检查的可成交订单。
- 新 `ic-mo-signal.yml` 只允许提交该新包，14:45预判、15:35起补查收盘，Pages同步白名单新增该包。原会员工作流、实时ETF、两融、归档和全球源不变。
- 预计持仓来自连续模型成交记录FIFO，按每个剩余批次加权成本。MO只买Put，市值不当作卖方保证金；收盘目标与当前模型持仓差额分别展示。真实成交、账户余额与未成交订单不写入该估算。

验收：`python3 -m unittest tests.test_ic_mo tests.test_expected_holdings tests.test_ic_strategy`；目标文件与母版／其他模块字节核对；真实 `dashboard.html#futures-preview.html` 桌面和390窄屏及策略说明、变仓记录、会员筛选互动。不得通过重录兼容fixtures追绿。

回滚：恢复上线前 `futures-preview.html`，停用新增 `ic-mo-signal.yml` 即恢复原收盘与FIFO模块；旧数据一直保留，不动任何真实账户文件。
