# -*- coding: utf-8 -*-
"""云端(Actions)专用:读 results/ 当天 CSV → 格式化 Markdown → 微信推送。

凭证来自环境变量 SERVERCHAN_SENDKEY(由工作流从 secrets 注入)。
用法: python push_from_results.py shape | auction | auction_v3
"""
import os
import sys
import glob
import datetime as dt
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")

def now_cn():
    """北京时间(GitHub Actions 服务器是 UTC,必须显式转换)"""
    return dt.datetime.now(CN_TZ)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wechat_push import push, format_table, push_retry, already_pushed, mark_pushed

RESULTS = os.environ.get("OUTPUT_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "results"))


def latest_csv(pattern, within_sec=1800):
    """取最新的CSV;只要最近 within_sec 秒内生成的,避免读到陈旧归档。"""
    files = glob.glob(os.path.join(RESULTS, pattern))
    if not files:
        return None
    latest = max(files, key=os.path.getmtime)
    if dt.datetime.now().timestamp() - os.path.getmtime(latest) > within_sec:
        return None
    return latest


def _fmt(v):
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def fmt_shape(path):
    today = now_cn().strftime("%Y-%m-%d")
    lines = [f"## 📊 A股形态选股 · {today}"]
    if not path:
        lines.append("\n今日未生成结果CSV(可能无候选或数据源受限)。")
        lines.append("\n---\n⚠️ 形态选股存在假突破风险,不构成投资建议。")
        return "\n".join(lines)
    import pandas as pd
    df = pd.read_csv(path, encoding="utf-8-sig")
    if df.empty:
        lines.append("\n今日无候选股票(约束过滤所致)。")
    else:
        cols_map = [("code", "代码"), ("name", "名称"), ("score", "综合分"),
                    ("chg", "涨跌幅%"), ("vol_ratio", "量比"), ("shapes", "命中形态")]
        cols = [c for c, _ in cols_map if c in df.columns]
        headers = [dict(cols_map)[c] for c in cols]
        rows = [[_fmt(r[c]) for c in cols] for _, r in df.head(12).iterrows()]
        lines.append(f"\n共 {len(df)} 只,展示 Top{min(12, len(df))}:\n")
        lines.append(format_table(headers, rows))
    lines.append("\n---\n⚠️ 形态选股存在假突破风险,涨幅>9%追高需等回踩确认。不构成投资建议。")
    return "\n".join(lines)


def fmt_auction_v3(path):
    """v3 双区推送:情绪开关 + 🏆首选Top3 + 📋观察区 + 开盘确认清单"""
    today = now_cn().strftime("%Y-%m-%d")
    lines = [f"## 📊 A股竞价选股 v3 · {today}"]
    if not path:
        lines.append("\n今日无竞价结果(可能非交易时段或数据源不可用)。")
        lines.append("\n---\n⚠️ 量化辅助筛选,不构成投资建议。")
        return "\n".join(lines)
    import pandas as pd
    df = pd.read_csv(path, encoding="utf-8-sig")
    if df.empty:
        lines.append("\n今日无候选股票(硬约束过滤所致,宁缺毋滥)。")
        lines.append("\n---\n⚠️ 量化辅助筛选,不构成投资建议。")
        return "\n".join(lines)

    # 情绪状态
    if "sentiment_state" in df.columns:
        st = str(df.iloc[0]["sentiment_state"])
        tp = str(df.iloc[0].get("sentiment_temp", ""))
        icons = {"主升": "🔥", "健康": "✅", "分歧": "⚡", "退潮": "🛑"}
        lines.append(f"\n**市场情绪: {icons.get(st, '')} {st}(温度 {tp})**")
        if st == "退潮":
            lines.append("\n> 🛑 退潮防守:今日首选买入区关闭,以下仅作观察,不建议实盘出手。")
        elif st == "分歧":
            lines.append("\n> ⚡ 多空分歧:减量降仓,精选少数标的。")

    if "zone" in df.columns and "score_robust" in df.columns:
        buy = df[df["zone"] == "首选"]
        watch = df[df["zone"] == "观察"]
        lines.append(f"\n### 🏆 首选买入区 Top{len(buy)}")
        if buy.empty:
            lines.append("\n(空)无票达到稳健分门槛或情绪退潮,宁缺毋滥。")
        else:
            cols_map = [("code", "代码"), ("name", "名称"), ("score_robust", "稳健分"),
                        ("gap", "高开%"), ("vol_ratio", "量比"), ("amount_wan", "竞价额万"),
                        ("weibi", "委比%")]
            cols = [c for c, _ in cols_map if c in buy.columns]
            headers = [dict(cols_map)[c] for c in cols]
            rows = [[_fmt(r[c]) for c in cols] for _, r in buy.head(3).iterrows()]
            lines.append("\n" + format_table(headers, rows))
            lines.append("\n**开盘确认(9:30-9:35):**")
            for _, r in buy.head(3).iterrows():
                nm = str(r.get("name", ""))[:8]
                try:
                    openp = float(r.get("price", 0))
                except (ValueError, TypeError):
                    openp = 0
                lines.append(f"\n- **{r['code']} {nm}**: 站稳 {openp:.2f} 放量不破→买;"
                             f"跌破竞价价+卖压放大→弃")
        if not watch.empty:
            lines.append(f"\n### 📋 观察区 Top{len(watch)}")
            for _, r in watch.head(3).iterrows():
                lines.append(f"\n- {r['code']} {str(r.get('name', ''))[:8]}: "
                             f"稳健分 {_fmt(r.get('score', ''))} 高开 {_fmt(r.get('gap', ''))}%")
    else:
        # 旧版兼容
        cols_map = [("code", "代码"), ("name", "名称"), ("score", "总分"),
                    ("gap", "竞价涨幅%"), ("vol_ratio", "量比"), ("amount_wan", "竞价额(万)")]
        cols = [c for c, _ in cols_map if c in df.columns]
        headers = [dict(cols_map)[c] for c in cols]
        rows = [[_fmt(r[c]) for c in cols] for _, r in df.head(12).iterrows()]
        lines.append("\n" + format_table(headers, rows))

    lines.append("\n---\n⚠️ 竞价选股为量化辅助筛选,需结合题材和消息面二次确认,"
                 "短线波动大,不构成投资建议。")
    return "\n".join(lines)


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "shape"
    if kind == "shape":
        path = latest_csv("形态选股结果_*.csv")
        msg = fmt_shape(path)
        title = f"形态选股 · {now_cn().strftime('%m-%d')}"
    elif kind == "auction_v3":
        path = latest_csv("竞价Top3结果_*.csv")
        msg = fmt_auction_v3(path)
        title = f"竞价选股Top3 · {now_cn().strftime('%m-%d')}"
    else:
        # 旧版 auction 兼容
        path = latest_csv("竞价选股结果_*.csv")
        msg = fmt_auction_v3(path)
        title = f"竞价选股 · {now_cn().strftime('%m-%d')}"
    # 当日去重:错峰多触发时,今天已成功推送过则跳过
    task_key = f"{kind}_push"
    marker_dir = RESULTS
    if already_pushed(task_key, marker_dir):
        print(f"[推送] SKIP: 今日已成功推送过 {kind},跳过重复推送")
        sys.exit(0)
    ok, info = push_retry(title, msg, channel="serverchan")
    if ok:
        mark_pushed(task_key, marker_dir)
    print(f"[推送] {'OK' if ok else 'FAIL'}: {info}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
