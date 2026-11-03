# 用法: python3 archive.py  —— 收盘后运行，归档当日分时与估算到 data/
import json, urllib.request, os

def g(u):
    q = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(q, timeout=15).read().decode("utf-8", "ignore"))

E = [
    ("上证50", "sh510050"),
    ("沪深300", "sh510300"),
    ("中证A500", "sz159352"),
    ("中证500", "sh510500"),
    ("中证1000", "sh512100"),
    ("创业板", "sz159915"),
    ("科创50", "sh588000"),
]
mins, row, date = {}, {}, None
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
    try:
        pre = float(raw["qt"][c][4])
    except Exception:
        pre = p[0] if p else None
    mins[c] = {"t": t, "p": p, "a": a, "pre": pre}
    row[n] = round(sum(x for x in a if x > 1), 2)
ds = f"{date[:4]}-{date[4:6]}-{date[6:]}"
row["合计"] = round(sum(row.values()), 2)
k = g("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sh000001,day,,,40,qfq")["data"]["sh000001"]
k = k.get("day") or k.get("qfqday")
idx = [i for i, x in enumerate(k) if x[0] == ds]
if idx:
    i = idx[0]
    c = float(k[i][2])
    pc = float(k[i - 1][2])
    row["上证收盘"] = c
    row["上证涨跌幅"] = round((c / pc - 1) * 100, 2)
hp = "data/history.json"
h = json.load(open(hp)) if os.path.exists(hp) else {}
h[ds] = row
json.dump(dict(sorted(h.items())), open(hp, "w"), ensure_ascii=False, indent=1)
os.makedirs("data/minutes", exist_ok=True)
json.dump({"date": ds, "etfs": mins}, open(f"data/minutes/{ds}.json", "w"), separators=(",", ":"))
print(ds, row)
