# 接手说明

给后续任何 AI。先读这一页，再按任务打开下面两份，不要凭文件名猜。

- 视觉：[docs/DESIGN.md](docs/DESIGN.md)。字号和页眉只改 `dashboard-shell.css`。
- 豆包定时任务：[AUTOMATION.md](AUTOMATION.md)。2026-11-19 主人批准新增三条：盘中刷新自恢复、成交后更新持仓、IC 策略信号变化提醒。2026-11-20另授权期指前台60秒可见刷新与两条既有后台工作流收盘补查；除此之外不改规则、窗口和输出。
- 脚本与字段：[BACKEND.md](BACKEND.md)。

## 这是什么

GitHub Pages 上的投资观察看板。公开行情写成 JSON，页面只读这些文件。没有下单、没有数据库、没有登录。主人在工位上看，界面保持白底蓝灰，不要做成红绿交易软件。

线上地址：`https://kakarottoisme.github.io/national-team-octagon/`  
`main` 一推，Pages 就重新发布。定时任务也只认 `main` 上的工作流。

## 页面

`dashboard.html` 把下面四页放进全屏 iframe，地址用 hash，例如 `dashboard.html#margin.html`。`dashboard-tabs.js` 负责切页；单独打开某一页会跳回看板。

| 文件 | 名称 | 读什么 |
|---|---|---|
| `index.html` | 资金观察 | 盘中成交及成交历史；读 `data/live.json`、`data/history.json`、`data/minutes/YYYY-MM-DD.json` 及成交基准 |
| `margin.html` | 申赎两融 | 先 ETF 份额申赎、后两融余额，分别读 `data/etf_share_flows.json`、`data/margin_balance.json` |
| `futures-preview.html` | 期指持仓 | `data/ic_strategy.json`、`data/holdings.json`、`preview-data/official/` |
| `risk-preview.html` | 全球风险 | `preview-data/global.json` |
| `market-cycle.html` | 市场顶底 | `data/market_cycle.json`；A股/美股分开，1–3个月价格观察规则，不联动持仓或 IC |

新增独立采集器 `market_cycle.py`（计算模块 `market_factors.py`）与工作流 `market-cycle.yml`：北京官方A股交易日08:20、17:10，只写 `data/market_cycle.json`。factor-v2 顶部/底部各4组8项固定分母，满足比例不叫概率、不映射仓位。先读 `docs/MARKET_FACTORS.md`；固定分箱历史检验与上线后版本留痕分开，旧v1观察不删除。现有六项豆包定时任务及四页数据写入器不变。当前仅价格衍生证据，不把实现波动当VIX，不复制受限MOVE/CDS/GEX。

`etf-flows.html` 只保留旧链接跳转到申赎两融的份额申赎段，不再是第五个 Tab。国家队官方持仓与 ETF 明细已按主人要求从界面移除；已核验数据文件保留，不再由页面读取。

文件名带 preview 的期指页和风险页就是正式 Tab，不是草稿。

不在看板里、仍可打开的旧页：`preview.html`、`margin-preview.html`、`flows-preview.html`。没有工作流引用。先不要删，见文末候选。

## 谁写数据

| 文件 | 谁写 | 不要做什么 |
|---|---|---|
| `data/live.json` | `.github/workflows/live.yml` 里的 `live_runner.py`，内部调用 `fetch_live.py` | 不要停用这个工作流，也不要再做一个写 live 的定时任务 |
| `data/history.json`、`data/minutes/` | `archive.py`；云端入口见 AUTOMATION.md 的八宫格归档 | 腾讯分时补不了错过的ETF日期；已有ETF日期的指数可用`backfill_index_minutes.py`补1/5分钟端点，严格标注粒度、不插值 |
| `data/margin_balance.json` | `margin.py`；云端窗口见 AUTOMATION.md | 不要用各自最近日期拼成同一天 |
| `data/ic_strategy.json` | 原 `ic_strategy.py` 计算，`futures_close_update.py --mode strategy` 增量门禁；`ic-strategy.yml` 收盘后补查（详见 AUTOMATION.md） | 未获授权不改 A/B/C/D、选约、换仓 |
| `data/holdings.json` | 主人在对话里给齐 contract、direction、lots、entry_price、entry_date 后可改；换仓再写 rolls（close_price、open_price、realized_pnl=(平仓价−原开仓价)×lots×200，空头符号相反）并累加 d_realized_net_pnl | 缺任一项先问。account_balance 只用主人报的数。成交价不写入 last_price。除此之外不改、不删、不模拟 |
| `preview-data/official/`、`preview-data/status.json` | `preview-data.yml` 收盘补查只运行 `futures_close_update.py --mode members`；23:30保留 `collect_preview.py` 全量 | 不写 `data/`；会员持仓不等待ETF/全球源 |
| `preview-data/global.json` | 同上采集里的全球风险更新 | 受限源不要填造数值 |
| `preview-data/turnover-*.json` | `preview-tools/turnover_baseline.py`、`turnover_history.py` | |
| `data/etf_share_history.json`、`data/etf_share_flows.json`、`data/etf_szse_snapshot.json` | `etf_share_flows.py`；工作流 `etf-share-flows.yml`（北京 23:40，次日 00:10 再试） | 深市只认 fund_jjgm 的 size_date；失败降级的无日期快照不得写成交易日。缺份额或成交均价保持 null。不改 live.yml |
| `data/etf_official_holdings.json` | 每年 4 月年报、9 月中期报告后按基金原文核对。schema v2 分 `annual_2025`（2025 年报前十名具名份额，17 只全量核对）与 `interim_2026`（新规后无前十名，rows 留空并附法规依据）；未在原文具名的持有人不写 | 不用媒体或 Wind 汇总；2026-05-01 后年报中报均不强制前十名，以证监会公告〔2026〕5 号第三十一条为准 |

工作流文件：

- `live.yml`：名称 Refresh live.json。北京 08:53 / 12:43 预热，`*/5 1-7 * * 1-5`（UTC）是重启机会。真正采集只在 09:15–11:35 和 12:55–15:05。
- `ic-strategy.yml`：只提交 `data/ic_strategy.json`，当前日已有时不重算、不提交；北京15:35–17:55每10分钟，保留17:40，20:10/22:10/23:30兜底。
- `preview-data.yml`：同一收盘补查窗口只提交新增四会员shard；20:10/22:10兜底，23:30保留原ETF/全球/成交额全量路径。手动默认期指only，`full_refresh=true`明确选择全量。
- 期指页可见时每60秒读取；切回Tab/浏览器可见立即读，隐藏暂停，网络失败保留原日期。后台schedule不是精确实时保证，可能排队。

`live_daemon.sh` 是电脑醒着时的可选补充，不是云端定时。`run_ashare_task.sh` 配 `ashare_tasks.py`，给 AUTOMATION.md 里的豆包任务用。

## 本地与发布

仓库根目录：

```sh
python3 -m http.server 8765
# 打开 http://127.0.0.1:8765/dashboard.html
```

单独打开某个 Tab 会跳回看板。要看单页本身，加 `?view=embedded`。

离线检查（不抓数、不改生产 JSON）：

```sh
python3 check_backend.py
python3 -m unittest tests.test_dashboard_ui tests.test_ic_strategy
```

真实采集会写文件，不要当成测试跑：`fetch_live.py`、`archive.py`、`margin.py`、`ic_strategy.py`、`preview-tools/collect_preview.py`。

发布就是把核对过的提交推到 `main`。Pages 随后构建。不要 force push，不要为了发布再跑一遍全量抓取。

## 小修改快速路径

文案、颜色、间距、单页图表显示使用本节，不套全库排障。数据源、公式、账户、权限和工作流变更不属小改，仍按各自门禁执行。

1. 一次同步main；只读本页与当前目标文件、需要的对应规范，不重复全量读AGENTS/BACKEND/AUTOMATION。先确认这次保留/改动范围，再一次完成补丁。
2. 保存本次base SHA，执行 `python3 check_ui_patch.py check --base <base>`。入口只选择改动相关测试；遇到data/、后台脚本或workflow变更立即拒绝快路。禁止为让旧冻结基线通过而重录fixture。
3. 真实入口 `dashboard.html#目标页` 验证一个关键交互；纯文案无需四Tab巡检。图表/布局才加桌面与390窄屏，同一浏览器调用一次完成同一状态的读数与截图。跨导航/交互仍先观察再行动。
4. 确认后一次commit→fetch/rebase→push（不用force）；只stage本次文件。`python3 check_ui_patch.py verify --commit <发布SHA>` 合并等待Pages和线上文件逐字核验，不再单独反复list/watch/API。后续行情提交已包含本次代码时接受后代部署，不等待被取消的旧run，也不再触发额外build。
5. 已发布后只验线上原入口一次，不重复本地已完成的历史与手机全套；无新增界面或运行逻辑的文档/检查器修改不打开浏览器、不另给预览链接。交付一句改动+入口，阻塞才汇报。

浏览器窄屏参数已确认：`bu.cdp('Emulation.setDeviceMetricsOverride',width=390,height=844,deviceScaleFactor=1,mobile=False)`，同次等待1秒再读布局/截图；不要传参数对象当session_id、不要尝试多种猜测签名。导航可能恢复默认尺寸，需要时重新设一次。截图工具结果不是自动展示，查看真实截图后再判断。

快路只减少重复检查，不降低数值真实性、历史保留、用户实际入口验证、并发保护和部署完成标准。目标是减少工具往返与重复发布，不承诺固定分钟数。

## 硬规则

- 不编造数字、日期、价格或成交。
- 不空提交。
- 不下单。持仓只按主人明确给出的成交修改 `data/holdings.json`。
- 不关闭、不改写 `Refresh live.json` 的采集窗口和写入范围。只改文档时，工作流 YAML 保持原样。
- 不把单笔浮盈当成完整 D 净盈利，也不补造账户余额。
- 界面改动先看 [docs/DESIGN.md](docs/DESIGN.md)。1440 和 390 都要看四页分隔线是否还对齐。
- `tests/fixtures/backend_compatibility.json` 不要为了让测试通过就重录。

## 旧文件，先留着

没有被四页或工作流引用，但是公开地址或数据快照，这次没有移动：

- `preview.html`、`margin-preview.html`、`flows-preview.html`（旧预览，互相链接）
- `preview-data/domestic.json`（文档写明的旧快照路径）
- `preview-data/official/futures/_quarantine_wrong_index.json`
- `favicon.ico`（浏览器会直接请求这个路径）

`logo.png` 没有任何引用，已放到 `archive/`。
