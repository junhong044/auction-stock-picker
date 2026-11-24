# A 股选股系统 · GitHub Actions 自动版

两条自动流程,结果均归档到 `results/` 目录:

| 流程 | 触发(北京时间) | 工作流 |
|---|---|---|
| 竞价选股 | 交易日 **9:26** | `.github/workflows/auction.yml` |
| 形态选股 + 广度择时 | 交易日 **19:15** | `.github/workflows/shape.yml` |

## 策略矩阵

| 脚本 | 策略 | 数据源 | 说明 |
|---|---|---|---|
| `auction_top3_v3.py` | **v3:情绪开关+稳健评分+双区Top3** | 东财/腾讯/DEMO | **竞价定时默认运行,每天最多推3只** |
| `auction_stock_picker.py` | v1:6约束 + 9维评分 | 东财/腾讯/DEMO | 旧版备用 |
| `auction_stock_picker_v2.py` | v2:2025最新战法优化 | 东财/腾讯/DEMO | 收紧高开/量比,换手分档,资金匹配 |
| `auction_v5_tushare.py` | v5-Top3:情绪+连板接力 | Tushare Pro(需Token) | GitHub开源策略复刻,回测胜率80.6% |
| `stock_shape_selector.py` | 六形态突破 + 广度择时 | 新浪日K + 腾讯行情 | 盘后定时运行,Sequoia-X × quant_v9 复刻 |

方法论文档:
- `GitHub竞价选股v3_每日Top3优化说明.md` — v3 情绪开关+稳健评分(最新)
- `GitHub最佳竞价选股方法_v5Top3.md` — v5 情绪+连板接力体系
- `形态选股使用说明.md` — 六形态策略 + 广度择时规则

## 竞价选股 v3(每日最优 Top3,当前默认)

融合 4 类 GitHub 最新方法:①市场情绪开关(广度择时)②竞价三因子解耦(价格意愿/资金规模/买卖失衡)③稳健评分(0.6加权+0.4最差维度)④双区输出+开盘确认清单。退潮日自动关闭买入区,宁缺毋滥。硬过滤:剔除科创板、股价<15元、ST、一字板。详见 `GitHub竞价选股v3_每日Top3优化说明.md`。

## 形态选股 + 广度择时(新增)

复刻 GitHub 开源方法:**Sequoia-X**(8.8k★,6大形态策略)× **quant_v9**(周频8因子+广度择时)。

- **两层漏斗**:先用中性宽基样本算市场广度(≥62%满仓 / 48-62%轻仓 / <48%空仓),再对强势股池拉真实历史K线识别6种形态打分。
- **6 大形态**:海龟突破 / RPS强势 / 旗形突破 / 均线放量 / 涨停洗盘 / 跌停反包。
- **数据源**:新浪日K(形态)+ 腾讯行情(广度/名称),全免费无 token,350只约2秒。
- **防偏差设计**:广度样本(中性宽基)与选股池(强势股)分开,避免幸存者偏差。
- **追高惩罚**:当日涨幅>9% 扣12分,提示回踩确认。

## 工作原理

- **触发**:cron 用 UTC。竞价 `26 1 * * 1-5` = 北京 9:26;形态 `15 11 * * 1-5` = 北京 19:15。也支持 Actions 页面手动触发。
- **数据源**:竞价用东财→腾讯→DEMO三级降级;形态用新浪日K+腾讯,失败跳过该股不中断。
- **输出**:`results/` 下按时间戳归档 CSV,由 workflow 自动 `git commit` 回仓库永久留存。

## 微信推送

选股结果会自动推送到微信(**Server酱 Turbo** 渠道),无需打开仓库:

| 项目 | 说明 |
|---|---|
| 渠道 | Server酱 Turbo(免费 5 条/天,每日 0 点重置,每天 2 条任务够用) |
| 本地定时任务凭证 | `/workspace/.push_config.json` 的 `channel: serverchan` + `serverchan_sendkey` |
| GitHub Actions 凭证 | 仓库 Secret `SERVERCHAN_SENDKEY`(加密存储) |
| 推送内容 | Markdown 榜单 + 市场情绪/广度结论 + 风险提示 |
| 失败处理 | 推送失败会在对话里明确告警,不影响结果归档 |

推送脚本:本地 `daily_push.py` / 云端 `push_from_results.py`。详见 `微信推送配置说明.md`。

> PushPlus 因需付费实名已弃用,如需更换渠道见配置说明文档。

## GitHub 使用

1. 仓库 **Settings → Actions → General → Workflow permissions** 勾选 **Read and write**。
2. 仓库 **Settings → Secrets and variables → Actions** 添加 `SERVERCHAN_SENDKEY`(Server酱 SendKey)。
3. **Actions** 标签页可手动点击 "Run workflow" 测试任一流程。
4. 交易日 9:26 竞价、19:15 形态自动运行,结果推送微信并归档到 `results/`。

> **时区**:GitHub Actions cron 用 UTC,北京时间 = UTC + 8。推送标题日期已按北京时间(Asia/Shanghai)生成。

## 本地运行

```bash
pip install -r requirements.txt

# 竞价选股
python auction_stock_picker.py --source eastmoney --top 20       # v1 全市场
python auction_stock_picker_v2.py --source eastmoney --top 15    # v2 最新战法
python auction_stock_picker.py --demo                            # 演示模式

# 形态选股 + 广度择时
python stock_shape_selector.py --top 15                          # 真实数据
python stock_shape_selector.py --top 12 --no-chart               # 纯文本(定时任务)
python stock_shape_selector.py --demo                            # 演示模式

# v5-Top3(需 Tushare Token 和相应积分)
export TUSHARE_TOKEN=你的token
python auction_v5_tushare.py                                     # 今日选股
python auction_v5_tushare.py --backtest --start 20260409 --end 20260508  # 回测
```

## v5-Top3 策略核心(80.6% 回测胜率)

来源:`github.com/huang1125677925/jingjia-stock-selector-custom-skill`

- **市场情绪判断**:3进4晋级率/炸板率,退潮日空仓
- **五维评分**:竞价量能25% + 开盘位置20% + 连板位置15% + 板块热度20% + 竞价形态20%
- **输出分级**:≥16分买入池 / 12~15分观察池 / 每天最多Top3

## 免责声明

本系统仅供个人研究、策略复盘和数据分析,不构成任何投资建议。竞价、打板、短线交易风险高,形态选股存在假突破风险,请独立判断、自负盈亏。
