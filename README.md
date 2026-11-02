# 国家队·分时八宫格
七只宽基 ETF 分时 + 分钟成交额>1亿合计估算。「今日实时」每 60 秒刷新，可按日期切换存档。成交额阈值代理，非官方国家队认定。数据：腾讯行情。

## 数据结构
- `data/history.json`：日期 → 七只 ETF 估算、`合计`、`上证收盘`、`上证涨跌幅`(%)
- `data/minutes/YYYY-MM-DD.json`：`{date, etfs:{code:{t,p,a}}}`，a 为每分钟成交额（亿元）

## 追加一天
收盘后（15:05 以后，北京时间）在仓库根目录运行 `python3 archive.py`，然后 commit 并 push `data/`。腾讯分时接口只返回当天数据，错过的日子无法补录。

## 两融余额 · 指数对比（margin.html）
`data/margin_balance.json`：两融余额（沪+深，亿元，2010-03-31 起）与创业板指 399006、科创50 000688、中证500 000905、中证1000 000852、沪深300 000300、上证50 000016 日收盘（全量历史）。
收盘后（建议 17:00 以后/次日，交易所两融披露滞后约 1 交易日）运行 `python3 margin.py`（需 akshare，可用 /workspace/.venv-nt），然后 commit 并 push `data/margin_balance.json`。脚本每次全量重拉，漏跑无影响。
