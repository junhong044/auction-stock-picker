# -*- coding: utf-8 -*-
"""
微信推送模块(多渠道)
支持:PushPlus(默认)/ Server酱Turbo / 企业微信群机器人
凭证从环境变量读取,不写死在代码里:
  PushPlus:      export PUSHPLUS_TOKEN=xxx
  Server酱Turbo: export SERVERCHAN_SENDKEY=xxx
  企业微信机器人: export WECOM_WEBHOOK=xxx

用法:
  # 命令行发送
  python3.11 wechat_push.py --channel pushplus --title "选股结果" --content-file result.md
  python3.11 wechat_push.py --title "测试" --content "**加粗**内容"

  # 作为模块调用
  from wechat_push import push, format_table
  push("标题", markdown_text)
"""
import os
import sys
import json
import argparse
import requests

TIMEOUT = 15
CONFIG_FILE = os.environ.get("PUSH_CONFIG", "/workspace/.push_config.json")


def _get_cred(env_name, cfg_name):
    """凭证优先级:环境变量 > 配置文件。配置文件不入库(见.gitignore)。"""
    v = os.environ.get(env_name)
    if v:
        return v
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get(cfg_name)
    except Exception:
        pass
    return None


def _session():
    s = requests.Session()
    s.trust_env = False
    return s


# ---------------- 各渠道推送实现 ----------------
def push_pushplus(title, content, token=None, topic=None):
    """PushPlus 推送加: POST https://www.pushplus.plus/send"""
    token = token or _get_cred("PUSHPLUS_TOKEN", "pushplus_token")
    if not token:
        return False, "未设置 PUSHPLUS_TOKEN 环境变量"
    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": "markdown",   # 关键:必须显式指定,否则html模板吞换行
    }
    if topic:
        payload["topic"] = topic   # 一对多群组推送(可选)
    try:
        r = _session().post("https://www.pushplus.plus/send", json=payload, timeout=TIMEOUT)
        d = r.json()
        if d.get("code") == 200:
            return True, "success"
        return False, f"code={d.get('code')} msg={d.get('msg') or d.get('data')}"
    except Exception as e:
        return False, str(e)[:120]


def push_serverchan(title, content, key=None):
    """Server酱 Turbo: POST https://sctapi.ftqq.com/{key}.send"""
    key = key or _get_cred("SERVERCHAN_SENDKEY", "serverchan_sendkey")
    if not key:
        return False, "未设置 SERVERCHAN_SENDKEY 环境变量"
    try:
        r = _session().post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": title, "desp": content},  # desp 支持 markdown
            timeout=TIMEOUT,
        )
        d = r.json()
        if d.get("code") == 0:
            return True, "success"
        return False, f"code={d.get('code')} msg={d.get('message')}"
    except Exception as e:
        return False, str(e)[:120]


def push_wecom(title, content, webhook=None):
    """企业微信群机器人: POST webhook,markdown消息"""
    webhook = webhook or _get_cred("WECOM_WEBHOOK", "wecom_webhook")
    if not webhook:
        return False, "未设置 WECOM_WEBHOOK 环境变量"
    # 企业微信markdown单条4096字节,超长截断
    text = f"## {title}\n\n{content}"[:4000]
    payload = {"msgtype": "markdown", "markdown": {"content": text}}
    try:
        r = _session().post(webhook, json=payload, timeout=TIMEOUT)
        d = r.json()
        if d.get("errcode") == 0:
            return True, "success"
        return False, f"errcode={d.get('errcode')} errmsg={d.get('errmsg')}"
    except Exception as e:
        return False, str(e)[:120]


CHANNELS = {
    "pushplus": push_pushplus,
    "serverchan": push_serverchan,
    "wecom": push_wecom,
}


def push(title, content, channel=None):
    """统一推送入口。channel缺省自动按优先级选第一个配置了凭证的渠道。"""
    channel = (channel or os.environ.get("PUSH_CHANNEL") or "").lower()
    if not channel:
        # 先看配置文件里指定的渠道
        try:
            if os.path.exists(CONFIG_FILE):
                channel = json.load(open(CONFIG_FILE, encoding="utf-8")).get("channel", "")
        except Exception:
            pass
        # 自动检测:优先 pushplus → serverchan → wecom
        if not channel:
            if _get_cred("PUSHPLUS_TOKEN", "pushplus_token"):
                channel = "pushplus"
            elif _get_cred("SERVERCHAN_SENDKEY", "serverchan_sendkey"):
                channel = "serverchan"
            elif _get_cred("WECOM_WEBHOOK", "wecom_webhook"):
                channel = "wecom"
        if not channel:
            return False, "未配置任何推送渠道(请设置凭证:创建 /workspace/.push_config.json 或设环境变量 PUSHPLUS_TOKEN)"
    fn = CHANNELS.get(channel)
    if not fn:
        return False, f"未知渠道: {channel}"
    return fn(title, content)


# ---------------- 格式化工具 ----------------
def format_table(headers, rows, note=""):
    """生成 Markdown 表格"""
    lines = []
    if note:
        lines.append(note)
        lines.append("")
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def format_ranking(title, df, cols=None, note=""):
    """从 DataFrame 生成排名 Markdown"""
    if df is None or df.empty:
        return f"### {title}\n\n无结果。"
    cols = cols or list(df.columns)
    rows = [[str(r[c]) for c in cols] for _, r in df.iterrows()]
    return f"### {title}\n\n" + format_table(cols, rows, note)


# ---------------- CLI ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default=None, help="渠道: pushplus/serverchan/wecom(缺省自动)")
    ap.add_argument("--title", required=True)
    ap.add_argument("--content", default=None)
    ap.add_argument("--content-file", default=None)
    args = ap.parse_args()

    if args.content_file:
        content = open(args.content_file, encoding="utf-8").read()
    elif args.content:
        content = args.content.replace("\\n", "\n")
    else:
        ap.error("需提供 --content 或 --content-file")

    ok, msg = push(args.title, content, args.channel)
    if ok:
        print(f"[OK] 微信推送成功: {args.title}")
    else:
        print(f"[FAIL] 推送失败: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
