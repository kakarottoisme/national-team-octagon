import json,urllib.request
def g(u): return json.loads(urllib.request.urlopen(u,timeout=15).read().decode('utf-8','ignore'))
T=[("上证50","510050","sh510050"),("沪深300","510300","sh510300"),("中证A500","159352","sz159352"),("中证500","510500","sh510500"),("中证1000","512100","sh512100"),("创业板","159915","sz159915"),("科创50","588000","sh588000")]
out=[]
for n,c,code in T:
  d=g(f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}")["data"][code]["data"]
  rows=[r.split() for r in d["data"]];date=d["date"]
  pa=0;t=[];p=[];a=[]
  for r in rows:
    am=float(r[3]);t.append(r[0]);p.append(float(r[1]));a.append(round((am-pa)/1e8,4));pa=am
  est=sum(x for x in a if x>1)
  out.append(dict(name=n,code=c,date=date,t=t,p=p,a=a,est=round(est,2)))
k=g("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sh000001,day,,,40,qfq")["data"]["sh000001"]
k=k.get("day") or k.get("qfqday")
sse=[[x[0],float(x[2])] for x in k if x[0]>="2026-10-15"]
json.dump(dict(etfs=out,sse=sse),open("data.json","w"),ensure_ascii=False)
for o in out: print(o["name"],o["est"],o["date"],o["t"][-1],len(o["t"]))
print(sum(o["est"] for o in out),sse[-3:])
