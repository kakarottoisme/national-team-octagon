"""Fixed factor-v2 score and historical-bin evaluation; no acquisition or trading.
Definitions frozen in docs/MARKET_FACTORS.md. Percentages are factor coverage,
not calibrated probabilities. No threshold search or future input in scoring.
"""
from __future__ import annotations
import math
import statistics

VERSION = 'factor-v2'
BINS = [(0,25,'0–<25'),(25,50,'25–<50'),(50,75,'50–<75'),(75,101,'75–100')]
GROUPS = {
    'top': [('position','价格位置'),('momentum','过热动量'),('trend','高位转弱'),('breadth_vol','共振与波动')],
    'bottom': [('oversold','超跌程度'),('selling','抛压缓和'),('trend','趋势改善'),('breadth_vol','共振与波动')],
}
FACTOR_DEFS = {
    'top': [
        ('bias','position','长期乖离偏高','至少半数宽基200日乖离≥15%'),
        ('percentile','position','乖离处于高分位','至少半数宽基乖离历史分位≥90%'),
        ('ret63','momentum','中期涨幅偏高','至少半数宽基63交易日涨幅≥20%'),
        ('rsi','momentum','短期动量过热','至少半数宽基RSI14≥70'),
        ('below20','trend','高位跌破短期趋势','至少半数宽基20日内曾过热、当前收盘低于20日线'),
        ('ret5','trend','高位短期转跌','至少半数宽基20日内曾过热、近5交易日涨跌<0'),
        ('breadth','breadth_vol','高位上涨共振减弱','20日内至少半数宽基过热、当前站50日线数低于20共同交易日前'),
        ('vol','breadth_vol','高位下跌波动放大','至少半数宽基20日内曾过热、近5日跌且波动高于此前20共同日均值'),
    ],
    'bottom': [
        ('drawdown','oversold','一年高点回撤较深','至少半数宽基距252交易日最高收盘回撤≥15%'),
        ('rsi','oversold','短期动量超跌','至少半数宽基RSI14≤35'),
        ('ret5','selling','超跌后短期止跌','至少半数宽基63日内曾超跌、近5交易日涨跌>0'),
        ('rsi_recover','selling','超跌后动量回升','至少半数宽基63日内曾超跌、当前RSI14≥45'),
        ('above20','trend','超跌后站稳20日线','至少半数宽基63日内曾超跌、连续3日站上20日线'),
        ('above50','trend','超跌后站稳50日线','至少半数宽基63日内曾超跌、连续3日站上50日线'),
        ('breadth','breadth_vol','超跌后上涨共振改善','63日内至少半数宽基超跌、当前站50日线数高于20共同交易日前'),
        ('vol','breadth_vol','超跌后波动缓和','至少半数宽基63日内曾超跌、波动低于此前20共同日均值'),
    ],
}


def bin_id(score):
    if score is None or not math.isfinite(score) or not 0 <= score <= 100:
        return None
    return next(i for i,(low,high,_) in enumerate(BINS) if low <= score < high)


def aggregate(side, factors):
    """Fixed denominators; one unknown item makes that group and total unknown."""
    groups=[]
    for gid,name in GROUPS[side]:
        items=[x for x in factors if x['group']==gid]
        assert len(items)==2
        valid=all(x['met'] is not None for x in items)
        count=sum(x['met'] is True for x in items)
        groups.append({'id':gid,'name':name,'met':count,'total':2,'score':count/2*100 if valid else None})
    valid=all(g['score'] is not None for g in groups)
    score=statistics.fmean(g['score'] for g in groups) if valid else None
    return {'score':score,'met':sum(x['met'] is True for x in factors),'total':8,
            'missing':sum(x['met'] is None for x in factors),'groups':groups,'factors':factors,
            'improvement_met':sum(x['met'] is True for x in factors if x['group']!='oversold') if side=='bottom' else None,
            'bin':bin_id(score)}


def score_day(day, matrices, context, main):
    """context is <=63 common dates ending at day; never consumes future dates."""
    if not context or context[-1]!=day or any(d>day for d in context):
        raise ValueError('因子上下文必须以信号日结束，不得含未来')
    fs={c:m.get(day,{}) for c,m in matrices.items()}
    n=len(fs);need=math.ceil(n/2)
    required=('close','ma20','ma50','bias200','bias_pctile','ret63','ret5','rsi14','drawdown','vol20','above20_3','above50_3','hot','washed')
    ready=len(context)>=63 and n>0 and main in fs and all(
        all(k in f and f[k] is not None and (isinstance(f[k],bool) or math.isfinite(float(f[k]))) for k in required) for f in fs.values())
    prior=context[-21:-1]
    # Required past fields cannot be silently replaced by zero.
    ready=ready and all(all(d in m and all(k in m[d] and m[d][k] is not None and (isinstance(m[d][k],bool) or math.isfinite(float(m[d][k]))) for k in ('hot','washed','close','ma50','vol20')) for d in context) for m in matrices.values())
    votes={'top':{},'bottom':{}}
    readings={}
    if ready:
        hot={c:any(m[d]['hot'] for d in context[-20:]) for c,m in matrices.items()}
        washed={c:any(m[d]['washed'] for d in context) for c,m in matrices.items()}
        hot_market=any(sum(m[d]['hot'] for m in matrices.values())>=need for d in context[-20:])
        washed_market=any(sum(m[d]['washed'] for m in matrices.values())>=need for d in context)
        above=sum(f['close']>f['ma50'] for f in fs.values())
        before=sum(m[context[-21]]['close']>m[context[-21]]['ma50'] for m in matrices.values())
        volbase={c:statistics.fmean(m[d]['vol20'] for d in prior) for c,m in matrices.items()}
        for c,f in fs.items():
            votes['top'][c]={
                'bias':f['bias200']>=15,'percentile':f['bias_pctile']>=90,
                'ret63':f['ret63']>=20,'rsi':f['rsi14']>=70,
                'below20':hot[c] and f['close']<f['ma20'],
                'ret5':hot[c] and f['ret5']<0,
                'vol':hot[c] and f['ret5']<0 and f['vol20']>volbase[c],
            }
            votes['bottom'][c]={
                'drawdown':f['drawdown']<=-15,'rsi':f['rsi14']<=35,
                'ret5':washed[c] and f['ret5']>0,'rsi_recover':washed[c] and f['rsi14']>=45,
                'above20':washed[c] and f['above20_3'],'above50':washed[c] and f['above50_3'],
                'vol':washed[c] and f['vol20']<volbase[c],
            }
        readings={'above50':above,'above50_before20':before,'total':n}
    result={'date':day,'version':VERSION,'indices':fs,'scores':{},'breadth':readings}
    for side,defs in FACTOR_DEFS.items():
        factors=[]
        for fid,group,name,rule in defs:
            count=None;met=None;current='上下文或核心数值缺失'
            if ready:
                if fid=='breadth':
                    met=(hot_market and above<before) if side=='top' else (washed_market and above>before)
                    current=f'站50日线 {above}/{n} 个，20共同交易日前 {before}/{n} 个；近期'+('过热' if side=='top' else '超跌')+('共振有' if (hot_market if side=='top' else washed_market) else '共振无')
                else:
                    count=sum(v[fid] for v in votes[side].values());met=count>=need
                    current=f'{count}/{n} 个宽基满足，需至少 {need} 个'
                met=bool(met)
            factors.append({'id':fid,'group':group,'name':name,'rule':rule,'met':met,'count':count,'indices_total':n,'current':current,'as_of':day,
                            'readings': [{'code':c,'met':bool(votes[side][c][fid]),
                                'value':f[{'bias':'bias200','percentile':'bias_pctile','ret63':'ret63','rsi':'rsi14','below20':'close','ret5':'ret5','vol':'vol20','drawdown':'drawdown','rsi_recover':'rsi14','above20':'close','above50':'close'}[fid]],
                                'reference': f['ma20'] if fid in ('below20','above20') else f['ma50'] if fid=='above50' else volbase[c] if fid=='vol' else None,
                                'context':hot[c] if side=='top' else washed[c]} for c,f in fs.items()] if ready and fid!='breadth' else []})
        result['scores'][side]=aggregate(side,factors)
    return result


def compact(row):
    return {'date':row['date'],'version':VERSION,'scores':{s:{k:v for k,v in score.items() if k!='factors'} for s,score in row['scores'].items()}}


def forward(history, idx, h, shift=0):
    i=idx+shift
    if i+h>=len(history):return None
    base=history[i]['close'];prices=[r['close'] for r in history[i+1:i+h+1]]
    return {'end_date':history[i+h]['date'],'base_close':base,'end_close':prices[-1],
            'return':(prices[-1]/base-1)*100,'min_from_signal':(min(prices)/base-1)*100,
            'max_from_signal':(max(prices)/base-1)*100}


def stats(events, side):
    n=len(events)
    if not n:return {'n':0,'hits':0,'hit_rate':None,'mean_return':None,'mean_min':None,'worst_min':None,'strict_hits':0,'strict_rate':None}
    hits=sum(e['forward']['return']<0 if side=='top' else e['forward']['return']>0 for e in events)
    strict=sum(e['forward']['min_from_signal']<=-5 if side=='top' else (e['forward']['return']>=5 and e['forward']['min_from_signal']>=-5) for e in events)
    return {'n':n,'hits':hits,'hit_rate':hits/n*100,'mean_return':statistics.fmean(e['forward']['return'] for e in events),
            'mean_min':statistics.fmean(e['forward']['min_from_signal'] for e in events),
            'worst_min':min(e['forward']['min_from_signal'] for e in events),'strict_hits':strict,'strict_rate':strict/n*100}


def evaluate(history, signals):
    """Fixed bins, independent non-overlap selection per side/bin/horizon.
    Historical labels are never score inputs. All-day/year baselines overlap;
    counts aren't independent inference. No probability/position mapping.
    """
    pos={r['date']:i for i,r in enumerate(history)}
    valid=[r for r in signals if all(r['scores'][s]['score'] is not None for s in ('top','bottom'))]
    result={'start':valid[0]['date'] if valid else None,'end':valid[-1]['date'] if valid else None,'daily_n':len(valid),'bins':[],
            'method':'固定4分箱；每侧每箱每窗口相邻入选点间隔>窗口；未满未来窗口不计入。跨箱仍相关，整体日度基准窗口重叠；规则为回溯设计，不是样本外胜率'}
    for side in ('top','bottom'):
        for h in (21,63):
            baseline=[{'date':r['date'],'forward':forward(history,pos[r['date']],h)} for r in valid if forward(history,pos[r['date']],h) is not None]
            baseline_stats=stats(baseline,side)
            for bid,(_,_,label) in enumerate(BINS):
                days=[r for r in valid if bin_id(r['scores'][side]['score'])==bid]
                mature=[r for r in days if pos[r['date']]+h<len(history)]
                selected=[];last=-100000
                for r in mature:
                    i=pos[r['date']]
                    if i>last+h:
                        selected.append({'date':r['date'],'idx':i,'score':r['scores'][side]['score'],'forward':forward(history,i,h)});last=i
                shifted=[dict(e,forward=forward(history,e['idx'],h,1)) for e in selected if forward(history,e['idx'],h,1) is not None]
                early=[e for e in selected if e['date']<'2024-01-01'];late=[e for e in selected if e['date']>='2024-01-01']
                years={e['date'][:4]:stats([d for d in baseline if d['date'][:4]==e['date'][:4]],side) for e in selected}
                matched=statistics.fmean(years[e['date'][:4]]['hit_rate'] for e in selected) if selected else None
                result['bins'].append({'side':side,'bin':bid,'label':label,'horizon':h,'raw_days':len(days),'mature_days':len(mature),
                    'immature_days':len(days)-len(mature),'stats':stats(selected,side),'baseline':baseline_stats,
                    'year_matched_baseline':matched,'pre2024':stats(early,side),'since2024':stats(late,side),
                    'next_close':stats(shifted,side),'events':selected})
    return result
