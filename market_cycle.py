"""Independent medium-term market observations; never reads holdings/IC or writes their files.
Price-derived factor-v2 scores are coverage ratios, not calibrated probabilities. All histories
use data available on/before their signal day; forward results are kept separate.
"""
from __future__ import annotations
import argparse
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import math
from pathlib import Path
import statistics
import urllib.request
from zoneinfo import ZoneInfo
from backend_io import write_json_atomic
from market_factors import VERSION, FACTOR_DEFS, score_day, compact, evaluate

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo('Asia/Shanghai')
NY = ZoneInfo('America/New_York')
BASE = 'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param='
MARKETS = {
    'cn': [('sh000300','沪深300'),('sh000905','中证500'),('sh000852','中证1000'),('sh000016','上证50'),('sz399006','创业板指'),('sh000688','科创50')],
    'us': [('us.INX','标普500'),('us.IXIC','纳斯达克综合'),('us.DJI','道琼斯')],
}
RULES = {
    'version': VERSION, 'horizon':'1–3个月', 'validated':False,
    'scoring':'顶部风险、底部机会各4组×2项；组内等权、组间等权。8项固定分母，有缺项不给总分；比例不是概率或仓位',
    'groups':'价格位置/过热动量/高位转弱/共振与波动；超跌程度/抛压缓和/趋势改善/共振与波动',
    'breadth':'A股6宽基、美股3宽基，至少一半满足才计1项；不是全市场个股宽度',
    'bottom':'超跌组最多25分，后6项均有近63日超跌背景；改善项单列，不把跌深当已反转',
    'missing':'核心价格缺失、非有限值、上下文不足不给新分数；保留旧行情日，不缩小8项分母',
    'evaluation':'固定0–<25、25–<50、50–<75、75–100分箱；每侧每箱每窗口非重叠取点，未来21/63日独立检验，未满为空。回溯结果不是样本外概率',
    'coverage':'仅价格位置、动量、趋势、多宽基及实现波动；RSI14为Cutler口径。未接估值、信用、期权、全成分股宽度，四组也非独立证据',
}


def fetch(code, now, getter=None):
    url = BASE + code + ',day,,,1500,qfq'
    if getter is None:
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = json.load(r)
    else:
        raw = getter(url)
    block = raw['data'][code]
    rows = block.get('day') or block.get('qfqday') or []
    local = now.astimezone(NY if code.startswith('us.') else TZ)
    # Exclude today's still-open daily bar; never call an intraday price a close.
    cutoff = local.date()
    closing = dt.time(16,15) if code.startswith('us.') else dt.time(15,15)
    if local.time().replace(tzinfo=None) < closing:
        cutoff -= dt.timedelta(days=1)
    out = {}
    for row in rows:
        day = dt.date.fromisoformat(row[0])
        if day > cutoff:
            continue
        values = [float(x) for x in row[1:5]]
        if not all(math.isfinite(x) and x > 0 for x in values):
            raise ValueError(code + ' 日线数值无效')
        op, close, high, low = values
        if not low <= min(op,close) <= max(op,close) <= high:
            raise ValueError(code + ' 日线高低价不一致')
        if row[0] in out:
            raise ValueError(code + ' 重复日期')
        out[row[0]] = close
    if len(out) < 300:
        raise ValueError(code + ' 完整日线不足300条')
    return {'code':code,'source':url,'provider':'腾讯财经日线','history':[{'date':d,'close':out[d]} for d in sorted(out)]}


def features(series, bias_history=None):
    """Features for a single prefix; no future data access."""
    closes = [r['close'] for r in series]
    if len(closes) < 253:
        raise ValueError('历史不足')
    c = closes[-1]
    mean = lambda n: statistics.fmean(closes[-n:])
    bias = (c / mean(200) - 1) * 100
    historic_bias = bias_history if bias_history is not None else [(closes[i]/statistics.fmean(closes[i-199:i+1])-1)*100 for i in range(max(199,len(closes)-756),len(closes)-1)]
    percentile = sum(v <= bias for v in historic_bias)/len(historic_bias)*100
    changes = [b-a for a,b in zip(closes[-15:-1],closes[-14:])]
    gain = sum(max(x,0) for x in changes)/14
    loss = sum(max(-x,0) for x in changes)/14
    rsi = 100*gain/(gain+loss) if gain+loss else 50.
    returns = [math.log(b/a) for a,b in zip(closes[-21:-1],closes[-20:])]
    vol = statistics.stdev(returns)*math.sqrt(252)*100
    ret63 = (c/closes[-64]-1)*100
    drawdown = (c/max(closes[-252:])-1)*100
    dd63 = (c/max(closes[-63:])-1)*100
    rebound = (c/min(closes[-63:])-1)*100
    above20 = all(closes[-1-j] > statistics.fmean(closes[-20-j:len(closes)-j if j else None]) for j in range(3))
    above50 = all(closes[-1-j] > statistics.fmean(closes[-50-j:len(closes)-j if j else None]) for j in range(3))
    below200 = all(closes[-1-j] < statistics.fmean(closes[-200-j:len(closes)-j if j else None]) for j in range(3))
    return {'close':c, 'ma20':mean(20),'ma50':mean(50),'ma60':mean(60),'ma200':mean(200),
            'bias200':bias,'bias_pctile':percentile,'ret63':ret63,'ret5':(c/closes[-6]-1)*100,'drawdown':drawdown,'dd63':dd63,'rebound':rebound,
            'rsi14':rsi,'vol20':vol,'above20_3':above20,'above50_3':above50,'below200_3':below200,
            'hot':(bias>=15 and percentile>=90) or (ret63>=20 and rsi>=70),
            'washed':drawdown<=-15 and rsi<=35}


def compute_features(history):
    closes=[r['close'] for r in history]
    biases={i:(closes[i]/statistics.fmean(closes[i-199:i+1])-1)*100 for i in range(199,len(closes))}
    return {history[i]['date']:features(history[:i+1],[biases[j] for j in range(max(199,i-755),i)]) for i in range(252,len(history))}


def build_market(key, series, now):
    codes = [c for c,_ in MARKETS[key]]
    if any(c not in series for c in codes):
        raise ValueError('核心宽基日线缺失')
    data = {c:series[c]['history'] for c in codes}
    tails = [dt.date.fromisoformat(data[c][-1]['date']) for c in codes]
    if (max(tails)-min(tails)).days>5 or (now.date()-min(tails)).days>5:
        raise ValueError('核心宽基日线陈旧或未同步')
    matrices={c:compute_features(data[c]) for c in codes}
    common=sorted(set.intersection(*(set(m) for m in matrices.values())))
    if len(common)<63:
        raise ValueError('共同历史不足')
    # Signals start only after warm-up; all lookbacks use prior/current signal dates.
    signals=[score_day(d,matrices,common[max(0,i-62):i+1],codes[0]) for i,d in enumerate(common)]
    latest=signals[-1]
    names=dict(MARKETS[key])
    for c,f in latest['indices'].items():
        f['name']=names[c]
        f['source']=series[c]['source']
    return {'name':'A股' if key=='cn' else '美股','as_of':common[-1],'status':'ok','primary':codes[0],
            'latest':latest,'history':[compact(r) for r in signals[-252:]], 'evaluation':evaluate(data[codes[0]],signals),
            'prices':{c:[r for r in data[c] if r['date']<=common[-1]][-504:] for c in codes},'sources':[series[c]['source'] for c in codes]}


def margin_context(path, day):
    try:
        raw=json.loads(path.read_text())['margin']
        dates=sorted(d for d in raw if d<=day)
        if not dates:
            return None
        d=dates[-1]
        value=float(raw[d])
        if not math.isfinite(value) or value<=0:
            return None
        change=(value/float(raw[dates[-21]])-1)*100 if len(dates)>=21 else None
        return {'as_of':d,'balance':value,'change20':change,'lag_days':(dt.date.fromisoformat(day)-dt.date.fromisoformat(d)).days,
                'source':'https://github.com/kakarottoisme/national-team-octagon/blob/main/data/margin_balance.json'}
    except (OSError,ValueError,KeyError,TypeError):
        return None


def collect(output, now=None):
    now=now or dt.datetime.now(TZ)
    prior=json.loads(output.read_text()) if output.exists() else {}
    tasks=[c for rows in MARKETS.values() for c,_ in rows]
    series={};errors={}
    def get_one(c):
        try:return c,fetch(c,now),None
        except Exception as e:return c,None,type(e).__name__+'：'+str(e)[:140]
    with ThreadPoolExecutor(max_workers=5) as pool:
        for c,result,error in pool.map(get_one,tasks):
            if result:series[c]=result
            else:errors[c]=error
    markets={}
    for k in MARKETS:
        try:
            markets[k]=build_market(k,series,now)
        except Exception as e:
            previous=prior.get('markets',{}).get(k)
            if previous:
                markets[k]=dict(previous,status='stale',last_attempt_at=now.isoformat(),error=str(e))
            else:
                markets[k]={'name':'A股' if k=='cn' else '美股','status':'missing','as_of':None,'error':str(e)}
    if markets['cn'].get('as_of'):
        markets['cn']['margin']=margin_context(ROOT/'data/margin_balance.json',markets['cn']['as_of'])
    observations={k:list(prior.get('observations',{}).get(k,[])) for k in MARKETS}
    for k,m in markets.items():
        if m['status']=='ok' and not any(r['date']==m['as_of'] and r.get('rule_version')==VERSION for r in observations[k]):
            r=m['latest'];observations[k].append({'date':m['as_of'],'observed_at':now.isoformat(timespec='seconds'),
                'rule_version':VERSION,'scores':{s:v['score'] for s,v in r['scores'].items()}})
    payload={'schema':'market-factor-2','observations':observations,'generated_at':now.isoformat(timespec='seconds'),'rules':RULES,'markets':markets,'errors':errors,
             'update_schedule':'北京时间交易日08:20、17:10；GitHub调度可能延迟；各市场只用已完成收盘日线',
             'reference':'https://musaeai.com/','history_kind':'按当日价格重放规则，非历史实盘预警',
             'first_published_at':prior.get('first_published_at') or now.isoformat(timespec='seconds')}
    # No commits when no data/state change; reread time is not data-update time.
    previous_compare={k:v for k,v in prior.items() if k!='generated_at'}
    comparison={k:v for k,v in payload.items() if k!='generated_at'}
    if previous_compare!=comparison:
        write_json_atomic(output,payload,ensure_ascii=False,separators=(',',':'))
    print(json.dumps({'markets':{k:{'status':v['status'],'as_of':v.get('as_of')} for k,v in markets.items()},'errors':errors},ensure_ascii=False))
    return all(v['status']=='ok' for v in markets.values())


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'data/market_cycle.json')
    args=parser.parse_args()
    collect(args.output)
