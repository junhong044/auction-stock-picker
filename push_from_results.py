# -*- coding: utf-8 -*-
"""云端(Actions)专用:读 results/ 当天 CSV → 格式化 Markdown → 微信推送。

凭证来自环境变量 SERVERCHAN_SENDKEY(由工作流从 secrets 注入)。
用法: python push_from_results.py shape | auction
"""
import os
import sys
import glob
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wechat_push import push, format_table

RESULTS = os.environ.get("OUTPUT_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "results"))


def latest_csv(pattern):
    files = glob.glob(os.path.join(RESULTS, pattern))
    return max(files, key=os.path.getmtime) if files else None


def fmt(path, kind):
    today = dt.datetime.now().strftime("%Y-%m-%d")
    if kind == "shape":
        lines = [f"## 📊 A股形态选股 · {today}"]
        cols_map = [("code", "代码"), ("name", "名称"), ("score", "综合分"),
                    ("chg", "涨跌幅%"), ("vol_ratio", "量比"), ("shapes", "命中形态")]
        tail = "⚠️ 形态选股存在假突破风险,涨幅>9%追高需等回踩确认。不构成投资建议。"
    else:
        lines = [f"## 📊 A股竞价选股 · {today}"]
        cols_map = [("code", "代码"), ("name", "名称"), ("score", "总分"),
                    ("gap", "竞价涨幅%"), ("vol_ratio", "量比"), ("amount_wan", "竞价额(万)")]
        tail = "⚠️ 竞价选股为量化辅助筛选,需结合题材二次确认。不构成投资建议。"

    if not path:
        lines.append("\n今日未生成结果CSV(可能无候选或数据源受限)。")
        lines.append("\n---\n" + tail)
        return "\n".join(lines)

    import pandas as pd
    df = pd.read_csv(path, encoding="utf-8-sig")
    if df.empty:
        lines.append("\n今日无候选股票(约束过滤所致)。")
    else:
        cols = [c for c, _ in cols_map if c in df.columns]
        headers = [dict(cols_map)[c] for c in cols]
        rows = []
        for _, r in df.head(12).iterrows():
            row = [f"{r[c]:.2f}" if isinstance(r[c], float) else str(r[c]) for c in cols]
            rows.append(row)
        lines.append(f"\n共 {len(df)} 只,展示 Top{min(12, len(df))}:\n")
        lines.append(format_table(headers, rows))
    lines.append("\n---\n" + tail)
    return "\n".join(lines)


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "shape"
    pattern = "形态选股结果_*.csv" if kind == "shape" else "竞价选股结果_*.csv"
    title = f"{'形态' if kind == 'shape' else '竞价'}选股 · {dt.datetime.now().strftime('%m-%d')}"
    msg = fmt(latest_csv(pattern), kind)
    ok, info = push(title, msg, channel="serverchan")
    print(f"[推送] {'OK' if ok else 'FAIL'}: {info}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
