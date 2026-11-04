# preview-data 国内官方预览 schema（v1.0.0）

> 隔离目录：`preview-tools/`（采集器+测试）、`preview-data/official/`（国内官方）、`preview-data/status.json`。
> 不触碰 `data/`、正式 `*.py`、既有 `live.yml`/`live_daemon.sh`、正式 html。

## 文件
| 文件 | 内容 |
|---|---|
| `preview-tools/collect_preview.py` | 主采集器（stdlib，零依赖） |
| `preview-tools/global_sources.py` | 全球安全包（global agent 维护；本器仅调用） |
| `preview-tools/tests/test_collect.py` | 确定性离线测试（8例） |
| `preview-data/official/snapshot-latest.json` | 当日官方快照 |
| `preview-data/official/history.json` | 逐日累积（可回补） |
| `preview-data/status.json` | 运行健康状态（stdout 友好） |
| `preview-data/global.json` | 全球安全包（由 global_sources 写入） |
| `preview-data/domestic.json` / `global.json`(旧) | **第三方 legacy 快照，仅存档，不用于官方数值** |

## snapshot-latest.json
```jsonc
{
 "schema_version":"1.0.0",
 "generated_at":"ISO8601 +08:00", "collected_at":"ISO8601 +08:00",
 "run_status":"ok|partial|failed",      // 国内ok但全球非连通=partial;SSE全失败=failed
 "as_of":"YYYY-MM-DD",                   // 被官方发布日确认的交易日
 "as_of_confirmed_by":["sse_stat_date"], // +cffex_trading_day 当日期指可得
 "calendar_source":"sse_observed_trading_days",
 "calendar_days":<int>,
 "etfs":[{
   "code":"510300","exch":"SH","core":true,
   "shares_yi":445.8739|null,
   "shares_status":"official_confirmed|pending_unverifiable|stale|unpublished",
   "growth_1d_pct":null,"growth_5d_pct":null,"growth_20d_pct":null,
   "coverage_reason_1d":"...",         // 窗口缺洞/历史不足才填,不拿legacy凑
   "anomaly":"ok|split_pending_await_official_notice",
   "amount_calc":"not_enabled_nav_unverified",
   "snapshot_scale_yi_no_date":<仅SZ,留痕不计算>
 }],
 "futures":{"member":"中信期货","as_of":"YYYY-MM-DD",
   "products":{"IH":{"long":0,"short":0,"net_short":0,
     "observed_contracts":["IH2611"],
     "contracts":[["IH2611",3094,795,5531,900]],  // [合约,多,多增减,空,空增减]
     "note":"仅前20上榜合约;缺席≠0手"}}},
 "sources":[{"name":"SSE|CFFEX|SZSE_snapshot","date":...,"http":...,"rows":...,"status":...}],
 "global":{"state":"ok|degraded_partial|failed_nonblocking|skipped","connected":1,...},
 "errors":[]
}
```

## 口径硬约束
- **份额**：仅沪市(SSE `TOT_VOL` 万份→亿份)日期可确认才入官方；深市(SZSE快照无日期)=`pending_unverifiable`，禁算。
- **增幅%**：以官方交易日历（SSE实际发布日）为准；1/5/20 要求 as_of 往前 n 个交易日**该标的每日均有官方值**，否则 null+coverage_reason；历史不足真实留空。
- **拆合**：单日 |增幅|≥25% → `split_pending`，增幅留空，待基金公告，不自动修。
- **金额**：NAV 未核，本版**只算份额%**，不产生金额（`amount_calc` 标注）。
- **期货**：仅CFFEX前20会员(代客)上榜合约；缺席合约≠0；品种合计=上榜合约多空加总。
- **单源失败**：保留 history 上一有效值标 stale，不造假0；SSE全失败→failed。
- **全球**：独立调用 `global_sources.update_global(output_path=...)`，CLI恒0需读 `connected/ok` 判降级；全球不阻国内但非连通→run_status=partial。

## 运行
- 本地：`python3 preview-tools/collect_preview.py [--date YYYYMMDD]`
- 测试：`python3 -m unittest preview-tools.tests.test_collect -v`
- 工作流：`.github/workflows/preview-data.yml`（最小权限，仅 `git add preview-data/`，无 secret；schedule 北京23:30 + workflow_dispatch；真正发布后由前端 agent 触发）。
