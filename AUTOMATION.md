# A股自动任务

本次授权仅新增观察提醒及指定JSON更新，不下单、不改持仓、不改IC策略和前端。全部北京时间，交易所节假日与周末静默。2026采用已核实上交所休市表；后续年份在线读取年度表，未确认/解析不完整则停止，不能把工作日当交易日。

## 时间与输出
- IC换仓：14:44触发，执行必须仍在14:42–14:47。先读取main的data/holdings.json。新浪sh000905和nf_IC同日、180秒内实时最新价；当月/次月/当季合约去重，剩余不到10天不候选。到期按合约月第三个周五；年化贴水=(指数−期货)/指数×365/剩余日历天。新合约较持仓高至少2pp或持仓不足10天/到期才提醒，价格/贴水/差值/剩余天数齐全，提醒14:57前平旧开新、索取两笔成交价。不触发静默。当前合约价格缺失不得拿开仓价替代。
- 八宫格归档：15:12启动、15:22补试不变，实时抓源仍仅15:10–15:30。**2026-11-23追加授权：晚到不再直接跳过，同一交易日15:30后只从Git中已保存的完整收盘快照补缺档，绝不重新请求腾讯旧日分时。** `restore_archive_snapshot.from_git` 按最新→较早检查同日快照；浅克隆补足目标日历史，七ETF完整分钟、非负原成交额、上证同日/前日、七指数完整1分钟端点和原估算一致才追加。缺源、坏数据或已有单边档停止，不删除/截零/补0/拿别日替代，不覆盖任何既有日期。两个授权输出同一commit，晚到补档后只读核验Pages对应文件才记成功。正常/无变化/已存档/成功补档完全静默；最终失败每日一次。不改变其他五个任务或GitHub工作流。
- 2026-11-23存档修复：腾讯分时在15:00后仍追加报价及累计额回补，不能把这些记录当交易分钟。存档专用入口先选择09:30–11:30、13:00–15:00全部242个原始分钟（包含端点），再严格验证顺序、完整性、价格/前收盘基准与累计额单调，并按原精度差分。交易时段内坏点仍整包拒绝，不截零、不跳点；非交易时段报价不计入收盘分时。不改共享live解析器、阈值、调度或既有历史。11-23缺档从已保存15:03:26完整Git快照恢复。
- 归档执行使用main版本 `run_ashare_task.sh`，任务最初收到时立即记录北京时间并传 `ARCHIVE_TASK_RECEIVED_AT`；wrapper、克隆完成、Python进入、锁等待、源采集/快照恢复、push、Pages核验与业务结果记入仓库外 `archive-runs.jsonl`，当前详情保留在archive.json的last_execution。15:12计划时间与任务收到时间差只表示进入执行前延迟，不能凭它进一步区分平台排队/资源启动；克隆/处理阶段单独有秒数。原15:12schedule不改、不增重复任务。跨午夜不是当日晚到补档，本入口不自动替前日回补。
- 两融（2026-11-23授权修复）：每个交易日09:05启动，窗口09:00–09:40，在同次执行内09:15/09:25补查；以“前一官方交易日”为目标数据日（周一为周五、节假日向前找），09:25为最终补查，迟到但仍在窗口则立即最终检查。深交所按次一交易日开市前披露，原16:35收盘抓当日安排取消。固定akshare==1.18.97，保留原沪深同日相加/亿元口径；SSE信用交易日期和SZSE响应metadata.subname必须匹配，不能以请求日期冒充。仅追加缺失余额及六指数日期，旧数值不覆盖；目标日和最近20自然日内部交易日缺口都齐全才入库。只写data/margin_balance.json，成功按实际数据日margin_success_data_date验收，不再用执行日margin_success_day跳过。早期pending不占告警去重，最终仍缺日期、来源/校验/授权/推送失败或错过窗口必须告警；已齐全不抓不提交、正常完全静默。
- 同一“两融余额日终更新”任务18:05只读兜底（窗口18:00–23:59）：运行data-audit，核对前一交易日两融+六指数及当天八宫格history/minutes，即使早间/收盘任务没有运行也检查，缺档每日一次告警，不采集、不补造、不写任何仓库文件。两融采集和只读兜底共用现有一条任务，cron为`5 9,18 * * 1-5`，不增加另一个写数据任务。
- 2026-11-23追加自动恢复闭环：同一归档任务15:12采集；源暂不可用时返回pending/followup_at=15:22，15:22补试仍源失败则返回pending/followup_at=15:31、next_mode=git_snapshot，不提前发送或消耗告警去重。执行者使用get_current_time及Wait到对应时刻，每次用新隔离main执行同入口；15:31或当日晚到仅校验Git已保存完整收盘快照，成功/已存/无变化完全静默，最终无合格快照才结合原始源失败原因每日一次告警。本条覆盖旧“越窗即告警、恢复成功也通知”要求。授权失败不反复重试；提交/发布结果未确认记录archive_publication_pending，后续只核验远端完整档和Pages，远端缺档保留提交并停止，不重抓或重复提交。不新增任务、不改15:12 schedule，不跨午夜替前日补档。
- 盘中：用户确认接受约5分钟刷新（GitHub首次启动仍可能延迟），仍仅09:15–11:35、12:55–15:05采集。原GitHub Actions改为每半日一个持续运行实例，实例内每300秒采集，不再每轮依赖调度排队；08:53/12:43预热，既有5分钟cron保留为故障重启机会，统一concurrency防止两个实例同时写入。`live_runner.py`复用原`fetch_live.main`向临时文件构建，七ETF必须全部当日、非空且对齐，验证后仅有实质变化才推data/live.json。采集失败保留旧数据，推送结果不明/并发冲突停机并保留本地提交，不强制覆盖。成功不通知。独立健康检查每10分钟观察，至少连续3次异常且已停更20分钟以上才提醒，当日最多一次；午休初段不判停更。
- 盘中刷新自恢复：每个交易日09:20与12:55各检一次。若本半日Refresh live.json没有在运行的实例，或data/live.json在采集窗口（09:15–11:35、12:55–15:05）内停更超过15分钟，可手动workflow_dispatch启动该工作流一次。同一半日最多1次；启动前确认没有正在运行或排队的实例，遵守concurrency组refresh-live-json。恢复成功不通知。手动启动后15分钟仍未更新才提醒一次，当日去重。这是「独立健康检查只观察」的例外：只允许启动这一个工作流，不改YAML、不停用、不另建写live的任务。
- 成交后更新持仓：主人在对话里明确给出成交后，才可写data/holdings.json，且只能写这一份。必填positions[0]：contract（IC加四位年月）、direction（多或空）、lots（整数0–100）、entry_price（>0）、entry_date（YYYY-MM-DD）。换仓还要旧合约平仓价和新合约开仓价，追加rolls一项：date、from、to、lots、direction、close_price、open_price、realized_pnl。多头已实现=(close_price−原entry_price)×lots×200，空头符号相反，并累加到d_realized_net_pnl；as_of改为成交日。account_balance与balance_as_of只用主人报的数，不推算。缺任一项先问，不猜。成交价不写入last_price、last_date、last_price_kind，这三项只保留收盘或结算。校验同futures-preview.html的validateLocalAccount：仅一笔持仓，日期合法，rolls的realized_pnl为有限数，全文不得出现「14%」。tests/test_ic_strategy.py的TestPageCopy把当前positions、rolls、d_realized_net_pnl写死，数字变了只改这些期望，与holdings.json同一提交。只stage这两处，推main后一句话说明改了什么。除此之外不改持仓、不下单。
- IC策略信号变化提醒：ic-strategy.yml按下方「期指自动更新闭环」收盘补查，仅写data/ic_strategy.json；原17:40与20:10保留。20:30检，窗口至21:30。读latest对应days最后一条：leg_a、leg_b与b_open（leg_b为2则趋势打开）、leg_c、leg_d、total。公式用sum_text（负号在字母前，如A2 + B0 − C2 + D0 = 4）。target只是A+B+C底仓，不单独当变化项。与仓库外状态目录上次已通知的值对比。任一项变化才提醒；无变化、文件未更新、信号日未推进则静默。提醒写信号日、sum_text、相对holdings.json的positions[0].lots应加或减几手（策略侧只有多；持仓方向以direction为准）、变化原因一句，并附https://kakarottoisme.github.io/national-team-octagon/futures-preview.html 。保证金按12%，总手数上限10，D上限4。只提醒，不下单、不改持仓。首次只把当日这几项写入状态作基线，不提醒。每日最多一条，错误每日去重。

## 期指自动更新闭环（2026-11-20追加授权）
- 前台：只对期指页增加可见时每60秒重读；dashboard在切回Tab、浏览器恢复可见时发可见消息并立即重读。隐藏页暂停，不重载iframe；并发刷新互斥，失败保留原数据及日期，明确历史选择不跳回最新。
- 后台：复用 `ic-strategy.yml` 与 `preview-data.yml`，北京15:35、15:45、15:55及16:05–17:55每10分钟补查。两条均20:10/22:10/23:30兜底；IC额外保留原17:40。GitHub schedule可能延迟，不能宣称官方发布后秒级同步。
- 期指会员：`python3 futures_close_update.py --mode members` 独立拉CFFEX四CSV并严格验交易日，只增量缺失品种日；某源失败/未发布不覆盖，其他品种可独立完成。已经发布当天跳过。无需SSE/全球/成交额源。23:30保留原全量采集路径；手动默认仅期指，`full_refresh=true`才跑全量。
- IC信号：`python3 futures_close_update.py --mode strategy` 同日指数日K与官方今收盘全部齐才调用原引擎候选。核验日期、250日均线、四挂牌今收盘/贴水及上次发布状态，仅追加新日，原历史逐条保持。已是当天不重算、不改updated、不提交。缺源等后续补查；旧状态不一致停止并留原文件，不自动重录历史。
- 2026-11-24授权修复IC收盘跨日迟到：只调整 `futures_close_update.py --mode strategy` 和既有 `ic-strategy.yml` 的采集/验收链，不改原schedule、其他任务或策略公式。目标为北京时间最近已收盘官方交易日（15:30前向前找，周末/长假也按官方日历）；迟到凌晨/上午仍补前日，不拿执行当天未收盘日K当收盘。仅追加缺失信号，每个新增日同日指数、250日均线与四挂牌官方今收盘逐一校验，指数缺交易日或上次状态不一致停止，旧历史不改。checkout显式main；源pending返回非零且不继续旧数据发布验收。成功必须有expected data_date，latest与days最后一条一致、数据日期达标，推送后Pages逐字一致才验收，不以绿色进程/旧字节一致冒充新数据。已有目标日不重算、不空提交；2026-11-23缺日按真实源补齐。
- 发布闭环：两条工作流以 `pages: write` 仅请求既有分支Pages构建，不改设置。`futures_pages_sync.py` 比较线上对应JSON与采集文件；不一致时请求 `/pages/builds`，最多等300秒再逐字核对，失败报错不假称上线；一致不重建。手动 `verify_publish=true` 用于实跑构建权限烟测，默认false。理由：GITHUB_TOKEN推送不自动触发Pages构建，官方文档 https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site 。
- 两条沿用各自concurrency和显式git stage，不写 `holdings.json`，不改策略公式、选约规则、live、归档、两融或原六项豆包任务。未确认年度交易日日历关闭；休市静默。

## 云端运行
- GitHub推送需云端gh已登录且git有写权限，不复制token、Cookie或认证文件进仓库或任务指令。
- `bash run_ashare_task.sh {ic|archive|margin|data-audit|live-health|live-recover|ic-signal} /home/user/Doubao/ashare-task-state` 在全新临时checkout执行；持久去重状态在仓库外，数据写入只显式stage授权路径。
- 回传标准JSON：notify=false时完全不发用户消息；notify=true时仅message，若含link经交付工具呈现，sources用于正文引用。运行失败不输出堆栈/凭证，只简短原因。
- 数据采集/告警执行留痕：`/home/user/Doubao/ashare-task-state/{archive,margin,data-audit}.json` 保存last_attempt，对应`*-attempts.jsonl`追加时间、status、data_date、原因和是否告警；不存凭证。成功标记不替代GitHub正式JSON。`run_ashare_task.sh data-audit`只读仓库，`--final`标记最后补查，不能用于越窗强行采集。
- 同一归档/两融执行有锁；无空提交、无force push；推送失败保留本地提交，冲突不自动覆盖。后续执行先核对远端是否已有成功记录，不能重试状态未知写入。
- 旧Refresh IC close signal与官方预览工作流不由本次任务替换；它们不写上述新任务的数据文件。原Refresh live.json继续负责实时刷新，不再建第二个写live的定时任务。
- `run_ashare_task.sh`接受ic、archive、margin、live-health、live-recover、ic-signal。后两项由`ashare_observers.py`实现；只读仓库数据，`live-recover`仅允许dispatch既有live.yml。首次09:20/12:55检查返回followup_at时，定时执行用Wait等15分钟后重新克隆main执行同入口复核；不在本地抓行情、不建第二个写live任务。半日启动预留状态在请求前持久化，结果不明不重发；运行/排队实例按所有分支检查。IC在20:30检查，当日文件未推进静默；首次仅建立当日基线，与上次通知值比较且同一信号日不重复提醒。仓库外状态分别为live-recover.json与ic-signal.json，正常notify=false不交付消息或链接。

## 2026-11-25更新链路修复（用户确认）
- 期指会员与IC信号都以北京时间最近已收盘官方交易日为目标；迟到到凌晨/上午仍补上一收盘日。会员逐只补最后已发布日后的缺日，并核对最近20自然日内部交易日缺口；已发布日保留原值和原来源，未知年度停止。
- preview-data.yml不改schedule、concurrency或权限，checkout显式main。独立会员采集部分失败时先发布其他已校验品种，最后业务验收必须非零；全量源失败不能把整个任务记为成功。四品种目标日、日期证据、原CSV SHA256与Pages逐字一致都达标才算成功，不能只验旧文件字节。
- 定时archive与archive.py共用指数合并函数，优先读取同日已收盘live缓存，必要时只在原实时授权窗补取；七ETF完整、按原精度重算摘要一致、七指数原生1分钟完整才写。不改分钟阈值或共享live解析。
- data-audit仍只读：除余额目标日及六指数外，校验当天七ETF分钟/摘要与七指数分钟完整，并核对正式Pages三个JSON逐字一致。存档状态标记或文件存在不再替代完整性校验。
- 本轮人工修复允许仅为11-24/11-25已有ETF档增加遗漏的指数层，来源限原Git完整收盘快照；已有ETF数组、摘要、其他历史逐条不变，保留原Git提交及SHA256。自动入口仍不覆盖任何已有档，若已存档不完整则明确失败等待人工修复。
- 全球源仍沿既有全量任务执行；SSE失败不再在调用全球源前提前退出。全球部分源失败保留原日期和空值、如实返回partial，不用“已有4个指标”冒充全源正常；不新增全球定时任务或改指标口径。
- 六个豆包任务时间、标题、启停、通知阈值及策略/持仓/前端不变；正常、恢复成功、无变化完全静默，最终错误仍每日去重。

## 2026-11-27 全Tab静默自检与受限自恢复（本节覆盖旧监控条款）

- 用户授权全Tab防停更，不下单、不改账户/持仓/ABCD/择约或研究策略参数。采集公式和来源日期证据不变。正常、排队、补跑、恢复及最终未恢复均不发送用户消息，详细状态留仓库外日志；认证需要用户介入时不得绕过权限。
- 仅保留两类监控：复用旧盘中停更监控为 `watch-intraday`，启动节点08:30、09:20、12:55、15:40、20:20、22:20、23:40；上午/下午同次任务内部每5分钟检查至11:35/15:05，收盘同次每10分钟检查至18:00（包含17:20市场顶底节点）。复用旧盘中刷新自恢复为 `watch-daily`，周二至周六00:40检查前一自然日对应官方交易日，日更最后00:10采集节点后一次统查；休市不扫旧周五。两类共用一个状态与锁，不另外建各Tab监控。
- 检查实际数据日期、源分钟、最近内部缺口、同日覆盖、源发布日和正式Pages文件，不以任务中心“执行成功”或JSON生成时间冒充新数据。美国市场用来源已完成日K，周/月指标不能按A股日历强求每日新值。全球40项目录的link_only不当缺数。
- 既有live/期指/IC/市场顶底/份额工作流失效时，先查所有分支queued/in_progress/waiting/pending/requested，再按工作流+节点在仓库外预留，最多补dispatch一次。结果不明不重发、不取消已有实例。接受dispatch不是恢复完成；同次followup与后续节点重读main及线上文件确认实际恢复。
- 日更覆盖ETF份额/同日价格/两指数参考/历史概览、全球、前一披露日两融、当天完整收盘归档。缺归档只用不可变Git当日完整快照，双端齐全且摘要一致才追加；缺两融只追加官方同日缺项，最近内部缺口必须全部验证；不覆盖任何有效旧历史。已有坏档记录问题，不截零、不借别日、不自动重写。
- 已入库但Pages落后先重试既有Pages构建并验证字节；不空提交。源暂不可用有限重试，源缺值保留原日期，不伪造。认证/数据结构不明/写入结果未知停止并留痕；需要新增权限或改变投资规则不自动处理。监控任务可按实际日志定位采集/推送/发布故障，必要的采集或发布小修必须保持来源、验证测试、限定相关文件和一次提交发布，不盲目改代码追绿。
- 使用main最新版 `run_ashare_task.sh watch-intraday|watch-daily /home/user/Doubao/ashare-task-state`。每次隔离克隆，clone传输失败有限三次，认证失败停止；日更复用固定akshare环境。`--followup`允许同次复核不新建定时任务。持久 `dashboard-watchdog.json`/`watchdog-runs.jsonl` 包含checked_at、unresolved、预留状态和实际结果，不存凭证。
- 旧live-health/live-recover监控规则由上述两类替代，不再额外运行旧监控循环；两融原09:05采集任务保留，18:05旧重复只读兜底移交日更巡检；IC换仓/信号变化/一日两算业务提醒不改变。
- 全球采集现独立于国内源失败，成功模块独立提交、Pages核验，最终业务失败仍非零。ETF深市已接带size_date官方fund_jjgm；无日期规模快照仍不参与日度估算。份额工作流同步追加同源指数与历史概览，显式Pages构建核验。

## 验收
`python3 -m unittest discover -s tests -p test_ashare_observers.py -v`覆盖18项新观察任务：排队/运行不重复启动、启动前持久预留、半日与每日去重、结果不明不重发、15分钟成功静默/失败提醒、交易窗口、首次基线、六字段变化、负C公式、持仓方向与手数差、无仓库写入。

## 份额与申赎（GitHub Actions，不由豆包再抓一遍）
- 工作流 `etf-share-flows.yml`，名称 Refresh ETF share flows。北京时间周一至周五 23:40（UTC `40 15 * * 1-5`）跑 `python3 etf_share_flows.py`，次日 00:10（UTC `10 16 * * 1-5`）再试一次。只写 `data/etf_share_history.json`、`data/etf_share_flows.json`、`data/etf_szse_snapshot.json`。份额和估算都没变就不提交。
- 沪市份额只接受上交所 `STAT_DATE` 与请求日一致的 `TOT_VOL`。深交所当前规模没有统计日，只追加未核日期快照，不写成交易日，不参与申赎。缺值保持 null。
- 这是全市场申赎估算，不是国家队买卖，也不是八宫格的分钟成交额代理。不改 Refresh live.json 的窗口和写入范围，不另建写 live 的任务。
- `data/etf_official_holdings.json` 不由这个定时任务改。每年 4 月核对年报、9 月核对中期报告，只写入基金原文里具名的持有人。

`python3 -m unittest discover -s tests -p test_ashare_tasks.py -v`
8项离线测试覆盖假日/周末/未知日历、交易窗口、2pp边界、强制换月、最新价非结算价、旧行情拒绝、余额无新数据静默、停更去重、归档失败无写入。前端/账户/IC源与本次起点保持字节一致。
既有check_backend基线在改动前已存在2项失败（Python版本AST差异、另一次期指页面修改未更新旧hash）；本次不篡改基线掩盖问题。
