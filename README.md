# 国家队·分时八宫格
七只宽基 ETF 分时 + 分钟成交额>1亿合计估算。「今日实时」可按日期切换存档，页面大约每 30 秒重读 `data/live.json`。成交额阈值代理，非官方国家队认定。数据：腾讯行情。

## 实时数据在云端
页面由 GitHub Pages 从 `main` 提供，手机打开不依赖笔记本。盘中 `data/live.json` 由 Actions 工作流 **Refresh live.json**（`.github/workflows/live.yml`）更新：

- 北京时间周一至周五 09:00–15:55 每 5 分钟触发一次（GitHub cron 用 UTC：`*/5 1-7 * * 1-5`）。
- 只有 09:15–11:35 和 12:55–15:05 会真正拉取；周末、午休和时段外直接退出，不提交。没有内置休市日历，法定假日若落在这两个钟点里仍会跑，行情没变就不提交。
- 行情内容有变化才提交并推到 `main`（只改了更新时间不会提交）。GitHub 定时最快大约 5 分钟，忙时还会再晚几分钟。页面仍用 `live.json?t=时间戳` 避开浏览器缓存；Pages 的 CDN 有时还会再留几分钟（文档上限大约 10 分钟）。
- 笔记本休眠不再挡住观众。`live_daemon.sh` 只是机器醒着时可选的更密补充（大约每分钟）；云端以 Actions 为准。

需要允许 Actions 写入：Settings → Actions → General → Workflow permissions 选 **Read and write permissions**。若 `main` 禁止直接推送，还要放行 `github-actions[bot]`，否则 Pages 不会更新。定时任务只认 `main` 上的工作流，合并后才开始跑。仓库连续 60 天没有任何提交时，GitHub 会停用定时任务；交易日有推送则碰不到。

## 数据结构
- `data/history.json`：日期 → 七只 ETF 估算、`合计`、`上证收盘`、`上证涨跌幅`(%)
- `data/minutes/YYYY-MM-DD.json`：`{date, etfs:{code:{t,p,a}}}`，a 为每分钟成交额（亿元）

## 追加一天
收盘后（15:05 以后，北京时间）在仓库根目录运行 `python3 archive.py`，然后 commit 并 push `data/`。腾讯分时接口只返回当天数据，错过的日子无法补录。

## 两融余额 · 指数对比（margin.html）
`data/margin_balance.json`：两融余额（沪+深，亿元，2010-03-31 起）与创业板指 399006、科创50 000688、中证500 000905、中证1000 000852、沪深300 000300、上证50 000016 日收盘（全量历史）。
收盘后（建议 17:00 以后/次日，交易所两融披露滞后约 1 交易日）运行 `python3 margin.py`（需 akshare，可用 /workspace/.venv-nt），然后 commit 并 push `data/margin_balance.json`。脚本每次全量重拉，漏跑无影响。
