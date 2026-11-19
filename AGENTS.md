# 接手说明

给后续任何 AI。先读这一页，再按任务打开下面两份，不要凭文件名猜。

- 视觉：[docs/DESIGN.md](docs/DESIGN.md)。字号和页眉只改 `dashboard-shell.css`。
- 豆包定时任务：[AUTOMATION.md](AUTOMATION.md)。只引用，不改其中的规则、窗口和输出。
- 脚本与字段：[BACKEND.md](BACKEND.md)。

## 这是什么

GitHub Pages 上的投资观察看板。公开行情写成 JSON，页面只读这些文件。没有下单、没有数据库、没有登录。主人在工位上看，界面保持白底蓝灰，不要做成红绿交易软件。

线上地址：`https://kakarottoisme.github.io/national-team-octagon/`  
`main` 一推，Pages 就重新发布。定时任务也只认 `main` 上的工作流。

## 页面

`dashboard.html` 把下面四页放进全屏 iframe，地址用 hash，例如 `dashboard.html#margin.html`。`dashboard-tabs.js` 负责切页；单独打开某一页会跳回看板。

| 文件 | 名称 | 读什么 |
|---|---|---|
| `index.html` | 资金观察 | `data/live.json`、`data/history.json`、`data/minutes/YYYY-MM-DD.json`，以及 `preview-data/turnover-baseline.json`、`turnover-history.json` |
| `margin.html` | 两融余额 | `data/margin_balance.json` |
| `futures-preview.html` | 期指持仓 | `data/ic_strategy.json`、`data/holdings.json`、`preview-data/official/` |
| `risk-preview.html` | 全球风险 | `preview-data/global.json` |

文件名带 preview 的期指页和风险页就是正式 Tab，不是草稿。

不在看板里、仍可打开的旧页：`preview.html`、`margin-preview.html`、`flows-preview.html`。没有工作流引用。先不要删，见文末候选。

## 谁写数据

| 文件 | 谁写 | 不要做什么 |
|---|---|---|
| `data/live.json` | `.github/workflows/live.yml` 里的 `live_runner.py`，内部调用 `fetch_live.py` | 不要停用这个工作流，也不要再做一个写 live 的定时任务 |
| `data/history.json`、`data/minutes/` | `archive.py`；云端入口见 AUTOMATION.md 的八宫格归档 | 腾讯分时补不了错过的日期 |
| `data/margin_balance.json` | `margin.py`；云端窗口见 AUTOMATION.md | 不要用各自最近日期拼成同一天 |
| `data/ic_strategy.json` | `ic_strategy.py`；工作流 `ic-strategy.yml`（北京 17:40 和 20:10） | 未获授权不改 A/B/C/D、选约、换仓 |
| `data/holdings.json` | 只按主人明确给出的成交改 | 不改、不删、不模拟。缺成交价就留空，不要用开仓价冒充最新价 |
| `preview-data/official/`、`preview-data/status.json` | `preview-tools/collect_preview.py`；工作流 `preview-data.yml`（北京 23:30） | 不写 `data/` |
| `preview-data/global.json` | 同上采集里的全球风险更新 | 受限源不要填造数值 |
| `preview-data/turnover-*.json` | `preview-tools/turnover_baseline.py`、`turnover_history.py` | |

工作流文件：

- `live.yml`：名称 Refresh live.json。北京 08:53 / 12:43 预热，`*/5 1-7 * * 1-5`（UTC）是重启机会。真正采集只在 09:15–11:35 和 12:55–15:05。
- `ic-strategy.yml`：只提交 `data/ic_strategy.json`。
- `preview-data.yml`：只提交 `preview-data/official` 和 `status.json`。

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
