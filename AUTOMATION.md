# A股自动任务

本次授权仅新增观察提醒及指定JSON更新，不下单、不改持仓、不改IC策略和前端。全部北京时间，交易所节假日与周末静默。2026采用已核实上交所休市表；后续年份在线读取年度表，未确认/解析不完整则停止，不能把工作日当交易日。

## 时间与输出
- IC换仓：14:44触发，执行必须仍在14:42–14:47。先读取main的data/holdings.json。新浪sh000905和nf_IC同日、180秒内实时最新价；当月/次月/当季合约去重，剩余不到10天不候选。到期按合约月第三个周五；年化贴水=(指数−期货)/指数×365/剩余日历天。新合约较持仓高至少2pp或持仓不足10天/到期才提醒，价格/贴水/差值/剩余天数齐全，提醒14:57前平旧开新、索取两笔成交价。不触发静默。当前合约价格缺失不得拿开仓价替代。
- 八宫格归档：15:12执行，15:22补试，授权窗口15:10–15:30。腾讯七ETF同日完整分钟累计额差分，严格每分钟>1亿元计入，沿用既有精度。单源HTTP临时失败、收盘分钟尚未齐全或累计额临时回补异常时，最多重读3次（间隔15/30秒）；每次整包重新校验，不删坏点、不截零、不沿用旧数据，认证/权限失败不重试。上证同日收盘及前日基准必须齐全。仅写data/history.json及data/minutes/日期.json，同一提交推main；成功日期/估算亿元/上证涨跌幅及原页面链接一句话。重复或无变化不提交、不通知；错误每日去重。
- 两融：16:35启动，同一次任务内部在16:45/55补查，窗口16:30–17:00。固定akshare==1.18.97以兼容既有接口；宏观缓存可能滞后，增量任务不重复全历史抓取，直接保留旧历史并用近期上交所+同日深交所余额补齐。仅两融数据最新交易日比仓库更新时才写data/margin_balance.json；六指数同日收盘必须齐全，旧历史不允许丢失。成功日期/亿元/前一交易日变化及margin.html链接一句话。未公布/无新交易日静默，不因updated时间变化推送。
- 盘中：用户确认接受约5分钟刷新（GitHub首次启动仍可能延迟），仍仅09:15–11:35、12:55–15:05采集。原GitHub Actions改为每半日一个持续运行实例，实例内每300秒采集，不再每轮依赖调度排队；08:53/12:43预热，既有5分钟cron保留为故障重启机会，统一concurrency防止两个实例同时写入。`live_runner.py`复用原`fetch_live.main`向临时文件构建，七ETF必须全部当日、非空且对齐，验证后仅有实质变化才推data/live.json。采集失败保留旧数据，推送结果不明/并发冲突停机并保留本地提交，不强制覆盖。成功不通知。独立健康检查每10分钟观察，至少连续3次异常且已停更20分钟以上才提醒，当日最多一次；午休初段不判停更。
- 盘中刷新自恢复：每个交易日09:20与12:55各检一次。若本半日Refresh live.json没有在运行的实例，或data/live.json在采集窗口（09:15–11:35、12:55–15:05）内停更超过15分钟，可手动workflow_dispatch启动该工作流一次。同一半日最多1次；启动前确认没有正在运行或排队的实例，遵守concurrency组refresh-live-json。恢复成功不通知。手动启动后15分钟仍未更新才提醒一次，当日去重。这是「独立健康检查只观察」的例外：只允许启动这一个工作流，不改YAML、不停用、不另建写live的任务。
- 成交后更新持仓：主人在对话里明确给出成交后，才可写data/holdings.json，且只能写这一份。必填positions[0]：contract（IC加四位年月）、direction（多或空）、lots（整数0–100）、entry_price（>0）、entry_date（YYYY-MM-DD）。换仓还要旧合约平仓价和新合约开仓价，追加rolls一项：date、from、to、lots、direction、close_price、open_price、realized_pnl。多头已实现=(close_price−原entry_price)×lots×200，空头符号相反，并累加到d_realized_net_pnl；as_of改为成交日。account_balance与balance_as_of只用主人报的数，不推算。缺任一项先问，不猜。成交价不写入last_price、last_date、last_price_kind，这三项只保留收盘或结算。校验同futures-preview.html的validateLocalAccount：仅一笔持仓，日期合法，rolls的realized_pnl为有限数，全文不得出现「14%」。tests/test_ic_strategy.py的TestPageCopy把当前positions、rolls、d_realized_net_pnl写死，数字变了只改这些期望，与holdings.json同一提交。只stage这两处，推main后一句话说明改了什么。除此之外不改持仓、不下单。
- IC策略信号变化提醒：ic-strategy.yml按下方「期指自动更新闭环」收盘补查，仅写data/ic_strategy.json；原17:40与20:10保留。20:30检，窗口至21:30。读latest对应days最后一条：leg_a、leg_b与b_open（leg_b为2则趋势打开）、leg_c、leg_d、total。公式用sum_text（负号在字母前，如A2 + B0 − C2 + D0 = 4）。target只是A+B+C底仓，不单独当变化项。与仓库外状态目录上次已通知的值对比。任一项变化才提醒；无变化、文件未更新、信号日未推进则静默。提醒写信号日、sum_text、相对holdings.json的positions[0].lots应加或减几手（策略侧只有多；持仓方向以direction为准）、变化原因一句，并附https://kakarottoisme.github.io/national-team-octagon/futures-preview.html 。保证金按12%，总手数上限10，D上限4。只提醒，不下单、不改持仓。首次只把当日这几项写入状态作基线，不提醒。每日最多一条，错误每日去重。

## 期指自动更新闭环（2026-11-20追加授权）
- 前台：只对期指页增加可见时每60秒重读；dashboard在切回Tab、浏览器恢复可见时发可见消息并立即重读。隐藏页暂停，不重载iframe；并发刷新互斥，失败保留原数据及日期，明确历史选择不跳回最新。
- 后台：复用 `ic-strategy.yml` 与 `preview-data.yml`，北京15:35、15:45、15:55及16:05–17:55每10分钟补查。两条均20:10/22:10/23:30兜底；IC额外保留原17:40。GitHub schedule可能延迟，不能宣称官方发布后秒级同步。
- 期指会员：`python3 futures_close_update.py --mode members` 独立拉CFFEX四CSV并严格验交易日，只增量缺失品种日；某源失败/未发布不覆盖，其他品种可独立完成。已经发布当天跳过。无需SSE/全球/成交额源。23:30保留原全量采集路径；手动默认仅期指，`full_refresh=true`才跑全量。
- IC信号：`python3 futures_close_update.py --mode strategy` 同日指数日K与官方今收盘全部齐才调用原引擎候选。核验日期、250日均线、四挂牌今收盘/贴水及上次发布状态，仅追加新日，原历史逐条保持。已是当天不重算、不改updated、不提交。缺源等后续补查；旧状态不一致停止并留原文件，不自动重录历史。
- 发布闭环：两条工作流以 `pages: write` 仅请求既有分支Pages构建，不改设置。`futures_pages_sync.py` 比较线上对应JSON与采集文件；不一致时请求 `/pages/builds`，最多等300秒再逐字核对，失败报错不假称上线；一致不重建。手动 `verify_publish=true` 用于实跑构建权限烟测，默认false。理由：GITHUB_TOKEN推送不自动触发Pages构建，官方文档 https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site 。
- 两条沿用各自concurrency和显式git stage，不写 `holdings.json`，不改策略公式、选约规则、live、归档、两融或原六项豆包任务。未确认年度交易日日历关闭；休市静默。

## 云端运行
- GitHub推送需云端gh已登录且git有写权限，不复制token、Cookie或认证文件进仓库或任务指令。
- `bash run_ashare_task.sh {ic|archive|margin|live-health|live-recover|ic-signal} /home/user/Doubao/ashare-task-state` 在全新临时checkout执行；持久去重状态在仓库外，数据写入只显式stage授权路径。
- 回传标准JSON：notify=false时完全不发用户消息；notify=true时仅message，若含link经交付工具呈现，sources用于正文引用。运行失败不输出堆栈/凭证，只简短原因。
- 同一归档/两融执行有锁；无空提交、无force push；推送失败保留本地提交，冲突不自动覆盖。后续执行先核对远端是否已有成功记录，不能重试状态未知写入。
- 旧Refresh IC close signal与官方预览工作流不由本次任务替换；它们不写上述新任务的数据文件。原Refresh live.json继续负责实时刷新，不再建第二个写live的定时任务。
- `run_ashare_task.sh`接受ic、archive、margin、live-health、live-recover、ic-signal。后两项由`ashare_observers.py`实现；只读仓库数据，`live-recover`仅允许dispatch既有live.yml。首次09:20/12:55检查返回followup_at时，定时执行用Wait等15分钟后重新克隆main执行同入口复核；不在本地抓行情、不建第二个写live任务。半日启动预留状态在请求前持久化，结果不明不重发；运行/排队实例按所有分支检查。IC在20:30检查，当日文件未推进静默；首次仅建立当日基线，与上次通知值比较且同一信号日不重复提醒。仓库外状态分别为live-recover.json与ic-signal.json，正常notify=false不交付消息或链接。

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
