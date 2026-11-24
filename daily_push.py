# -*- coding: utf-8 -*-
"""
选股 + 微信推送 统一编排
定时任务的唯一入口:跑选股脚本 → 读最新CSV → 格式化Markdown → 推微信

用法:
  python3.11 daily_push.py shape              # 形态选股+广度择时 → 微信
  python3.11 daily_push.py auction            # 竞价选股 → 微信
  python3.11 daily_push.py shape --dry-run    # 只生成不推送(测试格式)
  python3.11 daily_push.py shape --channel pushplus   # 指定渠道

凭证:在 /workspace/.push_config.json 配置(不入库),或设环境变量。
"""
import os
import sys
import glob
import argparse
import subprocess
import datetime as dt
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
def now_cn():
    """北京时间,保证推送标题日期正确(不受运行机器时区影响)"""
    return dt.datetime.now(CN_TZ)

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKSPACE)
from wechat_push import (push, format_table, push_retry, already_pushed, mark_pushed,
                         remote_already_pushed, mark_remote_pushed)


def run_script(mode):
    """运行对应选股脚本,返回(是否成功, 输出文本)"""
    py = sys.executable  # 用当前解释器,不写死 python3.11
    if mode == "shape":
        cmd = [py, os.path.join(WORKSPACE, "stock_shape_selector.py"), "--top", "12", "--no-chart"]
    elif mode == "auction":
        # v3: 情绪开关 + 稳健评分 + 双区 Top3
        cmd = [py, os.path.join(WORKSPACE, "auction_top3_v3.py"),
               "--source", "eastmoney", "--no-chart"]
    else:
        cmd = [py, os.path.join(WORKSPACE, "auction_stock_picker.py"),
               "--source", "eastmoney", "--top", "12", "--no-chart"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=WORKSPACE)
    out = r.stdout + "\n" + r.stderr
    return r.returncode == 0, out


def extract_breadth(out):
    """从形态脚本输出提取广度择时结论"""
    for line in out.splitlines():
        if "市场广度" in line:
            return line.strip().lstrip("[*]").strip()
    return ""


def load_latest_csv(pattern):
    files = glob.glob(f"{WORKSPACE}/{pattern}")
    if not files:
        return None
    latest = max(files, key=os.path.getmtime)
    # 只要最近10分钟内生成的,避免读到旧文件
    if dt.datetime.now().timestamp() - os.path.getmtime(latest) > 600:
        return None
    return latest


def df_from_csv(path):
    import pandas as pd
    return pd.read_csv(path, encoding="utf-8-sig")


def build_shape_message(out):
    """构造形态选股推送内容"""
    breadth_line = extract_breadth(out)
    csv = load_latest_csv("形态选股结果_*.csv")
    today = now_cn().strftime("%Y-%m-%d")
    lines = [f"## 📊 A股形态选股 · {today}"]
    if breadth_line:
        lines.append(f"\n**{breadth_line}**")
        if "退潮" in breadth_line:
            lines.append("\n> 🔴 按 v9 纪律今日空仓避险,以下榜单仅作观察,不建议实盘出手。")
        elif "分歧" in breadth_line:
            lines.append("\n> 🟡 精选形态最强的少数标的,控制仓位。")
        else:
            lines.append("\n> 🟢 市场健康,可正常出手。")
    if csv:
        df = df_from_csv(csv)
        if not df.empty:
            cols_map = [("code", "代码"), ("name", "名称"), ("score", "综合分"),
                        ("chg", "涨跌幅%"), ("vol_ratio", "量比"), ("shapes", "命中形态")]
            cols = [c for c, _ in cols_map if c in df.columns]
            headers = [dict(cols_map)[c] for c in cols]
            rows = []
            for _, r in df.head(12).iterrows():
                row = []
                for c in cols:
                    v = r[c]
                    row.append(f"{v:.2f}" if isinstance(v, float) else str(v))
                rows.append(row)
            lines.append(f"\n命中形态 **{len(df)}** 只,展示 Top{min(12, len(df))}:\n")
            lines.append(format_table(headers, rows))
        else:
            lines.append("\n今日无股票命中形态。")
    lines.append("\n---\n⚠️ 形态选股存在假突破风险,涨幅>9%追高需等回踩确认。结合题材/消息面二次确认,不构成投资建议。")
    return "\n".join(lines)


def _fmt(v):
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def build_auction_message(out):
    """v3 竞价推送:情绪开关 + 🏆首选Top3 + 📋观察区 + 开盘确认清单"""
    csv = load_latest_csv("竞价Top3结果_*.csv")
    # 兼容旧脚本输出
    if not csv:
        csv = load_latest_csv("竞价选股结果_*.csv")
    today = now_cn().strftime("%Y-%m-%d")
    lines = [f"## 📊 A股竞价选股 v3 · {today}"]

    # 数据源状态
    degraded = [l.strip() for l in out.splitlines() if "降级" in l or "源不可用" in l]
    if any("DEMO" in d for d in degraded):
        lines.append("\n⚠️ 数据源降级至 DEMO 模拟数据,仅供演示,非真实行情!")
    elif degraded:
        lines.append("\n> " + " / ".join(degraded[:2]))

    # 情绪状态(从CSV首行读取)
    sentiment_state, sentiment_temp = "", ""
    if csv:
        df = df_from_csv(csv)
        if "sentiment_state" in df.columns and not df.empty:
            sentiment_state = str(df.iloc[0]["sentiment_state"])
            sentiment_temp = str(df.iloc[0].get("sentiment_temp", ""))
    if sentiment_state:
        icons = {"主升": "🔥", "健康": "✅", "分歧": "⚡", "退潮": "🛑"}
        lines.append(f"\n**市场情绪: {icons.get(sentiment_state, '')} {sentiment_state}"
                     f"(温度 {sentiment_temp})**")
        if sentiment_state == "退潮":
            lines.append("\n> 🛑 退潮防守:今日首选买入区关闭,以下仅作观察,不建议实盘出手。")
        elif sentiment_state == "分歧":
            lines.append("\n> ⚡ 多空分歧:减量降仓,精选少数标的。")

    if not csv:
        lines.append("\n今日无竞价结果(可能非交易时段或数据源不可用)。")
        lines.append("\n---\n⚠️ 量化辅助筛选,需结合题材消息面二次确认,不构成投资建议。")
        return "\n".join(lines)

    df = df_from_csv(csv)
    if df.empty:
        lines.append("\n今日无候选股票(硬约束过滤所致,宁缺毋滥)。")
        lines.append("\n---\n⚠️ 量化辅助筛选,不构成投资建议。")
        return "\n".join(lines)

    # v3 CSV 有 zone 列 -> 双区推送
    if "zone" in df.columns and "score_worst" in df.columns:
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
            # 确认清单
            lines.append("\n**开盘确认(9:30-9:35):**")
            for _, r in buy.head(3).iterrows():
                nm = str(r.get("name", ""))[:8]
                openp = r.get("price", 0)
                try:
                    openp = float(openp)
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
        # 旧版兼容:Top12 表格
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
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["shape", "auction"], help="选股模式")
    ap.add_argument("--channel", default=None, help="推送渠道(缺省自动)")
    ap.add_argument("--dry-run", action="store_true", help="只生成内容不推送")
    args = ap.parse_args()

    print(f"[*] 运行 {args.mode} 选股脚本...")
    ok, out = run_script(args.mode)
    # 打印脚本输出供日志查看
    print("\n".join(l for l in out.splitlines() if l.strip())[:3000])

    if args.mode == "shape":
        title = f"A股形态选股 · {now_cn():%m-%d}"
        msg = build_shape_message(out)
    else:
        title = f"A股竞价选股 · {now_cn():%m-%d}"
        msg = build_auction_message(out)

    print("\n" + "=" * 50)
    print("微信推送内容预览:")
    print("=" * 50)
    print(msg)

    if args.dry_run:
        print("\n[--dry-run] 未实际推送。")
        return

    # 竞价task_key与云端Actions统一(云端叫auction_v3_push),否则两端互不认识会双推
    task_key = "auction_v3_push" if args.mode == "auction" else "shape_push"

    # 跨端去重:先查本地标记,再查仓库标记(云端兜底若已推过则App跳过)
    if already_pushed(task_key):
        print(f"\n[SKIP] 本地标记显示今日已推送过 {args.mode},跳过。")
        return
    if remote_already_pushed(task_key):
        print(f"\n[SKIP] 仓库标记显示今日已推送过 {args.mode}(云端兜底已推),跳过。")
        return

    success, info = push_retry(title, msg, args.channel)
    if success:
        mark_pushed(task_key)                       # 本地标记
        if mark_remote_pushed(task_key):            # 写仓库标记 → 云端Actions读到会跳过
            print("[*] 已写入仓库推送标记,云端兜底今日将自动跳过。")
        else:
            print("[WARN] 仓库标记写入失败,云端兜底可能重复推送(不影响本次送达)。")
        print(f"\n[OK] 微信推送成功: {title}")
    else:
        print(f"\n[FAIL] 微信推送失败(已重试3次): {info}")
        sys.exit(1)


if __name__ == "__main__":
    main()
