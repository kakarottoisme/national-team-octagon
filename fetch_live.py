# 拉取 7 只 ETF 当日分时 + 上证日线，写 data/live.json（站点同源读取）
import json, urllib.request, datetime, os, time

def g(u):
    # Actions 跑在云端，到腾讯的连接偶发超时或 SSL 中断；重试避免整档被一次抖动丢掉。
    delay = 1.5
    last = None
    for attempt in range(3):
        try:
            q = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com/"})
            with urllib.request.urlopen(q, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8", "ignore"))
        except Exception as e:
            last = e
            if attempt == 2:
                break
            time.sleep(delay)
            delay *= 2
    raise last

E = [
    ("上证50", "sh510050"),
    ("沪深300", "sh510300"),
    ("中证A500", "sz159352"),
    ("中证500", "sh510500"),
    ("中证1000", "sh512100"),
    ("创业板", "sz159915"),
    ("科创50", "sh588000"),
]
etfs, est, date = {}, {}, None
for n, c in E:
    raw = g(f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={c}")["data"][c]
    d = raw["data"]
    date = d["date"]
    pa = 0
    t, p, a = [], [], []
    for s in d["data"]:
        r = s.split()
        am = float(r[3])
        t.append(r[0])
        p.append(float(r[1]))
        a.append(round((am - pa) / 1e8, 4))
        pa = am
    pre = None
    try:
        pre = float(raw["qt"][c][4])
    except Exception:
        pre = p[0] if p else None
    etfs[c] = {"t": t, "p": p, "a": a, "date": date, "pre": pre}
    est[n] = round(sum(x for x in a if x > 1), 2)
est["合计"] = round(sum(v for k, v in est.items()), 2)
try:
    k = g("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sh000001,day,,,60,qfq")["data"]["sh000001"]
    sse = [x[:6] for x in (k.get("day") or k.get("qfqday"))]
except Exception:
    k = g(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001&fields1=f1&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=1&lmt=60&end=20500101"
    )
    sse = [x.split(",") for x in k["data"]["klines"]]
ds = f"{date[:4]}-{date[4:6]}-{date[6:]}"
ix = [i for i, x in enumerate(sse) if x[0] == ds]
if ix and ix[0] > 0:
    i = ix[0]
    c = float(sse[i][2])
    pc = float(sse[i - 1][2])
    est["上证收盘"] = c
    est["上证涨跌幅"] = round((c / pc - 1) * 100, 2)
now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
os.makedirs("data", exist_ok=True)
tmp = "data/live.json.tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(
        {"updated": now.strftime("%Y-%m-%d %H:%M:%S"), "date": date, "etfs": etfs, "estimates": est, "sse": sse},
        fh,
        ensure_ascii=False,
        separators=(",", ":"),
    )
os.replace(tmp, "data/live.json")
print(now.strftime("%Y-%m-%d %H:%M:%S"), date, est)
