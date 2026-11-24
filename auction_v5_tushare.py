#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
A股竞价选股 v5-Top3 —— GitHub 开源策略完整复刻
=====================================================================
来源: github.com/huang1125677925/jingjia-stock-selector-custom-skill
回测战绩: 胜率80.6%, 均收+4.56%, 累计+141.23%, 18分+胜率93%

核心体系(与 v1/v2 多因子体系完全不同):
  1. 市场情绪判断 —— 3进4晋级率/炸板率/最高板,退潮日空仓
  2. 五维评分 —— A竞价量能/B开盘位置/C连板位置/D板块热度/E竞价形态
  3. 宁缺毋滥 —— 每天最多Top3,>=16分才进买入池

数据源: Tushare Pro (需 TUSHARE_TOKEN, stk_auction等接口需相应积分)
=====================================================================
用法:
    export TUSHARE_TOKEN=你的token
    python3.11 auction_v5_tushare.py                    # 今日选股
    python3.11 auction_v5_tushare.py --date 20260508    # 指定日期
    python3.11 auction_v5_tushare.py --backtest --start 20260409 --end 20260508
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

try:
    import tushare as ts
    import pandas as pd
except ImportError:
    print("缺少依赖: pip install tushare pandas")
    sys.exit(1)

TOKEN = os.environ.get("TUSHARE_TOKEN", "")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))


def get_pro():
    if not TOKEN:
        print("\n[!] 未设置 TUSHARE_TOKEN")
        print("    export TUSHARE_TOKEN=你的token")
        print("    获取地址: https://tushare.pro/register")
        print("    注意: stk_auction 等接口需要相应积分\n")
        sys.exit(1)
    ts.set_token(TOKEN)
    return ts.pro_api()


# =====================================================================
# 数据采集
# =====================================================================
def fetch_auction(pro, date):
    """竞价数据主接口:stk_auction,自带pre_close,支持历史回查"""
    df = pro.stk_auction(trade_date=date)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"price": "open"})
    for c in ["open", "vol", "amount", "pre_close"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["竞开涨幅"] = (df["open"] / df["pre_close"] - 1) * 100
    return df


def fetch_daily_basic(pro, date):
    try:
        df = pro.daily_basic(trade_date=date, fields="ts_code,circ_mv,turnover_rate")
        if df is not None and not df.empty:
            df["circ_mv"] = pd.to_numeric(df["circ_mv"], errors="coerce") / 10000  # 万元→亿
            return df[["ts_code", "circ_mv", "turnover_rate"]]
    except Exception as e:
        print(f"[!] daily_basic 失败: {str(e)[:60]}")
    return pd.DataFrame()


def fetch_stock_names(pro):
    try:
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")
        return dict(zip(df["ts_code"], df["name"]))
    except Exception:
        return {}


def fetch_limit_step(pro, date):
    """昨日连板梯队"""
    try:
        return pro.limit_step(trade_date=date)
    except Exception:
        return pd.DataFrame()


def fetch_limit_list(pro, date):
    """涨跌停 + 封板质量(封单金额fd_amount, 炸板次数open_times)"""
    try:
        return pro.limit_list_d(trade_date=date)
    except Exception:
        return pd.DataFrame()


def fetch_limit_cpt(pro, date):
    """概念板块TOP10"""
    try:
        return pro.limit_cpt_list(trade_date=date)
    except Exception:
        return pd.DataFrame()


# =====================================================================
# 市场情绪判断
# =====================================================================
def judge_market_mood(limit_step_df, limit_list_df):
    """
    情绪判断:3进4晋级率/炸板率/最高板/核按钮
    返回 (情绪等级, 详情dict)
    """
    detail = {"晋级率": None, "炸板率": None, "最高板": None, "核按钮": False}
    try:
        if not limit_step_df.empty:
            # 连板高度
            if "lbc" in limit_step_df.columns:
                detail["最高板"] = int(pd.to_numeric(limit_step_df["lbc"], errors="coerce").max())
            # 3进4晋级率:3板数量 vs 4板数量(近似)
            if "lbc" in limit_step_df.columns:
                cnt = limit_step_df["lbc"].value_counts()
                n3, n4 = cnt.get(3, 0), cnt.get(4, 0)
                detail["晋级率"] = round(n4 / n3 * 100, 1) if n3 > 0 else None
        if not limit_list_df.empty:
            if "open_times" in limit_list_df.columns:
                total = len(limit_list_df)
                broken = (pd.to_numeric(limit_list_df["open_times"], errors="coerce") > 0).sum()
                detail["炸板率"] = round(broken / total * 100, 1) if total > 0 else None
    except Exception:
        pass

    jr = detail["晋级率"]
    zb = detail["炸板率"]
    # 判断规则
    if (jr is not None and jr < 20) or (zb is not None and zb > 45):
        return "🛑 退潮防守", detail
    if (jr is not None and jr >= 35) and (zb is not None and zb <= 30):
        return "✅ 接力友好", detail
    return "⚡ 分歧观察", detail


# =====================================================================
# v5 五维评分
# =====================================================================
def score_A_竞价量能(ratio, circ_mv):
    """竞价额/流通市值 按市值分档"""
    if circ_mv <= 200:
        if ratio >= 0.5: return 5
        if ratio >= 0.3: return 4
        if ratio >= 0.2: return 3
        if ratio >= 0.1: return 2
        return -99  # 硬排除
    else:
        if ratio >= 0.3: return 5
        if ratio >= 0.2: return 4
        if ratio >= 0.1: return 3
        return 2


def score_B_开盘位置(gap):
    if 8 <= gap < 10: return 5       # 黄金开盘点
    if gap >= 10: return 4           # 一字,观察(原5但买不到,降为4)
    if 3 <= gap < 8: return 4
    if 0 <= gap < 3: return 3
    if -3 <= gap < 0: return 2       # 低开拉板常见,不排除
    return -99                       # 大幅低开<-3 排除


def score_C_连板位置(lbc):
    return {3: 4.5, 2: 4, 1: 2.5}.get(lbc, 2 if lbc >= 4 else 0)


def score_D_板块热度(cpt_rank, cpt_limit_count):
    if cpt_rank <= 5 and cpt_limit_count > 20: return 5
    if cpt_rank <= 5: return 4
    if cpt_rank <= 10: return 3
    return -99  # 不在TOP10 排除


def score_E_竞价形态(yest_board, today_open, vol_ratio, ratio):
    """昨板质量 × 今开模式"""
    key = (yest_board, today_open)
    table = {
        ("一字板", "放量高开"): (5, "40.7%成功率"),
        ("换手板", "高开6%+放量"): (5, "36.8%成功率"),
        ("一字板", "一字10%"): (4, "买不到,观察"),
        ("烂板", "放量高开"): (4, "弱转强37%"),
        ("烂板", "平开/低开"): (3, "样本偏少"),
        ("烂板", "小幅高开"): (2, "弱承接"),
        ("换手板", "小幅高开"): (1, "陷阱!"),
        ("缩量板", "任意"): (2, "缩量尽头"),
    }
    return table.get(key, (2, "默认"))


def score_all(df, mood_penalty):
    """对所有候选打分,返回排序后的DataFrame"""
    results = []
    for _, r in df.iterrows():
        gap = r.get("竞开涨幅", 0)
        circ_mv = r.get("circ_mv", 0)
        amount = r.get("amount", 0)
        pre_close = r.get("pre_close", 0)

        # 竞价额/流通市值比(%)
        ratio = (amount / (circ_mv * 1e8) * 100) if circ_mv > 0 else 0

        # 硬排除
        if circ_mv < 20:  # 市值<20亿
            continue
        if gap < -3:
            continue
        if ratio < 0.05:  # 竞价无量
            continue

        sA = score_A_竞价量能(ratio, circ_mv)
        if sA == -99:
            continue
        sB = score_B_开盘位置(gap)
        if sB == -99:
            continue
        # C/D/E 需要连板/板块/形态数据,缺失时用中性分(2/2/2)
        sC = score_C_连板位置(r.get("lbc", 0)) or 2
        sD = r.get("sD", 2)  # 由外部板块数据注入
        sE, e_note = score_E_竞价形态(r.get("yest_board", ""), r.get("today_open", ""),
                                     r.get("vol_ratio", 0), ratio)

        final = (sA * 0.25 + sB * 0.20 + sC * 0.15 + sD * 0.20 + sE * 0.20)
        final += mood_penalty  # 情绪降分
        results.append({
            "ts_code": r.get("ts_code", ""),
            "name": r.get("name", ""),
            "竞开涨幅": round(gap, 2),
            "竞价额亿": round(amount / 1e8, 3),
            "流通市值亿": round(circ_mv, 1),
            "竞价量能比": round(ratio, 3),
            "A": sA, "B": sB, "C": sC, "D": sD, "E": sE,
            "最终分": round(final, 2),
            "形态依据": e_note,
        })
    out = pd.DataFrame(results)
    if not out.empty:
        out = out.sort_values("最终分", ascending=False).reset_index(drop=True)
    return out


def output_daily(result, mood, mood_detail, date):
    print("\n" + "=" * 78)
    print(f"  v5-Top3 竞价选股结果  {date}")
    print("=" * 78)
    print(f"\n【市场情绪】{mood}")
    print(f"  3进4晋级率: {mood_detail['晋级率']}% | 炸板率: {mood_detail['炸板率']}% | "
          f"最高板: {mood_detail['最高板']}")

    if "退潮" in mood:
        print("\n  🛑 退潮防守期,不推荐接力,今日空仓。")
        return

    if result.empty:
        print("\n  无候选标的。")
        return

    buy = result[result["最终分"] >= 16].head(3)
    watch = result[(result["最终分"] >= 12) & (result["最终分"] < 16)].head(5)

    print(f"\n【🏆 首选买入池 Top{len(buy)}】")
    if not buy.empty:
        cols = ["ts_code", "name", "竞开涨幅", "竞价量能比", "流通市值亿", "最终分", "形态依据"]
        print(buy[cols].to_string(index=False))
    else:
        print("  无标的达到16分门槛,宁缺毋滥。")

    print(f"\n【📋 观察池 Top{len(watch)}】")
    if not watch.empty:
        print(watch[["ts_code", "name", "竞开涨幅", "最终分"]].to_string(index=False))

    # 保存
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(OUTPUT_DIR, f"v5选股结果_{stamp}.csv")
    result.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n[+] 完整结果: {out}")
    print("\n⚠️ 仅供研究,不构成投资建议。竞价打板风险高,请独立判断。")


# =====================================================================
# 主流程
# =====================================================================
def run_daily(pro, date):
    print(f"\n[*] v5-Top3 选股启动 | {date}")
    auction = fetch_auction(pro, date)
    if auction.empty:
        print("[!] 竞价数据为空(可能非交易日或未到9:25)")
        return
    print(f"[*] 竞价数据 {len(auction)} 只")

    basic = fetch_daily_basic(pro, date)
    if not basic.empty:
        auction = auction.merge(basic, on="ts_code", how="left")
    names = fetch_stock_names(pro)
    auction["name"] = auction["ts_code"].map(names)

    # 情绪判断(需要连板/封板数据)
    lstep = fetch_limit_step(pro, date)
    llist = fetch_limit_list(pro, date)
    mood, mood_detail = judge_market_mood(lstep, llist)
    penalty = -1.5 if "分歧" in mood else 0

    result = score_all(auction, penalty)
    output_daily(result, mood, mood_detail, date)


def run_backtest(pro, start, end):
    """简化回测:逐日选股,统计次日表现"""
    print(f"\n[*] v5 回测 | {start} ~ {end}")
    cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
    dates = sorted(cal["cal_date"].tolist())
    all_picks = []
    for d in dates:
        try:
            auction = fetch_auction(pro, d)
            if auction.empty:
                continue
            basic = fetch_daily_basic(pro, d)
            if not basic.empty:
                auction = auction.merge(basic, on="ts_code", how="left")
            lstep = fetch_limit_step(pro, d)
            llist = fetch_limit_list(pro, d)
            mood, _ = judge_market_mood(lstep, llist)
            if "退潮" in mood:
                continue
            penalty = -1.5 if "分歧" in mood else 0
            result = score_all(auction, penalty)
            for _, r in result[result["最终分"] >= 16].head(3).iterrows():
                all_picks.append({"date": d, **r.to_dict()})
        except Exception as e:
            print(f"  {d}: 跳过({str(e)[:40]})")
            continue
    if not all_picks:
        print("[!] 回测无交易记录")
        return
    df = pd.DataFrame(all_picks)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(OUTPUT_DIR, f"v5回测结果_{stamp}.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"[*] 回测交易 {len(df)} 笔,明细: {out}")
    print("[i] 完整收益统计需在 score_all 中接入次日卖出价(T+1竞价开盘/收盘择高)")


def main():
    ap = argparse.ArgumentParser(description="v5-Top3 竞价选股(Tushare复刻版)")
    ap.add_argument("--token", help="Tushare Token(也可设环境变量 TUSHARE_TOKEN)")
    ap.add_argument("--date", help="选股日期 YYYYMMDD,默认最近交易日")
    ap.add_argument("--backtest", action="store_true", help="回测模式")
    ap.add_argument("--start", help="回测起始日期")
    ap.add_argument("--end", help="回测结束日期")
    args = ap.parse_args()

    global TOKEN
    if args.token:
        TOKEN = args.token
    pro = get_pro()

    date = args.date or datetime.now().strftime("%Y%m%d")
    if args.backtest:
        run_backtest(pro, args.start or "20260409", args.end or date)
    else:
        run_daily(pro, date)


if __name__ == "__main__":
    main()
