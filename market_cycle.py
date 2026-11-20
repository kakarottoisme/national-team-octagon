"""Independent medium-term market observations; never reads holdings/IC or writes their files.
Price-derived rules are v1 heuristics, not calibrated probabilities. All histories
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

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo('Asia/Shanghai')
NY = ZoneInfo('America/New_York')
BASE = 'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param='
MARKETS = {
    'cn': [('sh000300','沪深300'),('sh000905','中证500'),('sh000852','中证1000'),('sh000016','上证50'),('sz399006','创业板指'),('sh000688','科创50')],
    'us': [('us.INX','标普500'),('us.IXIC','纳斯达克综合'),('us.DJI','道琼斯')],
}
RULES = {
    'version':'cycle-v1', 'horizon':'1–3个月', 'validated':False,
    'overheat':'200日乖离≥15%且位于此前最多756日的90%分位以上；或63日涨幅≥20%且RSI14≥70',
    'breadth':'仅上述宽基指数等权计数，不是全市场个股上涨家数，不计算重复的ETF',
    'top':'过热指数≥一半为过热；近20日出现过热且至少一半跌破20日线、63日高点回撤≥5%，为顶部风险确认；仅价格层确认',
    'bottom':'至少一半指数回撤≥15%且RSI14≤35，或过去20日达到此条件，为底部观察区；至少一半从63日低点反弹≥5%、连续3日站上20日线、RSI14≥45且波动未创新高，为筑底改善',
    'downtrend':'至少一半指数连续3日位于200日线下且20日线低于60日线，显示中期下行压力；与顶部过热不是同一概念',
    'confirmation':'主指数50日线>200日线、连续3日站上50日线、至少2/3指数站上50日线，且距63日高点仍回撤≥10%：趋势修复确认，不宣称绝对底部',
    'margin':'A股两融仅用已入库且不晚于信号日的余额，辅助展示20个余额记录变化；不纳入顶底评分；历史发布时间未知，不用于回测',
    'missing':'核心价格缺源或共同交易日相差超过5个自然日，不生成新综合判断，保留旧数据及旧行情日期并标为陈旧',
    'evaluation':'历史按当日及以前价格逐日重算，不用今天指标回填；21/63交易日后的收益和期间最低收益独立列示，未满窗口为空。observations仅保存v1上线后的首次日度判断，不覆盖已留痕日期',
    'coverage':'价格、实现波动、多宽基共振；RSI14按最近14日涨跌幅的简单均值计算（Cutler口径）。没有全成分股宽度、估值、期权结构、信用确认。美国没有复制VIX、MOVE、GEX或CDS，也不把实现波动冒充VIX',
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
            'bias200':bias,'bias_pctile':percentile,'ret63':ret63,'drawdown':drawdown,'dd63':dd63,'rebound':rebound,
            'rsi14':rsi,'vol20':vol,'above20_3':above20,'above50_3':above50,'below200_3':below200,
            'hot':(bias>=15 and percentile>=90) or (ret63>=20 and rsi>=70),
            'washed':drawdown<=-15 and rsi<=35}


def compute_features(history):
    closes=[r['close'] for r in history]
    biases={i:(closes[i]/statistics.fmean(closes[i-199:i+1])-1)*100 for i in range(199,len(closes))}
    return {history[i]['date']:features(history[:i+1],[biases[j] for j in range(max(199,i-755),i)]) for i in range(252,len(history))}


def judge(day, matrices, recent, main):
    fs = {c:m[day] for c,m in matrices.items()}
    n = len(fs)
    need = math.ceil(n/2)
    hot = sum(f['hot'] for f in fs.values())
    washed = sum(f['washed'] for f in fs.values())
    above50 = sum(f['close']>f['ma50'] for f in fs.values())
    broken = sum(f['close']<f['ma20'] and f['dd63']<=-5 for f in fs.values())
    falling = sum(f['below200_3'] and f['ma20']<f['ma60'] for f in fs.values())
    recently_hot = any(sum(m[d]['hot'] for m in matrices.values())>=need for d in recent)
    recently_washed = any(sum(m[d]['washed'] for m in matrices.values())>=need for d in recent)
    improving = sum(f['above20_3'] and f['rebound']>=5 and f['rsi14']>=45 and
                    f['vol20']<max(matrices[c][d]['vol20'] for d in recent[:-1]) for c,f in fs.items()) if len(recent)>1 else 0
    f = fs[main]
    repaired = f['above50_3'] and f['ma50']>f['ma200'] and above50>=math.ceil(2*n/3) and f['dd63']<=-10
    top = '顶部风险确认' if recently_hot and broken>=need else '过热累积' if hot>=need else '中期下行压力' if falling>=need else '未见顶部共振'
    bottom = '趋势修复确认' if recently_washed and repaired else '筑底改善' if recently_washed and improving>=need else '底部观察区' if washed>=need or recently_washed else '未见底部共振'
    summary = '下行趋势仍在，反弹还没形成底部确认' if falling>=need and bottom=='底部观察区' else '底部迹象正在改善，尚未确认中期反转' if bottom=='筑底改善' else '价格与宽基共振出现趋势修复' if bottom=='趋势修复确认' else '高位过热后出现破位，顶部风险已获价格确认' if top=='顶部风险确认' else '上涨过热累积，尚未出现顶部价格确认' if top=='过热累积' else '中期价格承压，不能仅凭回撤判断已经见底' if falling>=need else '暂未出现明显顶底共振，继续观察趋势'
    return {'date':day,'top':top,'bottom':bottom,'summary':summary,
            'breadth':{'above50':above50,'total':n,'hot':hot,'washed':washed,'improving':improving,'falling':falling},'indices':fs}


def outcomes(history, signals, main):
    prices = {r['date']:r['close'] for r in history}
    days = sorted(prices)
    positions = {d:i for i,d in enumerate(days)}
    out=[]
    last=None
    for row in signals:
        pair = (row['top'],row['bottom'])
        if pair==last:
            continue
        last=pair
        i=positions[row['date']]
        item={'date':row['date'],'top':row['top'],'bottom':row['bottom'],'forward':{}}
        for window in (21,63):
            if i+window>=len(days):
                item['forward'][str(window)] = None
                continue
            future = [prices[d] for d in days[i+1:i+window+1]]
            base=prices[row['date']]
            item['forward'][str(window)]={'return':(future[-1]/base-1)*100,'min_from_signal':(min(future)/base-1)*100,
                                         'max_from_signal':(max(future)/base-1)*100,'end_date':days[i+window]}
        out.append(item)
    return out


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
    signals=[judge(d,matrices,common[max(0,i-19):i+1],codes[0]) for i,d in enumerate(common)]
    latest=signals[-1]
    names=dict(MARKETS[key])
    for c,f in latest['indices'].items():
        f['name']=names[c]
        f['source']=series[c]['source']
    return {'name':'A股' if key=='cn' else '美股','as_of':common[-1],'status':'ok','primary':codes[0],
            'latest':latest,'history':signals[-252:], 'events':outcomes(data[codes[0]],signals,codes[0]),
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
        if m['status']=='ok' and not any(r['date']==m['as_of'] for r in observations[k]):
            r=m['latest'];observations[k].append({'date':m['as_of'],'observed_at':now.isoformat(timespec='seconds'),
                'rule_version':RULES['version'],'top':r['top'],'bottom':r['bottom'],'breadth':dict(r['breadth'])})
    payload={'schema':'market-cycle-1','observations':observations,'generated_at':now.isoformat(timespec='seconds'),'rules':RULES,'markets':markets,'errors':errors,
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
