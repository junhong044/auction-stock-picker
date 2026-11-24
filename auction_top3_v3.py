#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
A 股竞价选股 v3 ——  günlük 最优 Top3 + 市场情绪开关 + 稳健评分
=====================================================================
融合 2026-11 GitHub 最新调研的 4 类方法(此前 v1/v2/v5 均未覆盖):

  ① 市场情绪开关(广度择时)  —— 来自 v5-Top3 情绪周期 / ashare-sentiment 7态分类
     用全市场竞价快照算"情绪温度",退潮日宁可不推,从源头避开大面。
  ② 竞价三因子解耦(XTick)   —— 来自同花顺 XTick / quant.10jqka
     jjzf 价格意愿 + jje 资金规模 + noe 买卖失衡(委比代理,强调持续性)。
  ③ 稳健评分(OK 折外思想)   —— 来自 tick-stock-panel 的 K 段稳健分
     稳健分 = 0.6×加权均分 + 0.4×最差维度分,避免单一维度虚高拉总分。
  ④ 双区输出 + 开盘确认清单 —— 来自 xunlongjue_doc 双区 / XTick 两阶段
     🏆首选区 / 📋观察区;每只给 9:30-9:35 的"确认条件 + 放弃条件"。

硬过滤(沿用用户设定):剔除科创板(688/689)、股价<15元、ST、一字板。

架构:复用 v1 数据层 -> 情绪层 -> 三因子稳健评分 -> 双区 Top3 -> 确认清单
=====================================================================
"""
import argparse
import csv
import os
import statistics
import sys
from dataclasses import dataclass, asdict
from datetime import datetime

import pandas as pd

import auction_stock_picker as base

try:
    from zoneinfo import ZoneInfo
    _CN = ZoneInfo("Asia/Shanghai")
except Exception:
    _CN = None


def now_cn():
    return datetime.now(_CN) if _CN else datetime.now()


# =====================================================================
# 配置
# =====================================================================
@dataclass
class ConstraintsV3:
    min_gap: float = 2.0
    max_gap: float = 7.0        # >7% 兑现压力,收紧
    min_vol_ratio: float = 3.0  # 量比<3 多为诱多
    min_amount_wan: float = 2000.0
    min_turnover: float = 0.2
    min_float_cap: float = 20.0
    max_float_cap: float = 300.0
    min_price: float = 15.0     # 股价<15元剔除
    exclude_star: bool = True   # 剔除科创板
    exclude_st: bool = True
    exclude_one_word: bool = True


@dataclass
class WeightsV3:
    """三因子解耦 + 辅助维度,权重和=100"""
    jjzf: float = 20          # 价格意愿(竞价涨幅质量)
    jje: float = 18           # 资金规模(竞价额池内百分位)
    noe: float = 18           # 买卖失衡(委比+涨速持续性)
    vol_ratio: float = 14     # 放量强度
    turnover_fit: float = 12  # 换手达标
    float_cap: float = 8      # 市值适中
    activeness: float = 6     # 活跃度
    momentum: float = 4       # 动量


DIM_NAMES = {
    "jjzf": "价格意愿", "jje": "资金规模", "noe": "买卖失衡",
    "vol_ratio": "放量强度", "turnover_fit": "换手达标",
    "float_cap": "市值适中", "activeness": "活跃度", "momentum": "动量",
}

# 情绪温度 -> 系数/推荐数量
SENTIMENT_RULES = {
    "主升": {"coef": 1.00, "buy_n": 3, "desc": "赚钱效应强,正常出手"},
    "健康": {"coef": 1.00, "buy_n": 3, "desc": "市场情绪正常,可出手"},
    "分歧": {"coef": 0.93, "buy_n": 2, "desc": "多空分歧,减量降仓"},
    "退潮": {"coef": 0.85, "buy_n": 0, "desc": "退潮防守,只观察不出手"},
}
BUY_THRESHOLD = 70.0   # 首选区稳健分门槛
WATCH_THRESHOLD = 58.0  # 观察区门槛


# =====================================================================
# ① 市场情绪开关(广度择时)
# =====================================================================
def compute_market_sentiment(rows):
    """用全市场竞价快照算情绪温度。
    样本不足(<50只)时返回中性的'健康',避免小池误判。
    """
    n = len(rows)
    if n < 50:
        return {
            "state": "健康", "temp": 50.0, "coef": 1.0, "buy_n": 3,
            "desc": "样本不足(<%d只),按中性处理" % 50,
            "n": n, "high_ratio": 0, "limit_ratio": 0,
            "strong_ratio": 0, "updown": 0,
            "sample_warning": True,
        }
    gaps = [r.get("gap", 0) for r in rows]
    high = sum(1 for g in gaps if g >= 2) / n * 100          # 高开>2%占比
    limit = sum(1 for g in gaps if g >= 9.8) / n * 100       # 涨停/接近涨停占比
    strong = sum(1 for g in gaps if g >= 5) / n * 100        # 强势占比
    down = sum(1 for g in gaps if g < 0)
    up = sum(1 for g in gaps if g > 0)
    updown = up / max(down, 1)                                # 涨跌家数比

    # 温度:高开广度(40)+强势广度(25)+涨停效应(20)+涨跌比(15)
    temp = (
        min(high / 30 * 40, 40) +
        min(strong / 12 * 25, 25) +
        min(limit / 3 * 20, 20) +
        min(updown / 3 * 15, 15)
    )
    temp = round(min(temp, 100), 1)
    # 阈值经典型市场校准:常态~44→健康,强势~82→主升,弱势~13→退潮
    if temp >= 65:
        state = "主升"
    elif temp >= 42:
        state = "健康"
    elif temp >= 26:
        state = "分歧"
    else:
        state = "退潮"

    # 样本偏差检测:高开>2%占比>75%在真实全市场极罕见,
    # 几乎总是"接口按涨幅降序截断的强势股子集"(如盘后/降级小池)。
    # 此时情绪不可信,按中性"健康"处理并降一档出手数,避免误报主升。
    biased = high > 75
    if biased:
        state = "健康"
        temp = 50.0
    rule = SENTIMENT_RULES[state]
    buy_n = rule["buy_n"]
    if biased:
        buy_n = min(buy_n, 2)  # 样本有偏时更保守
    return {
        "state": state, "temp": temp, "coef": rule["coef"],
        "buy_n": buy_n,
        "desc": ("样本为强势股子集(高开占比异常),已按中性保守处理" if biased else rule["desc"]),
        "n": n,
        "high_ratio": round(high, 1), "limit_ratio": round(limit, 1),
        "strong_ratio": round(strong, 1), "updown": round(updown, 2),
        "sample_warning": biased,
    }


# =====================================================================
# 硬约束
# =====================================================================
def _is_star(code):
    return str(code).startswith(("688", "689"))


def apply_constraints_v3(rows, c: ConstraintsV3):
    passed, rejected = [], []
    for r in rows:
        reasons = []
        code = str(r.get("code", ""))
        name = str(r.get("name", ""))
        price = float(r.get("price", 0) or 0)
        if c.exclude_star and _is_star(code):
            reasons.append("科创板")
        if c.exclude_st and "ST" in name.upper():
            reasons.append("ST股")
        if price > 0 and price < c.min_price:
            reasons.append(f"股价{price:.1f}<15元")
        if c.exclude_one_word and float(r.get("gap", 0)) >= 9.8 and float(r.get("ask1_vol", 1)) == 0:
            reasons.append("一字板")
        gap = float(r.get("gap", 0))
        if not (c.min_gap <= gap <= c.max_gap):
            reasons.append(f"高开{gap}%越界")
        if float(r.get("vol_ratio", 0)) < c.min_vol_ratio:
            reasons.append(f"量比不足")
        if float(r.get("amount_wan", 0)) < c.min_amount_wan:
            reasons.append(f"竞价额不足")
        if float(r.get("turnover", 0)) < c.min_turnover:
            reasons.append(f"换手不足")
        cap = float(r.get("float_cap", 0))
        if not (c.min_float_cap <= cap <= c.max_float_cap):
            reasons.append(f"市值越界")
        if reasons:
            r2 = dict(r)
            r2["reject_reason"] = "、".join(reasons)
            rejected.append(r2)
        else:
            passed.append(dict(r))
    return passed, rejected


# =====================================================================
# ②③ 三因子解耦 + 稳健评分
# =====================================================================
def _clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def score_v3_batch(rows, w: WeightsV3, sentiment):
    """三因子解耦评分 + 稳健分(0.6加权 + 0.4最差维度) + 情绪系数。
    资金规模用池内百分位(时段无关),胜过小样本绝对阈值。
    """
    if not rows:
        return []
    df = pd.DataFrame(rows).copy()
    for k in ["gap", "vol_ratio", "amount_wan", "turnover", "float_cap",
              "weibi", "speed", "price", "preclose", "high", "low", "open"]:
        if k in df.columns:
            df[k] = pd.to_numeric(df[k], errors="coerce").fillna(0.0)
    df["amt_ratio"] = df["amount_wan"] / df["float_cap"].replace(0, 1)
    df["amt_pct"] = df["amt_ratio"].rank(pct=True) * 100

    wd = asdict(w)
    scored = []
    for r in df.to_dict("records"):
        p = {}
        # jjzf 价格意愿:4.5%最佳,2-6%健康,>7%兑现压力惩罚
        g = r["gap"]
        p["jjzf"] = _clip(100 - abs(g - 4.5) * 16) if g <= 7 else _clip(70 - (g - 7) * 12)
        # jje 资金规模:池内百分位,>90%疑对倒轻扣
        ap = r["amt_pct"]
        p["jje"] = _clip(ap if ap <= 90 else 100 - (ap - 90) * 3)
        # noe 买卖失衡:委比为主,涨速持续性确认(二者同向才给高分)
        weibi, speed = r["weibi"], r["speed"]
        base_noe = _clip((weibi + 20) / 70 * 100)
        cont_bonus = 8 if (weibi > 0 and speed > 0) else (-10 if (weibi < 0 and speed < 0) else 0)
        p["noe"] = _clip(base_noe + cont_bonus)
        # 放量强度:3合格,8满分
        p["vol_ratio"] = _clip((r["vol_ratio"] - 1) / 7 * 100)
        # 换手达标:按市值分档
        cap = r["float_cap"]
        lo_t = 0.5 if cap < 50 else 0.3
        t = r["turnover"]
        if t < lo_t:
            p["turnover_fit"] = _clip(100 - (lo_t - t) * 120)
        elif t > 25:
            p["turnover_fit"] = _clip(100 - (t - 25) * 3)
        else:
            p["turnover_fit"] = 100
        # 市值适中:30-100亿
        p["float_cap"] = 100 if 30 <= cap <= 100 else _clip(100 - min(abs(cap - 30), abs(cap - 100)) * 1.2)
        # 活跃度:振幅3-8%
        hi = r.get("high", 0) or 0
        lo = r.get("low", 0) or 0
        if hi > 0 and lo > 0 and r["preclose"] > 0:
            amp = (hi - lo) / r["preclose"] * 100
        else:
            amp = abs(r.get("price", 0) - r.get("open", 0)) / max(r["preclose"], 1) * 100
        p["activeness"] = _clip(100 - abs(amp - 5) * 12)
        # 动量
        p["momentum"] = _clip((speed + 1.5) / 3 * 60 + (weibi + 20) / 80 * 40)

        weighted = sum(p[k] * wd[k] for k in p) / max(sum(wd.values()), 1)
        worst = min(p.values())          # 最差维度(折外思想:短板决定上限)
        robust = 0.6 * weighted + 0.4 * worst
        final = _clip(robust * sentiment["coef"])

        out = dict(r)
        out["score"] = round(final, 2)
        out["score_raw"] = round(weighted, 2)
        out["score_worst"] = round(worst, 2)
        out["score_robust"] = round(robust, 2)
        out["amt_ratio"] = round(r["amt_ratio"], 1)
        out["amt_pct"] = round(ap, 1)
        for k, v in p.items():
            out["s_" + k] = round(v, 1)
        scored.append(out)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


# =====================================================================
# ④ 双区 Top3 + 确认清单
# =====================================================================
def select_zones(scored, sentiment):
    """返回 (首选区, 观察区)。退潮日 buy_n=0,首选区为空。"""
    buy_n = sentiment["buy_n"]
    buy, watch = [], []
    for r in scored:
        s = r["score"]
        if s >= BUY_THRESHOLD and len(buy) < buy_n:
            buy.append(r)
        elif s >= WATCH_THRESHOLD and len(watch) < 3:
            watch.append(r)
        if len(buy) >= buy_n and len(watch) >= 3:
            break
    return buy, watch


def confirm_plan(r):
    """9:30-9:35 开盘确认:条件成立才买,破位放弃。"""
    open_ref = float(r.get("open", 0) or r.get("price", 0))
    yes = []
    if r["weibi"] > 10:
        yes.append("委比为正买盘占优")
    if r["speed"] > 0:
        yes.append("涨速向上")
    if r["s_jje"] >= 60:
        yes.append("竞价资金达成交")
    if r["s_jjzf"] >= 70:
        yes.append("高开幅度健康")
    return {
        "buy_cond": f"开盘价站稳竞价价 {open_ref:.2f} 且 5分钟内放量不破",
        "drop_cond": "高开后跌破竞价价且卖压放大 → 放弃",
        "signals": "、".join(yes[:3]) if yes else "综合评分居前",
    }


def print_report(scored, buy, watch, sentiment, c):
    n = sentiment["n"]
    print("\n" + "=" * 74)
    if sentiment.get("biased_note") or sentiment["sample_warning"]:
        title = "  市场情绪开关(样本有偏,已按中性保守处理)"
    else:
        title = "  市场情绪开关(全市场竞价广度)"
    print(title)
    print("=" * 74)
    label = {"主升": "🔥", "健康": "✅", "分歧": "⚡", "退潮": "🛑"}[sentiment["state"]]
    print(f"  情绪状态: {label} {sentiment['state']}  |  温度 {sentiment['temp']:.1f}/100  |  {sentiment['desc']}")
    print(f"  高开>2%占比 {sentiment['high_ratio']}% | 强势>5%占比 {sentiment['strong_ratio']}% "
          f"| 涨停占比 {sentiment['limit_ratio']}% | 涨跌家数比 {sentiment['updown']}  (样本 {n} 只)")
    if sentiment["state"] == "退潮":
        print("  ⚠️ 退潮防守:今日首选买入区关闭,以下仅为观察,不建议实盘出手。")

    print("\n" + "=" * 74)
    print(f"  🏆 首选买入区 Top{len(buy)}  (稳健分≥{BUY_THRESHOLD:.0f})")
    print("=" * 74)
    if not buy:
        print("  (空) —— 无票达到门槛或情绪退潮,宁缺毋滥。")
    else:
        hdr = (f"{'#':<3}{'代码':<8}{'名称':<10}{'稳健分':>7}{'情绪系数分':>9}"
               f"{'高开%':>7}{'量比':>6}{'竞价额万':>9}{'委比':>7}{'资规':>6}")
        print(hdr)
        print("-" * len(hdr))
        for i, r in enumerate(buy, 1):
            print(f"{i:<3}{r['code']:<8}{str(r['name'])[:9]:<10}{r['score_robust']:>7.1f}"
                  f"{r['score']:>9.1f}{r['gap']:>7.2f}{r['vol_ratio']:>6.2f}"
                  f"{r['amount_wan']:>9.0f}{r['weibi']:>7.1f}{r['s_jje']:>6.0f}")

    print("\n" + "=" * 74)
    print(f"  📋 观察区 Top{len(watch)}  (稳健分 {WATCH_THRESHOLD:.0f}-{BUY_THRESHOLD:.0f})")
    print("=" * 74)
    if not watch:
        print("  (空)")
    else:
        for i, r in enumerate(watch, 1):
            print(f"  {i}. {r['code']} {str(r['name'])[:10]:<11} 稳健分 {r['score']:.1f} "
                  f"高开 {r['gap']}% 量比 {r['vol_ratio']}")

    if buy:
        print("\n" + "=" * 74)
        print("  🔍 首选 Top3 深度诊断 + 开盘确认清单")
        print("=" * 74)
        for i, r in enumerate(buy[:3], 1):
            wd = asdict(WeightsV3())
            items = [(DIM_NAMES[k], r["s_" + k], wd[k]) for k in DIM_NAMES]
            strong = sorted(items, key=lambda x: x[1] * x[2], reverse=True)[:3]  # 强项按贡献
            weak = sorted(items, key=lambda x: x[1])[:2]                          # 短板按原始分
            plan = confirm_plan(r)
            print(f"\n【{i}】{r['code']} {r['name']}  稳健分 {r['score']:.1f} "
                  f"(加权 {r['score_raw']:.1f} / 短板 {r['score_worst']:.1f})")
            print(f"    高开 {r['gap']}% | 量比 {r['vol_ratio']} | 竞价额 {r['amount_wan']:.0f}万 "
                  f"| 换手 {r['turnover']}% | 市值 {r['float_cap']}亿 | 委比 {r['weibi']}")
            print(f"    ✅ 强项: " + "、".join(f"{nm}({s:.0f})" for nm, s, _ in strong))
            print(f"    ⚠️ 短板: " + "、".join(f"{nm}({s:.0f})" for nm, s, _ in weak))
            print(f"    📌 信号: {plan['signals']}")
            print(f"    ✔ 买入确认: {plan['buy_cond']}")
            print(f"    ✘ 放弃条件: {plan['drop_cond']}")
    print("\n" + "=" * 74 + "\n")


# =====================================================================
# 输出(CSV 兼容 daily_push)
# =====================================================================
def save_csv(scored, sentiment, buy, watch, path):
    if not scored:
        return
    cols = ["code", "name", "score", "score_raw", "score_worst", "score_robust",
            "gap", "vol_ratio", "amount_wan", "turnover", "float_cap", "weibi",
            "speed", "amt_ratio", "amt_pct", "price", "source",
            "sentiment_state", "sentiment_temp", "zone"] + ["s_" + k for k in DIM_NAMES]
    df = pd.DataFrame(scored)
    for k in ["sentiment_state", "sentiment_temp", "zone"]:
        df[k] = pd.Series([None] * len(df), dtype=object)
    if not df.empty:
        df.loc[0, "sentiment_state"] = sentiment["state"]
        df.loc[0, "sentiment_temp"] = str(sentiment["temp"])
        # 按实际入选code标记分区(而非位置),避免达门槛不足时误标
        buy_codes = {r["code"] for r in buy}
        watch_codes = {r["code"] for r in watch}
        df.loc[df["code"].isin(buy_codes), "zone"] = "首选"
        df.loc[df["code"].isin(watch_codes), "zone"] = "观察"
    df.to_csv(path, index=False, encoding="utf-8-sig", columns=cols)


def save_chart(buy, watch, sentiment, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        for fp in ["/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                   "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]:
            if os.path.exists(fp):
                font_manager.fontManager.addfont(fp)
                plt.rcParams["font.sans-serif"] = [font_manager.FontProperties(fname=fp).get_name()]
                break
        plt.rcParams["axes.unicode_minus"] = False
        top = buy[:3] if buy else watch[:3]
        if not top:
            return False
        names = [f"{r['code']}\n{str(r['name'])[:4]}" for r in top]
        keys = list(DIM_NAMES.keys())
        w = WeightsV3()
        wsum = sum(asdict(w).values())
        fig, ax = plt.subplots(figsize=(10, 5.5))
        bottom = [0] * len(top)
        colors = ["#e74c3c", "#e67e22", "#f1c40f", "#2ecc71", "#1abc9c",
                  "#3498db", "#9b59b6", "#34495e"]
        for k, color in zip(keys, colors):
            vals = [r["s_" + k] / 100 * asdict(w)[k] / wsum * 100 for r in top]
            ax.bar(names, vals, bottom=bottom, label=DIM_NAMES[k], color=color, width=0.6)
            bottom = [b + v for b in v] if False else [b + v for b, v in zip(bottom, vals)]
        for i, r in enumerate(top):
            ax.text(i, bottom[i] + 1, f"{r['score']:.1f}", ha="center", fontsize=10, fontweight="bold")
        ax.set_ylabel("加权得分贡献")
        ax.set_title(f"竞价选股 v3 Top3 稳健评分  [{sentiment['state']}·温度{sentiment['temp']:.0f}]  {now_cn():%m-%d %H:%M}")
        ax.legend(loc="upper right", fontsize=8, ncol=4)
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
def _load_csv(path):
    with open(path, encoding="utf-8-sig") as fh:
        rows = [dict(x) for x in csv.DictReader(fh)]
    for r in rows:
        for k in ["gap", "vol_ratio", "amount_wan", "turnover", "float_cap",
                  "weibi", "speed", "price", "open", "preclose", "high", "low"]:
            if k in r:
                try:
                    r[k] = float(r[k])
                except (ValueError, TypeError):
                    r[k] = 0.0
        r.setdefault("ask1_vol", 1.0)
    return rows


def main():
    ap = argparse.ArgumentParser(description="A股竞价选股 v3(情绪开关+稳健评分+Top3)")
    ap.add_argument("--source", choices=["eastmoney", "tencent", "demo"], default="eastmoney")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--csv", help="从已有结果CSV加载真实数据(避免重复抓取)")
    ap.add_argument("--no-chart", action="store_true")
    ap.add_argument("--min-price", type=float, default=15.0)
    ap.add_argument("--max-gap", type=float, default=7.0)
    ap.add_argument("--min-vr", type=float, default=3.0)
    args = ap.parse_args()

    c = ConstraintsV3(min_price=args.min_price, max_gap=args.max_gap,
                      min_vol_ratio=args.min_vr)
    w = WeightsV3()
    source = "demo" if args.demo else args.source
    session = base.make_session()

    print(f"\n[*] 竞价选股 v3 启动 | 数据源={source} | {now_cn():%Y-%m-%d %H:%M:%S}")
    print("[*] 硬过滤: 非科创板 股价>=%.0f元 高开[%.1f,%.1f]%% 量比>=%.1f 竞价额>=%.0f万 市值[20,300]亿"
          % (c.min_price, c.min_gap, c.max_gap, c.min_vol_ratio, c.min_amount_wan))

    rows = []
    if args.csv:
        rows = _load_csv(args.csv)
        print(f"[*] 从 CSV 加载 {len(rows)} 只: {args.csv}")
    elif source == "eastmoney":
        try:
            rows = base.fetch_eastmoney(session)
            if not rows:
                raise RuntimeError("东财空数据")
            print(f"[*] 东财全市场快照 {len(rows)} 只")
        except Exception as e:
            print(f"[!] 东财失败({str(e)[:60]}),降级腾讯")
            source = "tencent"
    if source == "tencent" and not rows:
        try:
            rows = base.fetch_tencent(session, base.DEMO_POOL)
            if not rows:
                raise RuntimeError("腾讯空数据")
            print(f"[*] 腾讯快照 {len(rows)} 只")
        except Exception:
            source = "demo"
    if source == "demo" and not rows:
        rows = base.gen_demo()
        print(f"[*] DEMO 数据 {len(rows)} 只")

    # ① 情绪开关(用全市场原始快照算,不受硬过滤影响)
    sentiment = compute_market_sentiment(rows)
    # 硬过滤
    passed, rejected = apply_constraints_v3(rows, c)
    print(f"[*] 硬过滤: 通过 {len(passed)} / 淘汰 {len(rejected)}")
    # ②③ 评分
    scored = score_v3_batch(passed, w, sentiment)
    # ④ 双区
    buy, watch = select_zones(scored, sentiment)
    print_report(scored, buy, watch, sentiment, c)

    stamp = now_cn().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(base.OUTPUT_DIR, f"竞价Top3结果_{stamp}.csv")
    save_csv(scored, sentiment, buy, watch, out)
    print(f"[+] 结果 CSV: {out}")
    if not args.no_chart and (buy or watch):
        chart = os.path.join(base.OUTPUT_DIR, f"竞价Top3评分图_{stamp}.png")
        if save_chart(buy, watch, sentiment, chart):
            print(f"[+] 评分图: {chart}")
    return {"buy": buy, "watch": watch, "sentiment": sentiment, "csv": out}


if __name__ == "__main__":
    main()
