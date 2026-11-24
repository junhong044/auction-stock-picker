#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
A 股集合竞价选股系统 (Auction Stock Picker)
=====================================================================
对标 GitHub 主流竞价选股方案,采用「6 约束硬过滤 + 9 维加权评分」架构。

核心逻辑
---------
竞价选股发生在 9:15-9:25 集合竞价阶段,9:25 产生开盘价。
本系统在 9:25-9:30 抓取全市场竞价快照,以「开盘涨幅」为核心,
结合量比、竞价成交额、换手、买盘力量、市值等维度综合评分排序。

数据源(自动降级)
------------------
1. eastmoney : 东财 push2 全市场快照(字段最全,推荐,本机网络通常可通)
2. tencent   : 腾讯 qt.gtimg.cn 批量行情(需 --pool 指定代码池,或内置示例池)
3. demo      : 内置可复现模拟竞价数据(任何环境都能演示完整流程)

用法
-----
    python3.11 auction_stock_picker.py --demo              # 演示模式(无需网络)
    python3.11 auction_stock_picker.py --source eastmoney  # 东财全市场扫描
    python3.11 auction_stock_picker.py --source tencent --pool mypool.csv
    python3.11 auction_stock_picker.py --top 20 --min-gap 2 --max-gap 9.5
    python3.11 auction_stock_picker.py --no-chart          # 不生成图表

结果保存到 OUTPUT_DIR 指定目录(默认脚本所在目录),文件名带时间戳。
=====================================================================
"""

import argparse
import csv
import os
import random
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime

import requests

try:
    import pandas as pd
except ImportError:
    print("缺少 pandas,请执行: pip3 install pandas")
    sys.exit(1)

# =====================================================================
# 配置区
# =====================================================================
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"


@dataclass
class Constraints:
    """6 条硬约束 —— 不满足即淘汰"""
    min_gap: float = 2.0        # 竞价涨幅下限(%):<2 说明高开力度不足
    max_gap: float = 9.5        # 竞价涨幅上限(%):>=9.8 接近一字板,买不到
    min_amount_wan: float = 1000.0   # 竞价成交额下限(万元)
    min_vol_ratio: float = 1.2       # 量比下限
    min_float_cap: float = 20.0      # 流通市值下限(亿)
    max_float_cap: float = 500.0     # 流通市值上限(亿)
    exclude_st: bool = True          # 排除 ST / *ST
    exclude_one_word: bool = True    # 排除一字涨停(卖盘为 0)


@dataclass
class Weights:
    """9 维评分权重(总和建议 100)"""
    gap: float = 20       # 1. 竞价涨幅
    vol_ratio: float = 15 # 2. 量比
    amount: float = 12    # 3. 竞价成交额
    turnover: float = 10  # 4. 换手率
    bid_power: float = 12 # 5. 买盘力量(委比)
    float_cap: float = 10 # 6. 流通市值适中度
    limit_dist: float = 8 # 7. 涨停距离
    activeness: float = 8 # 8. 活跃度(振幅)
    momentum: float = 5   # 9. 动量(涨速/主力净占比)


# 9 维中文名,用于报告
DIM_NAMES = {
    "gap": "竞价涨幅", "vol_ratio": "量比", "amount": "竞价额",
    "turnover": "换手率", "bid_power": "买盘力量", "float_cap": "市值适中",
    "limit_dist": "涨停距离", "activeness": "活跃度", "momentum": "动量",
}

# 东财全市场 A 股板块过滤:沪深主板+创业板+科创板(排除北交所)
EM_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
EM_FIELDS = "f12,f14,f2,f3,f4,f5,f6,f8,f10,f15,f16,f17,f18,f20,f21,f22,f62,f184"


def make_session():
    """禁用环境代理,直连行情接口"""
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": UA})
    return s


# =====================================================================
# 数据源
# =====================================================================
def _f(val, default=0.0):
    """东财 '-' / None 转浮点数"""
    try:
        if val in ("-", "", None):
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def fetch_eastmoney(session, max_pages=60):
    """东财 push2 全市场快照。返回统一字段列表。"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    results, page = [], 1
    while page <= max_pages:
        params = {
            "pn": page, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f3", "fs": EM_FS, "fields": EM_FIELDS,
        }
        try:
            r = session.get(url, params=params, timeout=15,
                            headers={"Referer": "https://quote.eastmoney.com/"})
            data = r.json().get("data") or {}
            diff = data.get("diff") or []
            if not diff:
                break
            for d in diff:
                preclose = _f(d.get("f18"))
                openp = _f(d.get("f17"))
                if openp <= 0:
                    openp = _f(d.get("f2"))  # 未开盘则用最新价
                gap = (openp - preclose) / preclose * 100 if preclose > 0 else 0.0
                total_ask = _f(d.get("f20"))  # 总市值仅占位,买盘力量用主力净占比近似
                results.append({
                    "code": str(d.get("f12", "")),
                    "name": str(d.get("f14", "")),
                    "price": _f(d.get("f2")),
                    "preclose": preclose,
                    "open": openp,
                    "gap": round(gap, 2),
                    "amount_wan": round(_f(d.get("f6")) / 1e4, 1),
                    "turnover": _f(d.get("f8")),
                    "vol_ratio": _f(d.get("f10")),
                    "float_cap": round(_f(d.get("f21")) / 1e8, 2),
                    "high": _f(d.get("f15")),
                    "low": _f(d.get("f16")),
                    "weibi": _f(d.get("f184")),   # 主力净占比(%)作委比代理
                    "speed": _f(d.get("f22")),    # 涨速
                    "ask1_vol": 1.0,              # 东财快照无盘口,默认有卖盘
                    "source": "EM",
                })
            total = data.get("total", 0)
            if page * 100 >= total:
                break
            page += 1
        except Exception as e:
            if page == 1:
                raise RuntimeError(f"东财接口失败: {e}")
            break
    return results


def _code_to_tencent(code):
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("0", "2", "3")):
        return "sz" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    return "sh" + code


def fetch_tencent(session, codes):
    """腾讯批量行情。codes 为 6 位代码列表。"""
    out = []
    for i in range(0, len(codes), 60):
        batch = codes[i:i + 60]
        q = ",".join(_code_to_tencent(c) for c in batch)
        try:
            r = session.get("https://qt.gtimg.cn/q=" + q, timeout=15,
                            headers={"Referer": "https://gu.qq.com/"})
            r.encoding = "gbk"
            for line in r.text.split(";"):
                if "=" not in line or '"' not in line:
                    continue
                body = line.split('"')[1]
                f = body.split("~")
                if len(f) < 50:
                    continue
                try:
                    name, code = f[1], f[2]
                    price, preclose, openp = _f(f[3]), _f(f[4]), _f(f[5])
                    if openp <= 0:
                        openp = price
                    gap = (openp - preclose) / preclose * 100 if preclose > 0 else 0.0
                    bid1, bid1v = _f(f[9]), _f(f[10])
                    ask1, ask1v = _f(f[19]), _f(f[20])
                    wb = 0.0
                    if bid1v + ask1v > 0:
                        wb = round((bid1v - ask1v) / (bid1v + ask1v) * 100, 1)
                    out.append({
                        "code": code, "name": name, "price": price,
                        "preclose": preclose, "open": openp, "gap": round(gap, 2),
                        "amount_wan": _f(f[37]),       # 万元
                        "turnover": _f(f[38]),
                        "vol_ratio": _f(f[49]),
                        "float_cap": _f(f[44]),        # 亿
                        "high": _f(f[33]), "low": _f(f[34]),
                        "weibi": wb, "speed": _f(f[31]),
                        "ask1_vol": ask1v, "source": "TX",
                    })
                except (IndexError, ValueError):
                    continue
        except Exception:
            continue
    return out


def load_pool(path):
    """从 CSV/TXT 读取代码池(支持 code 列或纯代码)"""
    codes = []
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.lower().startswith("code"):
                continue
            token = line.split(",")[0].strip().strip('"')
            if len(token) >= 6 and token[:6].isdigit():
                codes.append(token[:6])
    return list(dict.fromkeys(codes))


DEMO_POOL = [
    "600519", "000858", "300750", "002594", "601318", "600036", "000333",
    "601899", "002475", "300059", "600030", "000725", "601012", "002415",
    "688981", "600900", "000001", "601166", "002230", "300124", "600887",
    "000568", "601288", "002304", "300760", "600276", "601857", "000063",
]


def gen_demo(n=60, seed=20261124):
    """生成可复现的模拟竞价数据,覆盖各种特征以演示过滤与评分"""
    rnd = random.Random(seed)
    bases = DEMO_POOL + [f"{600000+i:06d}" for i in range(n - len(DEMO_POOL))]
    out = []
    for i, code in enumerate(bases[:n]):
        preclose = round(rnd.uniform(8, 120), 2)
        gap = round(rnd.gauss(3.5, 3.0), 2)
        gap = max(-5.0, min(10.0, gap))
        openp = round(preclose * (1 + gap / 100), 2)
        price = round(openp * rnd.uniform(0.99, 1.02), 2)
        amount = round(rnd.uniform(300, 9000), 1)
        turnover = round(max(0.01, rnd.gauss(2.0, 1.6)), 2)
        vol_ratio = round(max(0.2, rnd.gauss(2.8, 1.8)), 2)
        float_cap = round(rnd.uniform(15, 600), 1)
        weibi = round(rnd.uniform(-60, 80), 1)
        speed = round(rnd.uniform(-1.5, 2.5), 2)
        ask1 = 0 if (gap >= 9.8 and rnd.random() < 0.6) else rnd.randint(50, 5000)
        name = f"演示{code[-4:]}"
        if i % 17 == 0:
            name = "*ST" + name
        out.append({
            "code": code, "name": name, "price": price, "preclose": preclose,
            "open": openp, "gap": gap, "amount_wan": amount,
            "turnover": turnover, "vol_ratio": vol_ratio, "float_cap": float_cap,
            "high": round(openp * 1.02, 2), "low": round(openp * 0.98, 2),
            "weibi": weibi, "speed": speed, "ask1_vol": ask1, "source": "DEMO",
        })
    return out


# =====================================================================
# 6 约束过滤
# =====================================================================
def apply_constraints(rows, c: Constraints):
    passed, rejected = [], []
    for r in rows:
        reasons = []
        if c.exclude_st and ("ST" in r["name"].upper()):
            reasons.append("ST股")
        if c.exclude_one_word and r["gap"] >= 9.8 and r["ask1_vol"] == 0:
            reasons.append("一字板")
        if not (c.min_gap <= r["gap"] <= c.max_gap):
            reasons.append(f"涨幅{r['gap']}%越界")
        if r["amount_wan"] < c.min_amount_wan:
            reasons.append(f"竞价额{r['amount_wan']}万不足")
        if r["vol_ratio"] < c.min_vol_ratio:
            reasons.append(f"量比{r['vol_ratio']}不足")
        if not (c.min_float_cap <= r["float_cap"] <= c.max_float_cap):
            reasons.append(f"市值{r['float_cap']}亿越界")
        if reasons:
            r2 = dict(r)
            r2["reject_reason"] = "、".join(reasons)
            rejected.append(r2)
        else:
            passed.append(dict(r))
    return passed, rejected


# =====================================================================
# 9 维加权评分
# =====================================================================
def _clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def score_stock(r, w: Weights):
    # 1 竞价涨幅:最佳 4.5%,偏离递减
    s_gap = _clip(100 - abs(r["gap"] - 4.5) * 12)
    # 2 量比:6 倍满分
    s_vr = _clip(r["vol_ratio"] / 6 * 100)
    # 3 竞价额:5000 万满分
    s_amt = _clip(r["amount_wan"] / 5000 * 100)
    # 4 换手率:最佳 3%
    s_to = _clip(100 - abs(r["turnover"] - 3) * 15)
    # 5 买盘力量:委比,>50 满分
    s_bid = _clip((r["weibi"] + 20) / 70 * 100)
    # 6 市值适中:30-150 亿最佳区间
    cap = r["float_cap"]
    s_cap = 100 if 30 <= cap <= 150 else _clip(100 - min(abs(cap - 30), abs(cap - 150)) * 0.5)
    # 7 涨停距离:距涨停 3-5% 空间最佳
    dist = 9.8 - r["gap"]
    s_dist = _clip(100 - abs(dist - 4) * 15)
    # 8 活跃度:振幅 3-8%
    amp = (r["high"] - r["low"]) / r["preclose"] * 100 if r["preclose"] > 0 else 0
    s_act = _clip(100 - abs(amp - 5) * 12)
    # 9 动量:涨速,>1.5% 满分
    s_mom = _clip((r["speed"] + 1) / 2.5 * 100)

    parts = {
        "gap": s_gap, "vol_ratio": s_vr, "amount": s_amt, "turnover": s_to,
        "bid_power": s_bid, "float_cap": s_cap, "limit_dist": s_dist,
        "activeness": s_act, "momentum": s_mom,
    }
    total = (
        s_gap * w.gap + s_vr * w.vol_ratio + s_amt * w.amount +
        s_to * w.turnover + s_bid * w.bid_power + s_cap * w.float_cap +
        s_dist * w.limit_dist + s_act * w.activeness + s_mom * w.momentum
    ) / max(sum(asdict(w).values()), 1)
    out = dict(r)
    out["score"] = round(total, 2)
    for k, v in parts.items():
        out["s_" + k] = round(v, 1)
    return out


# =====================================================================
# 输出
# =====================================================================
def ts():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_csv(rows, path):
    if not rows:
        return
    cols = ["code", "name", "score", "gap", "vol_ratio", "amount_wan", "turnover",
            "float_cap", "weibi", "speed", "price", "open", "preclose", "source"]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        for r in rows:
            wr.writerow(r)


def print_table(rows, top):
    if not rows:
        print("\n[!] 没有股票通过约束,请放宽参数(--min-gap / --min-amount 等)。\n")
        return
    hdr = f"{'排名':<4}{'代码':<8}{'名称':<10}{'总分':>7}{'竞价涨幅':>9}{'量比':>7}{'竞价额万':>10}{'换手%':>7}{'市值亿':>8}{'委比%':>7}"
    print("\n" + "=" * len(hdr))
    print("  A 股集合竞价选股结果  (Top %d)" % min(top, len(rows)))
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for i, r in enumerate(rows[:top], 1):
        print(f"{i:<4}{r['code']:<8}{r['name']:<10}{r['score']:>7.1f}"
              f"{r['gap']:>9.2f}{r['vol_ratio']:>7.2f}{r['amount_wan']:>10.0f}"
              f"{r['turnover']:>7.2f}{r['float_cap']:>8.1f}{r['weibi']:>7.1f}")
    print("=" * len(hdr) + "\n")


def save_chart(rows, path, top=10):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        # 尝试加载中文字体
        for fp in ["/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                   "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]:
            if os.path.exists(fp):
                font_manager.fontManager.addfont(fp)
                plt.rcParams["font.sans-serif"] = [font_manager.FontProperties(fname=fp).get_name()]
                break
        plt.rcParams["axes.unicode_minus"] = False

        top_rows = rows[:top]
        names = [f"{r['code']}\n{r['name'][:4]}" for r in top_rows]
        keys = list(DIM_NAMES.keys())
        w = Weights()
        wsum = sum(asdict(w).values())
        fig, ax = plt.subplots(figsize=(12, 6))
        bottom = [0] * len(top_rows)
        colors = ["#e74c3c", "#e67e22", "#f1c40f", "#2ecc71", "#1abc9c",
                  "#3498db", "#9b59b6", "#34495e", "#7f8c8d"]
        for k, color in zip(keys, colors):
            vals = [r["s_" + k] / 100 * asdict(w)[k] / wsum * 100 for r in top_rows]
            ax.bar(names, vals, bottom=bottom, label=DIM_NAMES[k], color=color, width=0.65)
            bottom = [b + v for b, v in zip(bottom, vals)]
        for i, r in enumerate(top_rows):
            ax.text(i, bottom[i] + 1, f"{r['score']:.1f}", ha="center", fontsize=9, fontweight="bold")
        ax.set_ylabel("加权得分贡献")
        ax.set_title(f"竞价选股 Top{top} 九维评分构成  {datetime.now():%Y-%m-%d %H:%M}")
        ax.legend(loc="upper right", fontsize=8, ncol=3)
        ax.set_ylim(0, 108)
        plt.tight_layout()
        plt.savefig(path, dpi=130)
        plt.close()
        return True
    except Exception as e:
        print(f"[!] 图表生成跳过: {e}")
        return False


# =====================================================================
# 主流程
# =====================================================================
def main():
    ap = argparse.ArgumentParser(description="A 股集合竞价选股系统")
    ap.add_argument("--source", choices=["eastmoney", "tencent", "demo"], default="eastmoney",
                    help="数据源(默认 eastmoney,失败自动降级 demo)")
    ap.add_argument("--demo", action="store_true", help="等同于 --source demo")
    ap.add_argument("--pool", help="腾讯源的代码池 CSV/TXT(纯代码或 code 列)")
    ap.add_argument("--top", type=int, default=15, help="展示前 N 名(默认 15)")
    ap.add_argument("--min-gap", type=float, default=2.0, help="竞价涨幅下限%%")
    ap.add_argument("--max-gap", type=float, default=9.5, help="竞价涨幅上限%%")
    ap.add_argument("--min-amount", type=float, default=1000.0, help="竞价额下限(万元)")
    ap.add_argument("--min-vr", type=float, default=1.2, help="量比下限")
    ap.add_argument("--no-chart", action="store_true", help="不生成图表")
    args = ap.parse_args()

    c = Constraints(min_gap=args.min_gap, max_gap=args.max_gap,
                    min_amount_wan=args.min_amount, min_vol_ratio=args.min_vr)
    w = Weights()
    source = "demo" if args.demo else args.source
    session = make_session()

    print(f"\n[*] 竞价选股启动 | 数据源={source} | {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"[*] 6 约束: 涨幅[{c.min_gap},{c.max_gap}]% 竞价额>={c.min_amount_wan}万 "
          f"量比>={c.min_vol_ratio} 市值[{c.min_float_cap},{c.max_float_cap}]亿")

    rows = []
    if source == "eastmoney":
        try:
            rows = fetch_eastmoney(session)
            if not rows:
                raise RuntimeError("东财返回空数据")
            print(f"[*] 东财快照获取 {len(rows)} 只")
        except Exception as e:
            print(f"[!] 东财源不可用({str(e)[:80]}),尝试降级腾讯源")
            source = "tencent"
    if source == "tencent" and not rows:
        try:
            codes = load_pool(args.pool) if args.pool else DEMO_POOL
            rows = fetch_tencent(session, codes)
            if not rows:
                raise RuntimeError("腾讯返回空数据")
            print(f"[*] 腾讯快照获取 {len(rows)} 只(代码池 {len(codes)} 只)")
        except Exception as e:
            print(f"[!] 腾讯源不可用({str(e)[:80]}),降级 DEMO")
            source = "demo"
    if source == "demo" and not rows:
        rows = gen_demo()
        print(f"[*] DEMO 模拟竞价数据 {len(rows)} 只(种子固定,可复现)")

    passed, rejected = apply_constraints(rows, c)
    print(f"[*] 约束过滤: 通过 {len(passed)} 只 / 淘汰 {len(rejected)} 只")

    scored = [score_stock(r, w) for r in passed]
    scored.sort(key=lambda x: x["score"], reverse=True)

    print_table(scored, args.top)

    stamp = ts()
    if scored:
        csv_path = os.path.join(OUTPUT_DIR, f"竞价选股结果_{stamp}.csv")
        save_csv(scored, csv_path)
        print(f"[+] 结果 CSV: {csv_path}")
    else:
        print("[!] 无候选股票,未生成 CSV")

    if not args.no_chart and scored:
        chart_path = os.path.join(OUTPUT_DIR, f"竞价选股评分图_{stamp}.png")
        if save_chart(scored, chart_path, min(args.top, 10)):
            print(f"[+] 评分图表: {chart_path}")

    if rejected:
        print(f"[i] 淘汰样例(前3):")
        for r in rejected[:3]:
            print(f"    {r['code']} {r['name']}: {r['reject_reason']}")
    print()
    return scored


if __name__ == "__main__":
    main()
