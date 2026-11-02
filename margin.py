"""两融余额 + 指数日线，收盘后运行：python3 margin.py -> data/margin_balance.json"""
import json, datetime, akshare as ak, pandas as pd
def margin():
    sh = ak.macro_china_market_margin_sh()  # 上交所
    sz = ak.macro_china_market_margin_sz()  # 深交所
    def norm(df):
        df = df.copy(); df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
        return df.set_index("日期")["融资融券余额"].astype(float)
    a, b = norm(sh), norm(sz)
    s = pd.concat([a, b], axis=1, join="inner").sum(axis=1) / 1e8
    return {d: round(v, 2) for d, v in s.sort_index().items()}
IDX = {"创业板指":"sz399006","科创50":"sh000688","中证500":"sh000905","中证1000":"sh000852","沪深300":"sh000300","上证50":"sh000016"}
def idx(code):
    df = ak.stock_zh_index_daily(symbol=code)
    return {pd.Timestamp(d).strftime("%Y-%m-%d"): round(float(c), 2) for d, c in zip(df["date"], df["close"])}
m = margin(); out = {"updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "codes": IDX, "margin": m, "index": {}}
for k, c in IDX.items(): out["index"][k] = idx(c)
json.dump(out, open("data/margin_balance.json", "w"), ensure_ascii=False, separators=(",", ":"))
print("margin", min(m), max(m), len(m)); [print(k, min(v), max(v)) for k, v in out["index"].items()]
