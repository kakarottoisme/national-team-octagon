/* Only the current-signal and estimated-holdings host. Member/ranking modules are untouched. */
(() => {
'use strict';
const host=document.getElementById('icDynamic'), stamp=document.getElementById('icmoStamp');let PACK=null,REC=null,liveEpoch=0;
const esc=window.uiEsc,n=(v,d=0)=>v==null||!Number.isFinite(+v)?'—':(+v).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d});
const money=v=>v==null?'—':n(v/10000,2)+' 万';const pct=v=>v==null?'—':n(v*100,1)+'%';const qty=p=>Object.values(p||{}).reduce((a,b)=>a+b,0);
const local=()=>{const p=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',dateStyle:'short',timeStyle:'medium',hour12:false}).format(new Date());return {date:p.slice(0,10),time:p.slice(11,19)}};
const b=(title,value='',note='')=>`<div class="blk"><div class="k">${title}</div>${value?`<div class="v">${value}</div>`:''}${note?`<div class="s">${note}</div>`:''}</div>`;
const leg=(name,value,body)=>`<article class="leg"><div class="leg-top"><span>${name}</span><div><b>${value}</b><small>手</small></div></div>${body}</article>`;
const formula=r=>`A${r.a} + B${r.b}${r.c<0?' − C'+Math.abs(r.c):' + C'+r.c} + D${r.d}`;
const grid=[{max:5000,label:'≤ 5,000',lots:10},{max:5250,label:'5,000 &lt; 指数 ≤ 5,250',lots:8},{max:5500,label:'5,250 &lt; 指数 ≤ 5,500',lots:6},{max:6000,label:'5,500 &lt; 指数 ≤ 6,000',lots:4},{max:6500,label:'6,000 &lt; 指数 ≤ 6,500',lots:2},{max:Infinity,label:'&gt; 6,500',lots:2}];
const aIndex=p=>grid.findIndex(g=>p<=g.max);const aLots=p=>grid[aIndex(p)].lots;
function effectiveDate(s){return s?.snapshot?.date||null;}
function selectRecord(p,clock){
 const close=p.close,preview=p.preview;
 const isToday=preview?.snapshot?.date===clock.date&&clock.time>='14:45:00'&&preview.published_at<=`${clock.date}T${clock.time}+08:00`;
 if(close?.snapshot?.date===clock.date&&clock.time>='15:00:00')return {record:close,phase:'close'};
 if(clock.time>='15:00:00')return {record:isToday?preview:close,phase:effectiveDate(close)===clock.date?'close':'waiting'};
 return {record:isToday?preview:close,phase:isToday?'preview':'before'};
}
function targetAt(r,spot){
 const p=r.snapshot,base=Math.max(0,Math.min(10,aLots(spot)+r.b+r.c));let d=r.d;if(spot<p.ma250*.99||base===0)d=0;d=Math.min(d,4,10-base);
 const maxLev=Math.floor(r.equity*2/(p.futures[r.ic_code]?.close*200));return Math.max(0,Math.min(base+d,r.cool?0:10,maxLev));
}
function nextA(r){const idx=aIndex(r.snapshot.index500);const down=grid.slice(0,idx).reverse().find(g=>g.lots!==r.a);const j=grid.findIndex((g,k)=>k>idx&&g.lots!==r.a);return [down?`≤ ${n(down.max)} → A${down.lots} / IC ${targetAt(r,down.max)} 手`:'',j>=0?`&gt; ${n(grid[j-1].max)} → A${grid[j].lots} / IC ${targetAt(r,grid[j-1].max+.0001)} 手`:''].filter(Boolean).join('<br>')||'无新的价格档位变化';}
function nextB(r){
 const p=r.snapshot,h=PACK.index_history||[],same=h.at(-1)?.date===p.date;const last= h.slice(-250);const base=last.reduce((s,x)=>s+x.close,0)-(same?last[0]?.close:0);const upper=same?1.01*base/(250-1.01):p.ma250*1.01;const lower=same?.99*base/(250-.99):p.ma250*.99;
 return {upper,lower};
}
function moThreshold(r){
 const pk=r.put?.code,p=r.snapshot,held=r.mo_target?.[pk]||0,q=p.options[pk];if(!q||!r.ic_lots)return '暂无可比 Put 或 IC 空仓';
 const den=r.put.signal_nominal*.75/(p.index1000*100),max=r.put.capped??0;let up=held+1;while(up-held<Math.max(1,Math.floor(up*.1)))up++;
 const min=Math.max(1,Math.floor(held*.1)), inc=den/up,dec=den/(held-min+1);
 const rows=[];if(up<=60&&inc>=.1&&inc<=1)rows.push(`|Delta| ≤ 约 ${n(inc,4)} → 开始补保`);if(held>=min&&dec<1)rows.push(`|Delta| &gt; 约 ${n(dec,4)} → 开始减保`);
 const nextGrid=grid.slice(0,aIndex(p.index500)).reverse().find(g=>g.lots!==r.a);let joint='';
 if(nextGrid){const numIC=targetAt(r,nextGrid.max),unit=p.futures[r.ic_code]?.close;const raw=Math.floor(numIC*unit*200*.75/(p.index1000*100*Math.max(Math.abs(q.delta),.1)));joint=`<br>若中证500 ≤ ${n(nextGrid.max)}，IC 参考目标 ${numIC} 手 → Put 理论 ${raw} 张（资金/容量约束前）`;}
 return (rows.join('<br>')||'数量受预算或阈值限制')+joint+'<br>仅当合约、IC 名义额与资金限制不变；跨线后以重算数量为准';
}
function icCard(r,phase){
 const p=r.snapshot,g=aIndex(p.index500),ci=r.cinfo||{},tb=nextB(r),next=nextC(p);const model=qty(r.ic_target);const aTable=`<table class="lv"><tbody>${grid.map((x,i)=>`<tr${i===g?' class="on"':''}><td>${x.label}</td><td>${x.lots} 手</td></tr>`).join('')}</tbody></table>`;
 const note=r.delta_kind==='implied-intraday'?'盘中预判':'收盘正式';
 return `<section class="model icmo-model"><header class="model-h"><div class="model-total"><span class="k">IC 目标</span><b>${model}</b><small>手</small></div><div class="model-formula">${formula(r)}${r.model!==model?` · 模型 ${r.model} → 最终 ${model} 手`:''}</div><div class="model-meta">${esc(r.ic_code)} · ${note}</div></header><div class="leg-row icmo-legs">
 ${leg('A 网格',r.a,b('规则',aTable)+b('现状',n(p.index500,3))+b('下次变化','',nextA(r)))}
 ${leg('B 趋势',r.b,b('规则','','站上 MA250 × 1.01 → 2 手<br>跌破 MA250 × 0.99 → 0 手<br>两线之间（含边界）沿用')+b('现状',p.index500>p.ma250*1.01?'站上上轨 → 2 手':p.index500<p.ma250*.99?'低于下轨 → 0 手':'区间内，沿用原状态',`MA250 ${n(p.ma250,2)}<br>上轨 ${n(p.ma250*1.01,2)} · 下轨 ${n(p.ma250*.99,2)}`)+b('下次变化','',r.b?`下次收盘低于 ${n(tb.lower,2)} → B0`:`下次收盘高于 ${n(tb.upper,2)} → B2`))}
 ${leg('C 换月贴水',r.c<0?'减 '+Math.abs(r.c):r.c,b('规则','','到期换月判新近月年化贴水<br>≥10% 加2；&lt;3% 减2；其余0<br>判完保持至下次换月')+b('现状',pct(ci.rate),`${esc(ci.date||'未有判定')} · ${esc(ci.code||'—')} · ${r.c>0?'达到10%，加2手':r.c<0?'低于3%，减2手':'不加不减'}`)+b('下次变化','',next))}
 ${leg('D 盈利再投',r.d,b('规则','','重置后期货盈利的50%够一手12%估算保证金就加1手，最多4手<br>底仓为0或跌破下轨即清零')+b('现状',p.index500<p.ma250*.99?'低于下轨，D 清零':r.d?'盈利支持加仓':'盈利不足加仓',`期货累计盈利 ${money(r.d_pnl)} · 不含 Put`)+b('下次变化','',r.cool?'回撤冷却中':p.index500<p.ma250*.99?`先满足指数 ≥ ${n(tb.lower,2)}，再看盈利门槛`:r.d_need==null?'已达上限':`再赚 ${money(r.d_need)} → D 加 1 手`))}</div></section>`;
}
function nextC(p){
 const f=Object.keys(p.futures).sort();const near=p.futures[f[0]],nxt=f[1];return `${near?.expiry||'日期待确认'} 到期换月 · 新近月 ${esc(nxt||'—')}<br>换月前不按盘中贴水改变 C`;
}
function basis(p,code){const q=p.futures?.[code];return q&&q.days>0&&q.close>0?(p.index500-q.close)/p.index500*365/q.days:null;}
function freshPut(p){
 const rows=[];for(const [code,q] of Object.entries(p.options||{})){
  if(!code.includes('-P-')||!(q.close>0)||q.volume<500||q.oi<100||q.delta==null||Math.abs(q.delta)<.05||Math.abs(q.delta)>.98||q.days<15||q.days>90||Math.abs(q.strike/p.index1000-.95)>.04)continue;
  rows.push({code,q,score:Math.abs(q.days-45)/45+Math.abs(q.strike/p.index1000-.95)/.025});
 }return rows.sort((a,b)=>a.score-b.score||b.q.volume-a.q.volume||a.code.localeCompare(b.code))[0]||null;
}
function orderReason(t,r){
 if(t.asset!=='future'){
  if(t.tag==='delta_trim')return `按最新 IC 名义额与 Put Delta 重算，目标共 ${qty(r.mo_target)} 张，不是新买 ${qty(r.mo_target)} 张`;
  if(t.tag==='put_buy')return `补足保护差额；本笔权利金参考 ${money(Math.abs(t.quantity)*(r.snapshot.options[t.code]?.close||t.price)*100)}（费用另计）`;
  return reason(t.tag);
 }
 const before=Object.keys(PACK.state.futures||{}),old=before[0],next=Object.keys(r.ic_target||{})[0],oldLots=qty(PACK.state.futures),newLots=qty(r.ic_target),pieces=[];
 if(old&&next&&old!==next){const ob=basis(r.snapshot,old),nb=basis(r.snapshot,next);
  if(ob!=null&&nb!=null){const gap=nb-ob;pieces.push(r.snapshot.futures[old].days<=3?'剩余不超过 3 天，强制换月':`主动切换：${esc(next)} 年化贴水 ${pct(nb)}，${esc(old)} ${pct(ob)}，优势 ${n(gap*100,2)} 个百分点 ≥ 3 个百分点`);}
  else pieces.push('按已发布合约选择结果换约，贴水对比待核验');
 }
 if(oldLots!==newLots){pieces.push(`总仓 ${oldLots} → ${newLots} 手；${formula(r)}`);const prevSpot=(PACK.index_history||[]).filter(z=>z.date<r.snapshot.date).at(-1)?.close;if(oldLots>newLots&&prevSpot!=null&&aLots(prevSpot)>r.a)pieces.push(`A 网格由 ${aLots(prevSpot)} 降到 ${r.a} 手：中证500 ${n(prevSpot,3)} → ${n(r.snapshot.index500,3)}`);if(r.model!==newLots)pieces.push(`模型 ${r.model} 手被风控压至 ${newLots} 手：${esc((r.risk_reason||[]).join('、')||'资金或敞口限制')}`);}
 return pieces.join('；')||`维持合约、调整手数：${formula(r)}`;
}
function putCosts(r){
 const p=r.snapshot,targets=Object.entries(r.mo_target||{}),old=PACK.state.options||{},adds=(r.trades||[]).filter(t=>t.asset==='option'&&t.quantity>0),cuts=(r.trades||[]).filter(t=>t.asset==='option'&&t.quantity<0&&t.tag!=='expiry_cash');
 const buys=adds.reduce((a,t)=>a+t.quantity*(p.options[t.code]?.close??t.price)*100,0),sales=cuts.reduce((a,t)=>a+Math.abs(t.quantity)*(p.options[t.code]?.close??t.price)*100,0);
 let html=b('本次买卖现金',adds.length?`新买 ${adds.reduce((a,t)=>a+t.quantity,0)} 张 · 付 ${money(buys)}`:`本次无需买入 · 新增支出 0`,cuts.length?`计划卖平 ${cuts.reduce((a,t)=>a+Math.abs(t.quantity),0)} 张，回收约 ${money(sales)}，不是重新支付全仓权利金`:'新建、加仓才支付对应新增权利金；费用另计');
 const cashBlock=html;html='';
 for(const [code,count] of targets){const q=p.options[code];if(!q||q.close==null)continue;const paid=q.close*100*count,intr=Math.max(q.strike-p.index1000,0)*100*count,time=paid-intr;
  html+=b('持有旧约 ≠ 今天新建',`${esc(code)} · 当前参考市值 ${money(paid)}`,`${count} 张 × ${n(q.close,1)} 点 × 100 元；${old[code]?'模拟账本已有该约，只按差额调整':'新建时需支付权利金'}<br>剩余 ${q.days} 天 · ${q.strike>p.index1000?'目前为实值，不是现在的 5% 虚值新约':'目前为虚值'}`);
  html+=b('到期损失情景','',`若到期交割结算指数 ≥ ${n(q.strike)}，届时该 Put 价值归零；从当前价格计全仓可损失 ${money(paid)}<br>若交割结算指数恰为当前 ${n(p.index1000,3)}，按静态到期计算损失约 ${money(time)}；到期价值约 ${money(intr)}。实际滚动、波动和成交会改变路径。`);
 }
 const fresh=freshPut(p);if(fresh){const code=r.ic_code,fq=p.futures[code],nom=(fq?.close||0)*200*qty(r.ic_target),q=fresh.q,raw=Math.floor(nom*.75/(p.index1000*100*Math.max(Math.abs(q.delta),.1))),cash=raw*q.close*100;
  html+=b('如果现在没有 Put：新建参考',esc(fresh.code),`剩余 ${q.days} 天 · 虚值 ${n((1-q.strike/p.index1000)*100,2)}% · ${n(q.close,1)} 点（每张 ${money(q.close*100)}）<br>按当前 IC 目标，理论 ${raw} 张需约 ${money(cash)}，未经过你实际资金与仓位检查；不是当前账本的加仓指令。`);
 }
 return cashBlock+`<details><summary>查看保费、到期损失与空仓新建参考</summary>${html}<div class="hold-note">75% 是当时的名义 Delta 覆盖，不是保证补偿 IC 损失的 75%；IC 跟踪中证500，MO 跟踪中证1000，存在基差风险。贴水不是当月确定现金收益，不能保证覆盖滚动保险费。</div></details>`;
}
function moCard(r){const pi=r.put||{},p=r.snapshot,targets=Object.entries(r.mo_target||{}),sum=qty(r.mo_target),held=qty(PACK.state.options),code=pi.code,q=p.options[code],cur=projectExpected(r)?.coverage;
 return `<section class="model icmo-model"><header class="model-h"><div class="model-total"><span class="k">MO Put 目标</span><b>${sum}</b><small>张</small></div><div class="model-meta">${targets.map(([k,n])=>esc(k)+' × '+n).join('<br>')||'空仓'}</div></header><div class="icmo-put-body">${b('规则','45 天 · 5% 虚值 · Delta 目标 75%','持续持有 IC 时配置保护，不是等预测到下跌才买；合格旧约继续持有，剩余不超过10天滚动。')}${b('现状',`调整前 ${held} → 操作后目标 ${sum} 张`,q?`Delta ${n(q.delta,4)} · ${r.delta_kind==='official'?'官方收盘':'盘中隐波反解估算'}<br>操作后估算覆盖 ${pct(cur)}（75% 为理论目标，张数取整及预算可能使实际覆盖不足）`:esc(pi.reason||'IC 空仓，不新增 Put'))}${putCosts(r)}${b('下次变化','',q?moThreshold(r):'等待合格 Put；缺价不补造')}</div></section>`;
}
function orderTable(r,phase){const trades=r.trades||[],timed=phase==='before'||phase==='waiting';return `<div class="current-actions"><div class="current-actions-head"><h3>操作计划</h3><p>${phase==='close'?'下一个交易时段的剩余模型差额':phase==='preview'?'盘中预判 · 若实际持仓不同请重算':phase==='waiting'?'正式数据未齐，保留旧时点参考':'沿用上一交易日正式结果，先按规则观察'} · 相对模型调整前持仓，不是已核验真实账户</p></div>${trades.length?`<div class="tbx icmo-table"><table class="wrap-cells"><thead><tr><th>合约</th><th>操作 / 数量</th><th>调整前 → 操作后</th><th>原因</th></tr></thead><tbody>${trades.map(t=>{const prior=(t.asset==='future'?PACK.state.futures:PACK.state.options)[t.code]||0;return `<tr><td>${esc(t.code)}</td><td><b>${t.quantity>0?'买开':'卖平'} ${t.asset==='future'?'IC':'Put'} ${Math.abs(t.quantity)} ${t.asset==='future'?'手':'张'}</b></td><td>${prior} → ${prior+t.quantity}</td><td>${esc(orderReason(t,r))}</td></tr>`}).join('')}</tbody></table></div>`:'<div class="foot">模拟持仓已到目标，或暂无合格可买合约；不把缺数据当零仓。</div>'}<div class="hold-note">${r.cool?'回撤冷却。':''}${r.delta_kind==='implied-intraday'?'盘中 Delta 由真实权利金反解，非官方实时报价；收盘用官方 Delta 再核对。 ':''}${r.order_note}。盘中真实成交未回填时，不重复照单下单。</div></div>`;}
const reasons={current_plan:'本次建议操作（按快照价估算）',signal_or_risk:'IC 信号或资金约束',put_roll_or_flat:'到期滚动 / IC 归零',delta_trim:'Delta 减保',put_buy:'补足保护',put_value_cap:'单手保费上限',exposure_cap_reduce:'敞口上限',emergency_delever:'压力现金减仓',expiry_cash:'到期现金结算'};const reason=k=>reasons[k]||k;
function projectExpected(r){
 const e=JSON.parse(JSON.stringify(r.expected||{ic:[],mo:[]})),p=r.snapshot,all={};
 for(const z of [...e.ic,...e.mo])all[z.code]=JSON.parse(JSON.stringify(z.batches||[]));
 for(const t of r.trades||[]){let rows=all[t.code]||(all[t.code]=[]);if(t.quantity>0){const q=(t.asset==='future'?p.futures:p.options)[t.code],price=q?.close??t.price;rows.push({date:r.valid_for||p.date,qty:t.quantity,price,source:'current_plan'});}else{let left=-t.quantity;while(left>0&&rows.length){const z=rows[0],n0=Math.min(z.qty,left);z.qty-=n0;left-=n0;if(!z.qty)rows.shift();}if(left)all[t.code]=[];}}
 const out={ic:[],mo:[],state_date:p.date,asof:p.asof,projected:true};let fn=0,on=0;let unknown=false;
 for(const [type,targets] of [['ic',r.ic_target||{}],['mo',r.mo_target||{}]])for(const [code,qty0] of Object.entries(targets)){
  if(!qty0)continue;const bs=all[code]||[],q=(type==='ic'?p.futures:p.options)[code],known=bs.reduce((a,z)=>a+z.qty,0)===qty0&&bs.every(z=>Number.isFinite(z.price));
  const avg=known?bs.reduce((a,z)=>a+z.qty*z.price,0)/qty0:null,mark=q?.close??null,mult=type==='ic'?200:100,z={code,qty:qty0,batches:bs,average:avg,mark,cost_known:known,unrealized:avg!=null&&mark!=null?(mark-avg)*qty0*mult:null,days:q?.days,expiry:q?.expiry,last_change:bs.at(-1)?.date,delta:q?.delta};
  if(type==='ic'){z.margin=mark!=null?qty0*mark*200*.15:null;if(mark!=null)fn+=qty0*mark*200;else unknown=true;}
  else{z.value=mark!=null?qty0*mark*100:null;if(q?.delta!=null)on+=qty0*Math.abs(q.delta)*p.index1000*100;else unknown=true;}
  out[type].push(z);
 }
 out.coverage=!unknown&&fn>0?on/fn:null;return out;
}
function expected(r){const e=projectExpected(r),ic=e.ic||[],mo=e.mo||[];const k=(title,value,note)=>`<div class="kpi"><div class="lb">${title}</div><div class="vl">${value}</div><div class="ft">${note}</div></div>`;
 const sum=(rows,key)=>rows.some(z=>z[key]==null)?null:rows.reduce((a,z)=>a+z[key],0);const single=(rows,key,d=2)=>rows.length===1?n(rows[0][key],d):rows.length?'分合约见下表':'—';
 const contracts=rows=>rows.map(z=>`${esc(z.code)}<small>${z.qty} ${z.code.startsWith('IC')?'手':'张'}</small>`).join('<br>')||'空仓';
 const pane=(type,rows)=>`<section class="panel estimate-card"><div class="kpis estimate-kpis">${k(type==='IC'?'IC 预计合约 / 手数':'MO 预计合约 / 张数',contracts(rows),'假设已按当前建议完成操作')}${k(type==='IC'?'剩余持仓均价':'剩余权利金均价',single(rows,'average'),'剩余批次加权，不含已平批次')}${k(type==='IC'?'本合约参考价格':'本合约参考权利金',single(rows,'mark',1),`${esc(r.snapshot.date)} · ${r.delta_kind==='official'?'收盘':'盘中快照'}`)}${k('持仓损益（估算）',money(sum(rows,'unrealized')),'价格差 × 数量 × '+(type==='IC'?200:100)+'，不等于账户当日浮盈')}${k(type==='IC'?'保证金（15%估算）':'期权市值',money(sum(rows,type==='IC'?'margin':'value')),type==='IC'?'逐合约估算':'买入 Put 已付权利金，不再计卖方保证金')}${k(type==='IC'?'最近建议变动':'操作后 Delta 覆盖',type==='IC'?esc((r.trades||[]).filter(t=>t.asset==='future').at(-1)?.date||PACK.state.date):pct(e.coverage),'按当前建议假设执行 · 未写入真实账本')}</div></section>`;
 const rows=[...ic,...mo].flatMap(x=>(x.batches||[]).map(z=>`<tr><td>${esc(x.code)}</td><td>${esc(z.date)}</td><td>${z.qty} ${x.code.startsWith('IC')?'手':'张'}</td><td>${n(z.price,2)}</td><td>${esc(reason(z.source))}</td></tr>`));
 return `<div class="sec"><h2>预计当前持仓 · IC＋MO</h2><span class="side">按当前建议操作后 · 成本与损益按 ${esc(r.snapshot.date)} 价格估算 · 不是实际成交确认</span><div class="section-actions"><button onclick="window.uiPanel.open('expectedHistory')">查看变仓记录</button></div></div><div class="estimate-grid">${pane('IC',ic)}${pane('MO',mo)}</div><div class="sec"><h2>剩余持仓批次</h2><span class="side">各合约减仓先进先出；当前建议中的买入以快照价估算，未写入实际成交或连续模型账本。</span></div><section class="panel"><div class="tbx estimate-batches"><table><thead><tr><th>合约</th><th>成本日期</th><th>剩余数量</th><th>估算开仓价</th><th>来源</th></tr></thead><tbody>${rows.join('')||'<tr><td colspan="5">空仓或批次未知</td></tr>'}</tbody></table></div></section>`;
}
function history(){const trades=(PACK.history||[]).flatMap(d=>d.trades).slice().reverse();document.getElementById('expectedHistoryBody').innerHTML=`<p>研究连续账户的模型估算成交记录，不是真实账户。每笔已扣模型费用，持仓成本按 FIFO 保留。</p><div class="tbx"><table><thead><tr><th>日期</th><th>合约</th><th>数量</th><th>模拟价</th><th>费用</th></tr></thead><tbody>${trades.map(t=>`<tr><td>${esc(t.date)}</td><td>${esc(t.code)}</td><td>${t.quantity}</td><td>${n(t.price,1)}</td><td>${n(t.fee)}</td></tr>`).join('')}</tbody></table></div>`;}
function render(){if(!PACK)return;const c=local(),chosen=selectRecord(PACK,c);let r=chosen.record;if(!r){host.innerHTML='<section class="panel"><div class="foot">组合数据尚未就绪。</div></section>';return;}
 const phase=chosen.phase,snap=r.snapshot;stamp.textContent=`${snap.date} ${snap.asof.slice(11,19)} · ${phase==='close'?'正式收盘':phase==='waiting'?'等待当天收盘，旧时点参考':phase==='before'?'上一交易日参考':'盘中预判'}${r.delta_kind==='implied-intraday'?' · Delta 为隐波反解估算':''}`;
 if(REC && (REC.date!==c.date || c.time>='15:00:00'))REC=null;
 host.innerHTML=`<section class="panel current-signal"><div class="icmo-strategies">${icCard(r,phase)}${moCard(r)}</div>${REC?`<div class="icmo-diff">${esc(REC.asof)} 盘中最新：中证500 ${n(REC.index500,3)} · 中证1000 ${n(REC.index1000,3)} · Put ${n(REC.premium,1)} 点。当前指令仍基于 ${esc(snap.asof.slice(11,19))} 已保存快照；若跨阈值，先按下次变化重核资金与合约，不把只更新价格当成完整新订单。</div>`:''}${orderTable(r,phase)}</section>${expected(r)}`;history();
}
// JSONP is public market data; only documented fields are parsed. No account writes.
function jsonpQuote(symbols){return new Promise((resolve,reject)=>{const sc=document.createElement('script');sc.charset='gb2312';sc.src='https://hq.sinajs.cn/list='+symbols.join(',')+'&_='+Date.now();const timer=setTimeout(()=>{sc.remove();reject(Error('quote timeout'))},10000);sc.onload=()=>{clearTimeout(timer);const out={};for(const k of symbols)out[k]=window['hq_str_'+k]||'';sc.remove();resolve(out)};sc.onerror=()=>{clearTimeout(timer);sc.remove();reject(Error('quote unavailable'))};document.head.append(sc)})}
function impliedDelta(S,K,days,p){if(Math.min(S,K,days,p)<=0)return null;const T=days/365,r=.02,disc=Math.exp(-r*T);if(p<=Math.max(0,K*disc-S)||p>=K*disc)return null;const erf=x=>{const z=Math.abs(x),t=1/(1+.3275911*z),y=1-(((((1.061405429*t-1.453152027)*t)+1.421413741)*t-.284496736)*t+.254829592)*t*Math.exp(-z*z);return x<0?-y:y};const N=x=>(1+erf(x/Math.SQRT2))/2;const val=v=>{const a=v*Math.sqrt(T),d=(Math.log(S/K)+(r+.5*v*v)*T)/a;return K*disc*N(a-d)-S*N(-d)};let lo=.0001,hi=4;if(val(hi)<p)return null;for(let i=0;i<60;i++){const mid=(lo+hi)/2;if(val(mid)<p)lo=mid;else hi=mid}const v=(lo+hi)/2;return N((Math.log(S/K)+(r+.5*v*v)*T)/(v*Math.sqrt(T)))-1;}
async function tryLive(){
 const c=local();if(c.time<'14:45:00'||c.time>='15:00:00'||!PACK?.preview||PACK.preview.snapshot.date!==c.date)return;
 const epoch=++liveEpoch,base=PACK.preview,raw=base.snapshot,ic=base.ic_code,pk=base.put.code;if(!pk)return;const symbol='P_OP_'+pk.slice(0,2).toLowerCase()+pk.slice(2,6)+pk[7]+pk.slice(9);const vals=await jsonpQuote(['sh000905','sh000852','nf_'+ic,symbol]);
 const fresh=t=>t?.slice(0,10)===c.date&&Math.abs(Date.parse(c.date+'T'+c.time+'+08:00')-Date.parse(t.replace(' ','T')+'+08:00'))<=180000;
 const a=vals.sh000905.split(','),b0=vals.sh000852.split(','),f=vals['nf_'+ic].split(','),o=vals[symbol].split(',');const fi=f.findIndex(x=>/^\d{4}-\d{2}-\d{2}$/.test(x));if(!fresh(a[30]+' '+a[31])||!fresh(b0[30]+' '+b0[31])||!fresh(f[fi]+' '+f[fi+1])||!fresh(o[32]))throw Error('late stale quotes');
 const index=+a[3],im=+b0[3],fp=+f[3],op=+o[2];if(![index,im,fp,op].every(x=>Number.isFinite(x)&&x>0))throw Error('bad late quotes');const delta=impliedDelta(im,raw.options[pk].strike,raw.options[pk].days,op);if(delta==null)throw Error('invalid late Delta');
 // Late live data updates the observation line only; do not invent capacity/funding-approved fills.
 // Same-held-contract-only live recalc; any pending rolling or multi-contract holding stays server plan.
 if(Object.keys(PACK.state.options).some(k=>k!==pk)||Object.keys(PACK.state.futures).some(k=>k!==ic))return;
 if(epoch===liveEpoch){REC={date:c.date,asof:c.time,index500:index,index1000:im,premium:op,delta};render()}
}
window.reloadIC=async()=>{try{const candidate=await window.readJSON('data/ic_mo_strategy.json?t='+Date.now());if(candidate.schema!=='ic-mo-model-1'||!candidate.state||!candidate.close)throw Error('invalid combination data');PACK=candidate;REC=null;render();tryLive().catch(()=>{});}catch(e){if(!PACK)host.innerHTML='<section class="panel"><div class="foot">组合数据未就绪，未显示过期或示例交易指令。</div></section>';else render();}};
setInterval(()=>{if(!document.hidden)render()},30000);window.refresh();
})();
