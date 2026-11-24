#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
A 股竞价选股 v2 —— 融合 2025-2026 市场最新竞价战法优化
=====================================================================
最新方法论要点(经联网调研多源交叉验证):
  1. 高开 2%-6% 最佳,超过 8% 获利兑现风险大(涨幅与资金须匹配)
  2. 量比 ≥3 才算有效放量,<3 多是主力自导自演;≥5 为强势
  3. 换手率须按流通盘分档达标:小盘(<50亿)0.5%-1%,中大盘 0.3%-0.5%
     —— "无换手不走强",换手不足的高开是虚强势
  4. 竞价资金匹配度:竞价额相对流通市值/近期成交的强度,
     过低=无人抢筹,过高(>25%)=对倒放量出货,需防诱多
  5. 9:20-9:25 不可撤单阶段的承接力(用涨速/委比代理)
  6. 排除一字板、ST、涨幅越界标的

架构:v1 数据层复用 + v2 约束/评分层 + Top3 深度诊断
=====================================================================
"""
import argparse
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime

import pandas as pd

import auction_stock_picker as base


@dataclass
class ConstraintsV2:
    """v2 硬约束 —— 依据最新战法收紧"""
    min_gap: float = 2.0         # 高开下限
    max_gap: float = 6.5         # 高开上限(最新:>8%获利兑现,收紧到6.5%)
    min_vol_ratio: float = 3.0   # 量比门槛(最新:<3 多为诱多)
    min_amount_wan: float = 2000.0  # 竞价额门槛(提高过滤虚假)
    min_float_cap: float = 20.0
    max_float_cap: float = 300.0    # 聚焦中小盘(游资偏好30-100亿)
    min_turnover: float = 0.2       # 换手下限(无换手不走强)
    exclude_st: bool = True
    exclude_one_word: bool = True


@dataclass
class WeightsV2:
    """v2 九维评分权重(总 100)—— 加大量比与资金匹配"""
    gap: float = 18          # 1. 高开质量(2-6%,最佳4-5%)
    vol_ratio: float = 18    # 2. 量比强度(≥3合格,≥8满分)
    amount_match: float = 12 # 3. 资金匹配度(竞价额/流通市值,防诱多)
    turnover_fit: float = 12 # 4. 换手达标度(按市值分档)
    bid_power: float = 12    # 5. 买盘力量(委比/主力净占比)
    float_cap: float = 8     # 6. 市值适中(30-100亿最佳)
    continuation: float = 8  # 7. 承接力(涨速代理 9:20-9:25 不回撤)
    activeness: float = 6    # 8. 活跃度(振幅)
    momentum: float = 6      # 9. 动量


DIM_NAMES = {
    "gap": "高开质量", "vol_ratio": "量比强度", "amount_match": "资金匹配",
    "turnover_fit": "换手达标", "bid_power": "买盘力量", "float_cap": "市值适中",
    "continuation": "承接力", "activeness": "活跃度", "momentum": "动量",
}


def apply_constraints_v2(rows, c: ConstraintsV2):
    """v2 约束过滤,返回 (通过, 淘汰)"""
    passed, rejected = [], []
    for r in rows:
        reasons = []
        if c.exclude_st and "ST" in r["name"].upper():
            reasons.append("ST股")
        if c.exclude_one_word and r["gap"] >= 9.8 and r["ask1_vol"] == 0:
            reasons.append("一字板")
        if not (c.min_gap <= r["gap"] <= c.max_gap):
            reasons.append(f"高开{r['gap']}%越界")
        if r["vol_ratio"] < c.min_vol_ratio:
            reasons.append(f"量比{r['vol_ratio']}不足")
        if r["amount_wan"] < c.min_amount_wan:
            reasons.append(f"竞价额{r['amount_wan']:.0f}万不足")
        if r["turnover"] < c.min_turnover:
            reasons.append(f"换手{r['turnover']}%不足")
        if not (c.min_float_cap <= r["float_cap"] <= c.max_float_cap):
            reasons.append(f"市值{r['float_cap']}亿越界")
        if reasons:
            r2 = dict(r)
            r2["reject_reason"] = "、".join(reasons)
            rejected.append(r2)
        else:
            passed.append(dict(r))
    return passed, rejected


def _clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def score_v2_batch(rows, w: WeightsV2):
    """v2 批量评分。资金匹配度用池内相对排名(时段无关)。"""
    if not rows:
        return []
    df = pd.DataFrame(rows)
    # 资金匹配度:竞价额/流通市值,池内百分位(避免绝对阈值在盘中/竞价失真)
    df["amt_ratio"] = df["amount_wan"] / df["float_cap"].replace(0, 1)
    df["amt_pct"] = df["amt_ratio"].rank(pct=True) * 100

    scored = []
    for r in df.to_dict("records"):
        # 1 高开质量:最佳4.5%,2-6%区间
        s_gap = _clip(100 - abs(r["gap"] - 4.5) * 18)
        # 2 量比强度:3合格,8满分
        s_vr = _clip((r["vol_ratio"] - 1) / 7 * 100)
        # 3 资金匹配:相对强度百分位,两端惩罚(过高疑对倒)
        p = r["amt_pct"]
        s_amt = _clip(p if p <= 85 else 100 - (p - 85) * 4)
        # 4 换手达标:按市值分档。最新战法:"无换手不走强",换手越高说明资金越真实;
        #    只有 >25% 才视为异常高换手(可能出货)轻微扣分,达标区间内不惩罚高换手
        cap = r["float_cap"]
        lo = 0.5 if cap < 50 else 0.3
        t = r["turnover"]
        if t < lo:
            s_to = _clip(100 - (lo - t) * 120)   # 换手不足重罚
        elif t > 25:
            s_to = _clip(100 - (t - 25) * 3)      # 异常高换手轻罚
        else:
            s_to = 100                            # 达标即满分,不惩罚适度高换手
        # 5 买盘力量:委比/主力净占比
        s_bid = _clip((r["weibi"] + 20) / 70 * 100)
        # 6 市值适中:30-100亿最佳
        s_cap = 100 if 30 <= cap <= 100 else _clip(100 - min(abs(cap - 30), abs(cap - 100)) * 1.2)
        # 7 承接力:涨速代理(高开后不回撤)
        s_cont = _clip((r["speed"] + 1) / 2.5 * 100)
        # 8 活跃度:振幅3-8%(优先 high/low,缺失则用 现价/开盘价 近似)
        if r.get("high", 0) > 0 and r.get("low", 0) > 0:
            hi, lo = r["high"], r["low"]
        else:
            hi, lo = max(r.get("price", 0), r.get("open", 0)), min(r.get("price", 0), r.get("open", 0))
        amp = (hi - lo) / r["preclose"] * 100 if r["preclose"] > 0 else 0
        s_act = _clip(100 - abs(amp - 5) * 12)
        # 9 动量:涨速与委比的综合(开盘后主动买盘延续)
        s_mom = _clip((r["speed"] + 1.5) / 3 * 60 + (r["weibi"] + 20) / 80 * 40)

        parts = {
            "gap": s_gap, "vol_ratio": s_vr, "amount_match": s_amt,
            "turnover_fit": s_to, "bid_power": s_bid, "float_cap": s_cap,
            "continuation": s_cont, "activeness": s_act, "momentum": s_mom,
        }
        wd = asdict(w)
        total = sum(parts[k] * wd[k] for k in parts) / max(sum(wd.values()), 1)
        out = dict(r)
        out["score"] = round(total, 2)
        out["amt_ratio"] = round(r["amt_ratio"], 1)
        for k, v in parts.items():
            out["s_" + k] = round(v, 1)
        scored.append(out)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def print_top(scored, top):
    if not scored:
        print("\n[!] 无股票通过 v2 约束(最新战法较严格),可放宽 --min-vr/--max-gap\n")
        return
    hdr = (f"{'排名':<4}{'代码':<8}{'名称':<11}{'总分':>7}{'高开%':>7}{'量比':>6}"
           f"{'竞价额万':>10}{'换手%':>7}{'市值亿':>8}{'委比%':>7}{'资匹':>6}")
    print("\n" + "=" * len(hdr))
    print(f"  v2 竞价选股(最新战法优化) Top{min(top, len(scored))}")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for i, r in enumerate(scored[:top], 1):
        print(f"{i:<4}{r['code']:<8}{r['name'][:10]:<11}{r['score']:>7.1f}"
              f"{r['gap']:>7.2f}{r['vol_ratio']:>6.2f}{r['amount_wan']:>10.0f}"
              f"{r['turnover']:>7.2f}{r['float_cap']:>8.1f}{r['weibi']:>7.1f}"
              f"{r['amt_ratio']:>6.1f}")
    print("=" * len(hdr) + "\n")


def diagnose_top3(scored):
    """Top3 深度诊断:各维度表现 + 入选逻辑 + 风险点"""
    if not scored:
        return
    print("=" * 72)
    print("  Top3 深度诊断(基于最新竞价战法)")
    print("=" * 72)
    for i, r in enumerate(scored[:3], 1):
        wd = asdict(WeightsV2())
        parts = sorted(
            [(DIM_NAMES[k], r["s_" + k], wd[k]) for k in DIM_NAMES],
            key=lambda x: x[1] * x[2], reverse=True,
        )
        strong = parts[:3]
        weak = parts[-2:]
        print(f"\n【{i}】{r['code']} {r['name']}  总分 {r['score']}")
        print(f"    高开 {r['gap']}% | 量比 {r['vol_ratio']} | 竞价额 {r['amount_wan']:.0f}万 | "
              f"换手 {r['turnover']}% | 市值 {r['float_cap']}亿 | 委比 {r['weibi']}")
        print(f"    ✅ 优势维度:" + "、".join(f"{n}({s:.0f})" for n, s, _ in strong))
        print(f"    ⚠️ 相对短板:" + "、".join(f"{n}({s:.0f})" for n, s, _ in weak))
        # 入选逻辑
        logic = []
        if r["gap"] <= 5.5:
            logic.append("高开幅度健康未透支")
        if r["vol_ratio"] >= 5:
            logic.append("量比强势放量")
        if r["turnover"] >= (0.5 if r["float_cap"] < 50 else 0.3):
            logic.append("换手达标有真实资金")
        if r["weibi"] > 20:
            logic.append("买盘力量占优")
        if r["amt_ratio"] > 30:
            logic.append("竞价资金匹配度高")
        print(f"    📌 入选逻辑:" + ("、".join(logic[:4]) if logic else "综合评分居前"))
        if not logic:
            pass
    print("\n" + "=" * 72)


def save_csv(scored, path):
    if not scored:
        return
    cols = ["code", "name", "score", "gap", "vol_ratio", "amount_wan", "turnover",
            "float_cap", "weibi", "speed", "amt_ratio", "price", "source"] + \
           ["s_" + k for k in DIM_NAMES]
    pd.DataFrame(scored).to_csv(path, index=False, encoding="utf-8-sig", columns=cols)


def main():
    ap = argparse.ArgumentParser(description="A股竞价选股 v2(最新战法优化)")
    ap.add_argument("--source", choices=["eastmoney", "tencent", "demo"], default="eastmoney")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--max-gap", type=float, default=6.5)
    ap.add_argument("--min-vr", type=float, default=3.0)
    ap.add_argument("--min-amount", type=float, default=2000.0)
    ap.add_argument("--csv", help="从已有结果CSV加载真实数据(避免重复抓取)")
    args = ap.parse_args()

    c = ConstraintsV2(max_gap=args.max_gap, min_vol_ratio=args.min_vr,
                      min_amount_wan=args.min_amount)
    w = WeightsV2()
    source = "demo" if args.demo else args.source
    session = base.make_session()

    print(f"\n[*] 竞价选股 v2 启动 | 数据源={source} | {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("[*] v2 约束: 高开[2,%.1f]%% 量比>=%.1f 竞价额>=%.0f万 换手>=0.2%% 市值[20,300]亿"
          % (c.max_gap, c.min_vol_ratio, c.min_amount_wan))

    rows = []
    if args.csv:
        import csv as _csv
        with open(args.csv, encoding="utf-8-sig") as fh:
            rows = [dict(x) for x in _csv.DictReader(fh)]
        # CSV 存储的是字符串数值,统一转 float
        for r in rows:
            for k in ["gap", "vol_ratio", "amount_wan", "turnover", "float_cap",
                      "weibi", "speed", "price", "open", "preclose", "high", "low"]:
                if k in r:
                    try:
                        r[k] = float(r[k])
                    except (ValueError, TypeError):
                        r[k] = 0.0
            r.setdefault("ask1_vol", 1.0)
        print(f"[*] 从 CSV 加载 {len(rows)} 只真实数据: {args.csv}")
    elif source == "eastmoney":
        try:
            rows = base.fetch_eastmoney(session)
            if not rows:
                raise RuntimeError("东财空数据")
            print(f"[*] 东财快照 {len(rows)} 只")
        except Exception as e:
            print(f"[!] 东财失败({str(e)[:60]}),降级腾讯")
            source = "tencent"
    if source == "tencent" and not rows:
        try:
            codes = base.DEMO_POOL
            rows = base.fetch_tencent(session, codes)
            if not rows:
                raise RuntimeError("腾讯空数据")
            print(f"[*] 腾讯快照 {len(rows)} 只")
        except Exception:
            source = "demo"
    if source == "demo" and not rows:
        rows = base.gen_demo()
        print(f"[*] DEMO 数据 {len(rows)} 只")

    passed, rejected = apply_constraints_v2(rows, c)
    print(f"[*] v2 约束过滤: 通过 {len(passed)} / 淘汰 {len(rejected)}")

    scored = score_v2_batch(passed, w)
    print_top(scored, args.top)
    diagnose_top3(scored)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(base.OUTPUT_DIR, f"竞价选股v2结果_{stamp}.csv")
    save_csv(scored, out)
    if scored:
        print(f"[+] 结果 CSV: {out}")
    print()
    return scored


if __name__ == "__main__":
    main()
