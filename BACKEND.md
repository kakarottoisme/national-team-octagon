# 后台维护地图

## 架构与数据流
公开源 → Python 采集/计算 → 仓库 JSON → GitHub Pages 同源读取 → 原四页。没有独立服务器、数据库、登录或交易下单服务。`dashboard.html` 与 `dashboard-tabs.js` 只负责缓存四页、提前渲染及导航；不是新的可见页面壳。

### 资金观察
`index.html` 读 `data/live.json`、`data/history.json`、`data/minutes/YYYY-MM-DD.json`，以及 `preview-data/turnover-baseline.json` / `turnover-history.json`。ETF 列表固定为上证50、沪深300、中证A500、中证500、中证1000、创业板、科创50。
- `fetch_live.py`：腾讯七 ETF 分时和上证日线；腾讯上证失败时东财兜底；只生成 live
- `index_quotes.py`：仅供悬浮展示的七个对应指数分时。`fetch_live.main` 可选附加 `index_minutes`，8秒超时并行、单源失败不阻断ETF。前端严格同日同分钟匹配，不反推指数
- `archive.py`：收盘后抓取当天ETF分时，追加 history；默认一起保存同日完整指数1分钟记录，来源失败时保留已存完整指数或同日live缓存，不拿其他日期替代。`include_indices=False`保留旧字节输出
- `restore_archive_snapshot.py`：人工恢复工具及2026-11-23授权的日终晚到兜底共用门禁，不抓行情。指定已存在的Git提交或 `from_git(root,date)` 自动发现当日最新完整快照；浅克隆仅补目标日历史。严格验证同日15:00后快照、七ETF完整原始分钟/非负成交额、七指数完整1分钟端点及上证同日收盘/前日基准；日度估算现算并与原快照一致后才允许追加。已有日期不覆盖，保存来源提交与SHA256；原ETF t/p/a/pre及指数端点不改、不插值。人工用法 `python3 restore_archive_snapshot.py --snapshot-commit <SHA> --date <YYYY-MM-DD>` 先检查，明确恢复后加 `--apply`。只写history.json和该日期minutes。日更由ashare_tasks.archive_mode区分原15:10–15:30实时源和同日15:30后Git快照；晚到成功经Pages文件核验后静默。不改15:12调度和其他采集权限。
  - 2026-11-20缺档恢复：当日实时数据曾发布，最后完整收盘快照提交为 `5c90371d067a575d37aecda16e3f41b8bbca9c14`（15:01:29）；此前history止于11-19且没有11-20的minute文件，从该原始快照追加恢复，七ETF/七指数各242个原生点。排查时定时工具显示八宫格日终存档最近运行时间为11-20 15:35:36，已超过任务与脚本共同限定的15:30，但这个字段未区分开始/结束，不能断言是平台排队23分钟。任务状态执行成功不代表归档业务成功；缺档与过窗规则已核实，20日完整分段记录不可得。11-18归档提交15:28，11-19先在窗口内因累计额倒退失败、后来15:48人工补救，并非以往未出现故障。11-23授权的兜底与阶段日志见AUTOMATION.md。
- `backfill_index_minutes.py`：只给已存在的ETF日期档补充顶层`index_minutes`，不改ETF、日汇总、持仓或策略。新浪历史1分钟必须完整才采用，否则取完整5分钟端点（48点/日）；不插值。每只附`date/interval_minutes/price_type/source/source_url/collected_at/interpolated`，回补全部覆盖后才写入；命令`python3 backfill_index_minutes.py --staging-dir <目录> --apply`。悬浮标签明示1/5分钟，5分钟非端点显示“无5分钟点”
- `index.html` 七ETF涨跌幅图使用 `sharedPctAxis`：同日七只最大绝对涨跌幅、10%留白、向外取整，最小±0.5%，上下对称，五刻度；实时及历史各计算一次再传七图。`etfPercentSeries`保留原三位小数，缺值为null。删除隐藏价格序列，悬浮框仍从原p读取价格；不改成交额、MACD、数据或任务。
- `preview-tools/turnover_baseline.py`：20 完整交易日、48 个同时点均值，供盘中成交额比较
- `preview-tools/turnover_history.py`：新浪60分钟K按日累加、配合本地 live 进行一致性检查
- `backfill_historical_overview.py`：只读Git中已保存同日完整收盘live快照及严格早于观察日的20日基线，生成`data/historical_overview.json`。仅补有原始值的历史概览，不改既有分钟档或history；日期/来源提交/SHA留在数据文件。资金观察历史页读取该包，或同日完整minute档的七只net_buy；无数据显示横杠、说明留空，不用当前滚动基线回算旧日。

### 两融余额
`margin.html` 读 `data/margin_balance.json`。
`margin.py` 通过 akshare/pandas 取沪深融资融券余额及六个指数日收盘；深市缺失日按交易所接口补齐。手工全量入口保留，云端`ashare_tasks.py`日更只追加缺日期，既有历史逐值不覆盖。2026-11-23授权改为次一交易日09:05/15/25核对前一交易日，SSE信用交易日期+SZSE metadata.subname同日验证，成功标记为数据日。18:05只读data-audit核对前日两融和当日八宫格缺档；窗口迟到/最终缺日不再静默。两融值亿元，指数点位；缺少同日值不能当零或用各自最近日期拼成比较。

### 期指持仓
`futures-preview.html` 读 `data/ic_strategy.json`、本人 `data/holdings.json`、`preview-data/official/snapshot-latest.json`、`preview-data/official/futures/{IH,IF,IC,IM}.json` 等官方历史。
- `ic_strategy.py`：新浪中证500和 IC 收盘、中金所今收盘核对，生成原策略信号及模型历史
- `preview-tools/collect_preview.py`：SSE ETF 份额及 CFFEX 会员排名、快照、状态、四品种日更
- `preview-tools/futures_history.py`：CFFEX 原始 CSV 严格验日期、前20会员并集、独立品种历史
- `holdings.json`：本人配置，非模型仓位。采集器不应写它；缺净账本时前端不硬算 D
- 会员持仓观察日与顶部 IC 信号日是两套独立日期；验证发布必须打开 `dashboard.html#futures-preview.html` 核对两处，不以四个会员 shard 更新代替信号更新。日期选择停在上一次最新日时，刷新跟随新最新日；明确选择的历史日仍保留。
- `futures_close_update.py`：两种增量入口，members仍按原收盘窗口只追加已严格验日期的缺失四会员shard。strategy在2026-11-24授权后按北京时间最近已收盘官方交易日补齐，延迟到凌晨/上午仍处理前日，过滤未收盘新日K；每个新增日验指数、MA250与四挂牌官方今收盘，再调用原引擎候选，仅追加新日且保留旧历史，已有目标日不重算。`ic-strategy.yml` checkout显式main；pending非零，不把旧数据已上线当成功；`--verify-date`只读验收预期数据日，Pages逐字一致后才完成。原schedule、members路径、SSE/全球任务、账户与策略公式不改。前台60秒可见重读及Tab恢复由dashboard可见消息驱动。

### 份额与申赎
`margin.html` 中的独立 `etfFlows` 组件读 `data/etf_share_flows.json`，并入申赎两融 Tab，先申赎后两融。名单仍为 17 只宽基，包含 159352；概览、合计申赎柱/累计线、单只份额线按独立截止日展示，不与盘中成交混算。国家队持仓、ETF 明细从界面移除，原核验文件保留但页面不读。`etf-flows.html` 仅兼容旧链接跳转。
- `etf_share_flows.py`：上交所按日份额（STAT_DATE）、深交所 fund_jjgm 逐基金历史规模（每行自带 size_date，万份）、东财日 K 成交均价。估算净流入 = 份额差 × 成交均价。窗口内缺一天则为 null。
- 深市主源 `CATALOGID=fund_jjgm` 失败时，才降级抓取 1000_lf 无日期当前规模，写入 `data/etf_szse_snapshot.json`（`date_status=undated_fallback`），仅留痕不参与计算；成功时 flows 内标 `dated_via_fund_jjgm`。长跨度查询按 28 自然日分段（接口 pageno 不翻历史，每段至多 20 个交易日），单页步行回退保证无缺口。
- 官方持仓文件 `data/etf_official_holdings.json`（schema `etf-official-holdings-2`）分两个口径：`annual_2025` 存 2025 年年报（截止 2025-12-31）前十名中的国家队具名份额，17 只逐份核对自交易所/巨潮原始 PDF；`interim_2026` 保留 17 份中报核对痕迹且 `rows` 为空。证监会公告〔2026〕5 号（2026-05-01 施行）第三十一条后年报与中报均不再强制披露前十名持有人，法规原文链接写在 `regulation_notes`，媒体和 Wind 汇总不写入。
- 云端由 `.github/workflows/etf-share-flows.yml` 在北京 23:40 和次日 00:10 运行，无变化不提交。不碰 `live.yml`。
- 失败重读不能抹掉已有完整同日沪市官方份额的可用性：`effective_blocked_dates`仅忽略这类旧错误标记；真实未确认日与单标的缺口仍为null，不跨缺口。`--check-freshness`不联网不写入，按官方交易日检查应披露日份额/均价；工作流先保存抓到的数据，再执行该检查，缺项或截止日落后返回失败，不能把进程成功等同于数据新鲜。

### 全球风险
`risk-preview.html` 读 `preview-data/global.json`。
`collect_preview.maybe_collect_global()` 调 `global_sources.update_global()`；后者合并 Treasury、public_macro 与上次缓存。`public_macro.py` 管理名义/实际曲线、ACM、SOFR/IORB 同日差等。现有 Cboe/ICE/FINRA 等受限指标不自动复制成数值，保留现有 link_only/restricted/pending 处理；不要因清理代码绕开这些限制。文件里的历史注释曾描述旧接入状态，以当前函数和状态字段为准。

### 市场顶底（独立新增）
`market_cycle.py` + `market_factors.py` → `data/market_cycle.json` → `market-cycle.html`。schema=`market-factor-2`，规则=`factor-v2`，先读 `docs/MARKET_FACTORS.md`。A股六宽基、美股三宽基分开；顶部/底部各4组×2项固定8项分母，组内组间等权。`latest.scores`含score/met/total/missing/groups/factors，各项当前值、阈值和数据日可追溯。任何必需缺项不给总分，不缩分母；底部改善6项与超跌2项分别展示。`history`保存252日评分，`evaluation`固定4分箱、21/63日非重叠观察、方向/严格命中、均值/最低/基准/年份匹配/分段/次日敏感性及观察点。仅价格衍生评分，比例不等于概率；没有信用/估值/期权实质接入。两融不参与评分。`observations`按日期+rule_version保留首次留痕，旧v1不覆盖也不冒充v2前向结果。缺源保留旧日stale；旧包版本不显示新评分。不联动原六任务、持仓、IC或live。既有market-cycle工作流及更新时间保持不变，美国在A股长假不更新的限制尚未改变。测试 `python3 -m unittest tests.test_market_cycle tests.test_market_factors tests.test_dashboard_ui`。

## 本轮整理后的模块
- `backend_common.py`：ETF 常量、分时累计额差分、方向估算及旧日期转换；纯函数，无抓取与写入
- `backend_io.py`：原子文本/JSON 替换，先序列化，再写同目录临时文件、flush/fsync、rename；异常不覆盖旧文件，清理临时文件
- `fetch_live.py`：`build_payload(fetch=None, now=None)` 构建；`main(output=None, fetch=None, now=None, include_indices=True)` 写入，可选附加指数分时；关闭附加时保留原字节输出
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
python3 etf_share_flows.py
```
`fetch_live/archive/ic_strategy` 用标准库；`margin` 需 akshare/pandas；preview 全量依赖按 `preview-tools/requirements.txt` 安装，期指收盘补查只用标准库。2026-11-20用户授权追加期指收盘补查，不改依赖或其他调度。

现有调度：live 保留 UTC `*/5 1-7 * * 1-5` 作为重启机会，另于北京08:53/12:43预热；同一concurrency组仅一个`live_runner.py`实例运行，每300秒复用原采集器，仅北京09:15–11:35、12:55–15:05采集，午休/收盘结束，周末/未确认日历关闭。首次GitHub启动仍可能排队延迟。IC与会员期指 北京15:35–17:55每10分钟，IC额外保留17:40，两者20:10/22:10/23:30兜底；preview全量仍23:30。`archive/margin`云端任务与去重见`AUTOMATION.md`；`live_daemon.sh` 是机器醒着时的可选补充，不属于云端定时。

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
