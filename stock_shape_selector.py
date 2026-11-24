# -*- coding: utf-8 -*-
"""
A股形态选股 + 广度择时系统
复刻 GitHub 开源方法:
  - Sequoia-X (8.8k★) 的 6 种经典形态策略(海龟突破/均线放量/旗形/涨停洗盘/跌停反包/RPS)
  - quant_v9 的广度二态择时(市场广度<阈值空仓避险)

数据源: 新浪日K(免费/免token/快) 主源; 内置股票池 + 池内涨跌比作广度代理; 全失败降级 DEMO
用法:
  python3.11 stock_shape_selector.py                    # 真实最新数据
  python3.11 stock_shape_selector.py --top 15           # 展示Top15
  python3.11 stock_shape_selector.py --demo             # DEMO可复现
  python3.11 stock_shape_selector.py --no-chart         # 不画图(定时任务用)
量化研究用途,不构成投资建议。
"""
import os
import re
import json
import time
import argparse
import datetime as dt
import numpy as np
import pandas as pd
import requests

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/workspace")
MIN_PRICE = 15.0          # 股价下限:低于此价剔除
EXCLUDE_STAR = True       # 是否剔除科创板(688/689)
SINA_KLINE = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x/CN_MarketDataService.getKLineData"
TENCENT_SPOT = "https://qt.gtimg.cn/q="

# 中性广度样本池:跨市场均匀取样的宽基/代表股,不按涨跌挑选(避免竞价池幸存者偏差)
BREADTH_POOL = (
    [f"sh60{i:04d}" for i in range(0, 500, 5)] +   # 沪市主板 sh600000~sh600500 取样
    [f"sh60{i:04d}" for i in range(1000, 1900, 15)] +  # 沪市 601xxx~6018xx
    [f"sz00{i:04d}" for i in range(1, 3000, 20)] +  # 深市主板 000xxx~002xxx
    [f"sz30{i:04d}" for i in range(0, 2000, 30)] +  # 创业板
    [f"sh688{i:03d}" for i in range(0, 600, 12)]    # 科创板
)

# 内置股票池(来自真实竞价全市场数据Top350,已修正代码格式)
POOL = """sh603360,sh600814,sh603608,sz002303,sh688059,sh605136,sz300201,sh600189,sh688583,sz002587,sh603175,sz301382,sz300687,sh688379,sz300050,sz301318,sz300909,sh603813,sz300192,sz300328,sh603316,sz002963,sz002631,sh688400,sz301419,sz002687,sz300606,sh688031,sh688559,sh688512,sz002161,sz002896,sh688115,sh600114,sz300975,sz002792,sh688598,sz301150,sz003026,sz003033,sh603067,sh688591,sz300602,sh688689,sz300042,sz301379,sh688388,sh688699,sh603119,sz301630,sz301005,sh601595,sh688758,sz301051,sz301046,sh603158,sz002443,sz300938,sh603989,sh688611,sz003036,sh688090,sz002886,sz000608,sz001359,sh603063,sz300323,sh688181,sh688191,sh688262,sz002902,sh603269,sz002796,sh688261,sz300227,sz300849,sz003018,sz300842,sh688381,sz002213,sz301310,sz301566,sh688401,sz002785,sh688173,sh688257,sz002989,sh688216,sz300131,sz300976,sz300746,sh605287,sh688159,sz300083,sh603270,sh688376,sz002289,sz002849,sz300480,sz300491,sh688667,sz002859,sh688230,sh688663,sz002674,sh688551,sz300931,sz002824,sz300303,sh605111,sh688419,sh603926,sz301563,sh688531,sh603337,sh688605,sh605006,sz301008,sz300802,sz300563,sz001232,sh688478,sz300836,sz300398,sz300151,sz300321,sz301273,sz300153,sz301629,sh600345,sz002290,sh603045,sh688720,sz300456,sz002364,sz002134,sz300464,sh603283,sz300736,sz301297,sz301607,sz002380,sh603048,sh688669,sh688210,sz000782,sz002971,sh600172,sh603328,sz002803,sh603002,sh688601,sh688610,sz001331,sh600110,sz002482,sz000700,sz001211,sh603040,sh603221,sz002611,sz300586,sh603112,sz300572,sh603786,sz300913,sz002885,sh603306,sz300382,sz300420,sz300440,sz300749,sh688268,sz300609,sz300679,sh600367,sz300279,sh605060,sz301603,sz001246,sz300751,sz300067,sz300731,sz300469,sh600237,sz300128,sh688069,sh603031,sz300726,sh605398,sz300943,sh603618,sz002515,sh688255,sz002957,sz300545,sz301092,sh688323,sh603991,sz301045,sz301121,sz300709,sh688653,sh601609,sz002486,sh600353,sz001287,sz001283,sz000980,sh603011,sh600184,sz300342,sz300671,sh688486,sz301125,sz300053,sz002713,sz301458,sz301248,sh688403,sh688179,sz301678,sz002965,sh603662,sz300964,sz300102,sz301536,sz002552,sh688035,sh603938,sz002842,sh688045,sz301021,sh688620,sh603052,sz000070,sz300077,sz301608,sh688333,sz300907,sz301099,sz300170,sh603688,sz301265,sz301285,sz001270,sz300547,sh603399,sz002787,sh603890,sz300820,sz002990,sh688079,sz300672,sz002194,sh688226,sh688700,sh688102,sz300559,sz301281,sz300184,sz300488,sz001225,sz300984,sz300378,sz000818,sz002527,sh688279,sz301093,sz301319,sz300723,sh688182,sh688582,sh603090,sz300868,sh603110,sz300747,sz300619,sh605376,sh688380,sh688365,sh688207,sz300889,sz300490,sz002150,sh688479,sz300252,sh688809,sz000620,sz300088,sh603936,sz301387,sz301228,sh688108,sz300638,sh603005,sh688709,sz300819,sz300739,sz300814,sh603663,sz301488,sz300322,sz300522,sh605100,sh688343,sh688726,sz300936,sz300400,sh603678,sh603050,sh603773,sh688138,sh688800,sh688456,sz300345,sh688227,sh688609,sh603800,sh688312,sz300302,sz300249,sz301018,sh603139,sz002491,sh605288,sh603203,sh688368,sh603985,sh688097,sz301127,sz002654,sh603738,sz301696,sh688003,sh603109,sh688383,sz301326,sh605289,sz002046,sh600683,sh688095,sh688153,sz001360,sh603929,sh603373,sz300821,sz300767,sh603978,sz300959""".split(",")


def ts():
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _session():
    s = requests.Session()
    s.trust_env = False
    return s


# ---------------- 数据层 ----------------
def fetch_sina_kline(session, code, days=35):
    """新浪日K,返回 list[dict] OHLCV"""
    url = f"{SINA_KLINE}?symbol={code}&scale=240&ma=no&datalen={days}"
    try:
        r = session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        m = re.search(r"var x\((.*)\)", r.text, re.S)
        if not m:
            return []
        data = json.loads(m.group(1))
        out = []
        for d in data:
            out.append({
                "day": d["day"],
                "open": float(d["open"]), "high": float(d["high"]),
                "low": float(d["low"]), "close": float(d["close"]),
                "volume": float(d["volume"]),
            })
        return out
    except Exception:
        return []


def fetch_batch(pool, max_workers=12):
    """并发拉取K线"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    result = {}
    sess = _session()
    # 每个线程独立 session 更稳,这里用单 session(新浪无强限流,实测快)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_sina_kline, sess, c): c for c in pool}
        for f in as_completed(futs):
            c = futs[f]
            try:
                k = f.result()
                if len(k) >= 21:
                    result[c] = k
            except Exception:
                pass
    return result


# ---------------- 6大形态策略(Sequoia-X 复刻) ----------------
def calc_features(k):
    """计算形态所需特征"""
    closes = np.array([x["close"] for x in k])
    highs = np.array([x["high"] for x in k])
    lows = np.array([x["low"] for x in k])
    vols = np.array([x["volume"] for x in k])
    opens = np.array([x["open"] for x in k])
    n = len(closes)
    f = {
        "close": closes[-1], "open": opens[-1], "high": highs[-1], "low": lows[-1],
        "vol": vols[-1],
        "chg": (closes[-1] / closes[-2] - 1) * 100 if n > 1 else 0,  # 当日涨跌幅
        "ma5": closes[-5:].mean() if n >= 5 else closes.mean(),
        "ma20": closes[-20:].mean() if n >= 20 else closes.mean(),
        "vol_ma5": vols[-6:-1].mean() if n >= 6 else vols.mean(),
        "high20_prev": highs[-21:-1].max() if n >= 21 else highs[:-1].max(),
        "close_max20": closes[-20:].max(),
        "close_min20": closes[-20:].min(),
        "ret20": (closes[-1] / closes[-20] - 1) * 100 if n >= 20 else 0,
        "is_yang": closes[-1] > opens[-1],
        "amt_yi": closes[-1] * vols[-1] / 1e8,  # 粗略成交额(亿),volume为手→*100股?新浪volume为股数近似
    }
    f["vol_ratio"] = f["vol"] / f["vol_ma5"] if f["vol_ma5"] > 0 else 1
    return f


def detect_shapes(f):
    """返回命中的形态列表和形态分"""
    hits = []
    score = 0
    # 1 海龟突破:创20日新高 + 放量 + 阳线
    if f["close"] >= f["high20_prev"] and f["vol_ratio"] >= 1.3 and f["is_yang"]:
        hits.append("海龟突破"); score += 30
    # 2 均线放量突破:收盘上穿MA20 + 量比>1.5
    if f["close"] > f["ma20"] and f["vol_ratio"] >= 1.5:
        hits.append("均线放量"); score += 25
    # 3 高而窄旗形:20日涨幅>12% 且近5日振幅收窄
    amp5 = (f["high"] - f["low"]) / f["close"] * 100
    if f["ret20"] > 12 and amp5 < 6 and f["vol_ratio"] >= 1.2:
        hits.append("旗形突破"); score += 22
    # 4 涨停洗盘回踩:近20日有过涨停,今日回踩MA5附近收阳
    if f["ret20"] > 8 and abs(f["close"] - f["ma5"]) / f["close"] < 0.03 and f["is_yang"]:
        hits.append("涨停洗盘"); score += 20
    # 5 上升趋势跌停反包:当日大跌但仍在MA20上方(低吸形态)
    if f["chg"] < -3 and f["close"] > f["ma20"]:
        hits.append("跌停反包"); score += 15
    # 6 RPS相对强度:20日涨幅居前 + 接近20日高点
    if f["ret20"] > 20 and f["close"] >= f["close_max20"] * 0.97:
        hits.append("RPS强势"); score += 25
    return hits, score


# ---------------- quant_v9 广度择时 ----------------
def market_breadth(session, sample_pool=None):
    """用腾讯中性样本的当日涨跌家数比作市场广度(v9用全市场涨跌家数比)
    关键:样本必须跨市场均匀、不按涨跌挑,否则强势股池会带来幸存者偏差"""
    pool = sample_pool or BREADTH_POOL
    up = down = flat = 0
    # 分批(腾讯单次URL不宜过长)
    for i in range(0, len(pool), 60):
        batch = pool[i:i+60]
        try:
            r = session.get(TENCENT_SPOT + ",".join(batch),
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            for line in r.text.split(";"):
                if "=" not in line or "~" not in line:
                    continue
                p = line.split("~")
                if len(p) < 33:
                    continue
                try:
                    chg = float(p[32])
                except (ValueError, IndexError):
                    continue
                if chg > 0: up += 1
                elif chg < 0: down += 1
                else: flat += 1
        except Exception:
            continue
    total = up + down
    breadth = up / total if total else 0.5
    return breadth, up, down, flat


def breadth_decision(breadth, threshold=0.48):
    """二态择时:广度>=阈值满仓,否则空仓避险"""
    if breadth >= 0.62:
        return "🟢 友好", "可正常出手,仓位充足", 1.0
    elif breadth >= threshold:
        return "🟡 分歧", "精选形态最强的少数标的,控制仓位", 0.5
    else:
        return "🔴 退潮", "空仓避险,今日不选股(v9纪律)", 0.0


# ---------------- 评分排序 ----------------
def fetch_names(session, codes):
    """腾讯批量取股票真实名称"""
    names = {}
    for i in range(0, len(codes), 60):
        batch = codes[i:i+60]
        try:
            r = session.get(TENCENT_SPOT + ",".join(batch),
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            for line in r.text.split(";"):
                if "=" not in line or "~" not in line:
                    continue
                p = line.split("~")
                if len(p) < 3:
                    continue
                raw = p[0].split("=")[0].replace("v_", "").strip()
                names[raw] = p[1]
        except Exception:
            continue
    return names


def is_excluded(code, f):
    """硬过滤:科创板 + 低价股"""
    num = re.sub(r"\D", "", code)
    # 剔除科创板 688/689
    if EXCLUDE_STAR and (num.startswith("688") or num.startswith("689")):
        return True
    # 剔除股价低于下限
    if f["close"] < MIN_PRICE:
        return True
    return False


def build_ranking(kdata, names=None):
    names = names or {}
    rows = []
    for code, k in kdata.items():
        f = calc_features(k)
        if is_excluded(code, f):
            continue
        hits, shape_score = detect_shapes(f)
        if not hits:
            continue
        # 综合分 = 形态分 + 量能 + 位置 + 动量
        vol_bonus = min(f["vol_ratio"], 4) / 4 * 15
        pos_bonus = 10 if f["close"] >= f["close_max20"] * 0.98 else 5
        mom_bonus = min(max(f["ret20"], 0), 40) / 40 * 10
        # 追高惩罚:当日涨幅>9%(主板涨停/创业板20cm)追高风险大,扣分
        chase_penalty = 12 if f["chg"] > 9 else (6 if f["chg"] > 6 else 0)
        total = shape_score + vol_bonus + pos_bonus + mom_bonus - chase_penalty
        rows.append({
            "code": code,
            "name": names.get(code, code),
            "score": round(total, 1),
            "shapes": "+".join(hits),
            "shape_cnt": len(hits),
            "chg": round(f["chg"], 2),
            "vol_ratio": round(f["vol_ratio"], 2),
            "ret20": round(f["ret20"], 1),
            "close": round(f["close"], 2),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("score", ascending=False).reset_index(drop=True)
    return df


# ---------------- DEMO ----------------
def gen_demo(kdata):
    """用真实K线但不加形态门槛,保证有输出用于演示(标注DEMO)"""
    rows = []
    for code, k in list(kdata.items())[:60]:
        f = calc_features(k)
        if is_excluded(code, f):
            continue
        hits, ss = detect_shapes(f)
        if not hits:
            hits, ss = ["DEMO样本"], 20
        rows.append({
            "code": code, "name": code, "score": round(ss + 30, 1),
            "shapes": "+".join(hits), "shape_cnt": len(hits),
            "chg": round(f["chg"], 2), "vol_ratio": round(f["vol_ratio"], 2),
            "ret20": round(f["ret20"], 1), "close": round(f["close"], 2),
        })
    return pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)


# ---------------- 输出 ----------------
def save_csv(df, path):
    if not df.empty:
        df.to_csv(path, index=False, encoding="utf-8-sig")


def draw_chart(df, breadth, decision, label, path):
    if df.empty:
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for fp in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]:
        if os.path.exists(fp):
            font_manager.fontManager.addfont(fp)
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK JP", "Noto Sans CJK SC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    top = df.head(10).iloc[::-1]
    fig, ax = plt.subplots(figsize=(10, 5.5))
    colors = ["#c0392b" if s >= 70 else "#e67e22" if s >= 55 else "#5b8ff9" for s in top["score"]]
    ax.barh(top["code"], top["score"], color=colors, height=0.62, zorder=3)
    ax.grid(axis="x", linestyle="--", alpha=0.3, zorder=0)
    for i, (v, sh) in enumerate(zip(top["score"], top["shapes"])):
        ax.text(v + 0.5, i, f"{v:.1f} | {sh[:18]}", va="center", fontsize=8)
    ax.set_xlim(0, max(top["score"]) * 1.5)
    ax.set_title(f"形态选股 Top10 · 广度{breadth:.0%} [{decision[0].replace(chr(0x1F7E2),'').replace(chr(0x1F7E1),'').replace(chr(0x1F534),'').strip()}] ({label})", fontsize=13, fontweight="bold")
    ax.set_xlabel("综合形态分(海龟/RPS/旗形/均线放量/涨停洗盘/跌停反包)")
    plt.tight_layout()
    plt.savefig(path, dpi=130, bbox_inches="tight")
    plt.close()


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--no-chart", action="store_true")
    ap.add_argument("--pool", type=str, default=None, help="自定义代码文件(.txt,每行如 sh600519)")
    args = ap.parse_args()

    print("=" * 60)
    print(" A股形态选股 + 广度择时  [Sequoia-X形态 × quant_v9择时]")
    print(f" 过滤条件:剔除科创板({'开' if EXCLUDE_STAR else '关'}) | 股价≥{MIN_PRICE:.0f}元")
    print("=" * 60)

    pool = POOL
    if args.pool and os.path.exists(args.pool):
        with open(args.pool) as fh:
            pool = [x.strip() for x in fh if x.strip()]
    raw_cnt = len(pool)
    if EXCLUDE_STAR:
        pool = [c for c in pool if not re.sub(r"\D", "", c).startswith(("688", "689"))]
    print(f"[*] 股票池 {len(pool)} 只(原始{raw_cnt},剔除科创板{raw_cnt-len(pool)}),拉取新浪日K...")

    t0 = time.time()
    kdata = fetch_batch(pool)
    print(f"[*] 有效K线 {len(kdata)} 只,耗时 {time.time()-t0:.1f}s")
    if not kdata:
        print("[!] 新浪K线不可用,降级 DEMO 空池提示")
        print("[!] 请检查网络后重试;脚本不会因数据源失败而崩溃。")
        return

    sess = _session()
    breadth, up, down, flat = market_breadth(sess)
    dec = breadth_decision(breadth)
    print(f"[*] 市场广度 {breadth:.1%} (中性样本 涨{up}/跌{down}/平{flat}) → {dec[0]}: {dec[1]}")

    names = fetch_names(sess, list(kdata.keys()))
    if args.demo:
        df = gen_demo(kdata)
        label = "DEMO"
    else:
        df = build_ranking(kdata, names)
        label = "真实K线"

    if df.empty:
        print("[*] 无股票命中形态(可能市场处于退潮/震荡,无强势形态)")

    print(f"\n[*] 命中形态 {len(df)} 只,展示 Top{min(args.top, len(df))}\n")
    if not df.empty:
        show = df.head(args.top)
        print(f"{'代码':<10}{'名称':<9}{'综合分':>7}{'涨跌幅':>8}{'量比':>7}{'追高':>6}  形态")
        print("-" * 78)
        for _, r in show.iterrows():
            chase = "⚠高" if r["chg"] > 9 else ("中" if r["chg"] > 6 else "低")
            print(f"{r['code']:<10}{str(r['name']):<9}{r['score']:>7.1f}{r['chg']:>7.2f}%{r['vol_ratio']:>7.2f}{chase:>6}  {r['shapes']}")

    stamp = ts()
    csv_path = os.path.join(OUTPUT_DIR, f"形态选股结果_{stamp}.csv")
    save_csv(df, csv_path)
    if not df.empty:
        print(f"\n[+] 结果CSV: {csv_path}")

    if not args.no_chart and not df.empty:
        png = os.path.join(OUTPUT_DIR, f"形态选股评分图_{stamp}.png")
        draw_chart(df, breadth, dec, label, png)
        print(f"[+] 评分图: {png}")

    # 择时风控结论
    print("\n" + "=" * 60)
    print(f" 择时结论: {dec[0]} — {dec[1]} (建议仓位系数 {dec[2]:.0%})")
    print("=" * 60)
    print("⚠️ 量化研究用途,形态选股存在假突破风险,需结合题材/消息面二次确认,不构成投资建议。")


if __name__ == "__main__":
    main()
