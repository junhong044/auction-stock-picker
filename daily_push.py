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

sys.path.insert(0, "/workspace")
from wechat_push import push, format_table

WORKSPACE = "/workspace"


def run_script(mode):
    """运行对应选股脚本,返回(是否成功, 输出文本)"""
    if mode == "shape":
        cmd = ["python3.11", f"{WORKSPACE}/stock_shape_selector.py", "--top", "12", "--no-chart"]
    else:
        cmd = ["python3.11", f"{WORKSPACE}/auction_stock_picker.py",
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
    today = dt.datetime.now().strftime("%Y-%m-%d")
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


def build_auction_message(out):
    """构造竞价选股推送内容"""
    csv = load_latest_csv("竞价选股结果_*.csv")
    today = dt.datetime.now().strftime("%Y-%m-%d")
    lines = [f"## 📊 A股竞价选股 · {today}"]
    # 提取数据源
    for line in out.splitlines():
        if "快照获取" in line or "数据源=" in line:
            lines.append(f"\n{line.strip()}")
            break
    if csv:
        df = df_from_csv(csv)
        if not df.empty:
            cols_map = [("code", "代码"), ("name", "名称"), ("score", "总分"),
                        ("gap", "竞价涨幅%"), ("vol_ratio", "量比"), ("amount_wan", "竞价额(万)")]
            cols = [c for c, _ in cols_map if c in df.columns]
            headers = [dict(cols_map)[c] for c in cols]
            rows = []
            for _, r in df.head(12).iterrows():
                row = []
                for c in cols:
                    v = r[c]
                    row.append(f"{v:.2f}" if isinstance(v, float) else str(v))
                rows.append(row)
            lines.append(f"\nTop{min(12, len(df))}:\n")
            lines.append(format_table(headers, rows))
        else:
            lines.append("\n今日无候选股票(约束过滤所致)。")
    else:
        lines.append("\n今日无候选股票,未生成结果。")
    lines.append("\n---\n⚠️ 竞价选股为量化辅助筛选,需结合题材和消息面二次确认,不构成投资建议。")
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
        title = f"A股形态选股 · {dt.datetime.now():%m-%d}"
        msg = build_shape_message(out)
    else:
        title = f"A股竞价选股 · {dt.datetime.now():%m-%d}"
        msg = build_auction_message(out)

    print("\n" + "=" * 50)
    print("微信推送内容预览:")
    print("=" * 50)
    print(msg)

    if args.dry_run:
        print("\n[--dry-run] 未实际推送。")
        return

    success, info = push(title, msg, args.channel)
    if success:
        print(f"\n[OK] 微信推送成功: {title}")
    else:
        print(f"\n[FAIL] 微信推送失败: {info}")
        sys.exit(1)


if __name__ == "__main__":
    main()
