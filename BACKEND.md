# 后台维护地图

## 架构与数据流
公开源 → Python 采集/计算 → 仓库 JSON → GitHub Pages 同源读取 → 原四页。没有独立服务器、数据库、登录或交易下单服务。`dashboard.html` 与 `dashboard-tabs.js` 只负责缓存四页、提前渲染及导航；不是新的可见页面壳。

### 资金观察
`index.html` 读 `data/live.json`、`data/history.json`、`data/minutes/YYYY-MM-DD.json`，以及 `preview-data/turnover-baseline.json` / `turnover-history.json`。ETF 列表固定为上证50、沪深300、中证A500、中证500、中证1000、创业板、科创50。
- `fetch_live.py`：腾讯七 ETF 分时和上证日线；腾讯上证失败时东财兜底；只生成 live
- `archive.py`：收盘后抓取当天分时，追加 history 并写当天 minutes；不能补抓错过日期的腾讯分时
- `preview-tools/turnover_baseline.py`：20 完整交易日、48 个同时点均值，供盘中成交额比较
- `preview-tools/turnover_history.py`：新浪60分钟K按日累加、配合本地 live 进行一致性检查

### 两融余额
`margin.html` 读 `data/margin_balance.json`。
`margin.py` 通过 akshare/pandas 取沪深融资融券余额及六个指数日收盘；深市缺失日按交易所接口补齐。每次全量拉，不改变原数据源/时间口径。两融值亿元，指数点位；缺少同日值不能当零或用各自最近日期拼成比较。

### 期指持仓
`futures-preview.html` 读 `data/ic_strategy.json`、本人 `data/holdings.json`、`preview-data/official/snapshot-latest.json`、`preview-data/official/futures/{IH,IF,IC,IM}.json` 等官方历史。
- `ic_strategy.py`：新浪中证500和 IC 收盘、中金所今收盘核对，生成原策略信号及模型历史
- `preview-tools/collect_preview.py`：SSE ETF 份额及 CFFEX 会员排名、快照、状态、四品种日更
- `preview-tools/futures_history.py`：CFFEX 原始 CSV 严格验日期、前20会员并集、独立品种历史
- `holdings.json`：本人配置，非模型仓位。采集器不应写它；缺净账本时前端不硬算 D

### 全球风险
`risk-preview.html` 读 `preview-data/global.json`。
`collect_preview.maybe_collect_global()` 调 `global_sources.update_global()`；后者合并 Treasury、public_macro 与上次缓存。`public_macro.py` 管理名义/实际曲线、ACM、SOFR/IORB 同日差等。现有 Cboe/ICE/FINRA 等受限指标不自动复制成数值，保留现有 link_only/restricted/pending 处理；不要因清理代码绕开这些限制。文件里的历史注释曾描述旧接入状态，以当前函数和状态字段为准。

## 本轮整理后的模块
- `backend_common.py`：ETF 常量、分时累计额差分、方向估算及旧日期转换；纯函数，无抓取与写入
- `backend_io.py`：原子文本/JSON 替换，先序列化，再写同目录临时文件、flush/fsync、rename；异常不覆盖旧文件，清理临时文件
- `fetch_live.py`：`build_payload(fetch=None, now=None)` 构建；`main(output=None, fetch=None, now=None)` 写入
- `archive.py`：`build_archive(fetch=None)` 构建；`main(data_dir=None, fetch=None)` 更新归档
- `margin.py` / `ic_strategy.py`：数值逻辑保留，只复用完整文件写入
- preview 模块未迁包、未统一请求策略；`futures_history` 只关闭读取句柄，保持 published 才写的语义

根脚本默认输出基于自身所在仓库目录，避免 AI 换工作目录后把 data 写错地方。各工作流和 `live_daemon.sh` 都在仓库根运行，原命令不变。

## 运行入口
在仓库根目录：
```sh
# 安全验收：不联网，不采集，不改变生产数据
python3 check_backend.py

# 以下是真实采集，会写正式/预览数据；不要当作单元测试运行
python3 fetch_live.py
python3 archive.py
python3 margin.py
python3 ic_strategy.py
python3 preview-tools/collect_preview.py
python3 preview-tools/turnover_baseline.py
python3 preview-tools/turnover_history.py
```
`fetch_live/archive/ic_strategy` 用标准库；`margin` 需 akshare/pandas；preview 依赖按 `preview-tools/requirements.txt` 安装。本轮不改现有依赖版本或调度。

现有调度：live 的 UTC `*/5 1-7 * * 1-5`，实际执行窗口北京09:15–11:35、12:55–15:05；IC 北京17:40及20:10；preview 北京23:30。`archive/margin` 仍是手动维护，`live_daemon.sh` 是机器醒着时的可选补充，不属于云端定时。

## 关键数据契约
### live / archive
- `live.date` 腾讯 `YYYYMMDD`；history 日期键与 minutes.date 为 `YYYY-MM-DD`
- `t/p/a` 逐点对齐；a = 本分钟累计成交额差 / 1e8，round 4，单位亿元
- 估算只计 `a > 1`，每只先 round 2，再相加 round 2；阈值本身不能改成 `>=`
- `amt` 累计额 round 4；`net_buy`=(外盘−内盘)/(外盘+内盘)×amt，round 3；不是净流入、申赎或机构身份
- 外内盘不可用则 net_buy=null；0 与 null 不同；汇总 covered 只计非 null 的方向值
- live JSON：UTF-8、紧凑 separators；history：ensure_ascii=False/indent1/日期升序；minutes：保留原默认 ASCII 转义及紧凑格式
- live 上证只在匹配日且有前日时写涨跌；archive 保留原匹配首日索引行为，不在本轮改业务
- 任一 ETF 抓取/解析抛异常，不写新的 live 或归档；原子化不等于跨 history/minutes 的事务

### IC
`schema/latest/days` 及每一天原有字段由 `build_payload`、`make_day` 和 `dump_payload` 定义；dump 保留每个 day 一行、末尾换行。本轮所有非 I/O 函数的 AST 一致。A/B/C/D、MA250、0–10手、D最多4手、乘数200、12%保证金、3pp开仓/2pp换仓/最少10自然日等均是现行模型，未重新回测认证。模型历史与本人账户是不同层。

### 官方期指 / 风险
- futures schema=`futures-history-2.0`，records日期键；日期来自 CSV 交易日，不能拿快照日期推断
- 缺榜不是0，单边缺榜 long/short=null；net_short=short−long，仅双方完整可观测时算
- `collect_daily` 源失败/未发布保留盘上历史，不写该次伪记录；已发布才写回完整 shard
- global schema=`2.1-safe`；每个 metric 有 id/status/value/as_of/history/stale/source/license，保留 metadata
- global 文件 run_state 是 `ok/degraded_cached/partial/failed`；函数返回摘要 state 是 `ok/degraded_partial/failed_no_cache`，二者不同，不可混用
- stale 继承旧数值、原 as_of/collected_at，last_attempt_at 才是本次尝试；失败不把旧日期改成今日

## 验收与发布
`check_backend.py` 汇集根兼容测试、IC、页面静态/JS回归、preview 采集/期指/macro和global测试；阻止 HTTP/socket，检查测试前后前端/生产JSON/workflow哈希。
`tests/fixtures/backend_compatibility.json` 从整理前脚本录制11场景（正常、空分钟、缺 quote、零外内盘、负方向、坏quote、错日期、无匹配日、qfqday、首日、兜底），逐字节锁定 JSON。兼容文件也锁定 IC 非 I/O 函数、六个核心前端哈希。

本地单文件预览及其包装测试不是线上仓库依赖，后台发布不带它。发布只包含明确的脚本/共享模块/文档/测试，不能把账户、数据快照、备份、缓存或测试素材一起上传。当前公开站点是 GitHub Pages，不另部署到其他域名。
