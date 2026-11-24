# A 股选股系统 · GitHub Actions 自动版

两条自动流程,结果均归档到 `results/` 目录:

| 流程 | 触发(北京时间) | 工作流 |
|---|---|---|
| 竞价选股 | 交易日 **9:26** | `.github/workflows/auction.yml` |
| 形态选股 + 广度择时 | 交易日 **19:15** | `.github/workflows/shape.yml` |

## 策略矩阵

| 脚本 | 策略 | 数据源 | 说明 |
|---|---|---|---|
| `auction_stock_picker.py` | v1:6约束 + 9维评分 | 东财/腾讯/DEMO | 竞价定时默认运行 |
| `auction_stock_picker_v2.py` | v2:2025最新战法优化 | 东财/腾讯/DEMO | 收紧高开/量比,换手分档,资金匹配 |
| `auction_v5_tushare.py` | v5-Top3:情绪+连板接力 | Tushare Pro(需Token) | GitHub开源策略复刻,回测胜率80.6% |
| `stock_shape_selector.py` | 六形态突破 + 广度择时 | 新浪日K + 腾讯行情 | 盘后定时运行,Sequoia-X × quant_v9 复刻 |

方法论文档:
- `GitHub最佳竞价选股方法_v5Top3.md` — v5 情绪+连板接力体系
- `形态选股使用说明.md` — 六形态策略 + 广度择时规则

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

## GitHub 使用

1. 仓库 **Settings → Actions → General → Workflow permissions** 勾选 **Read and write**。
2. **Actions** 标签页可手动点击 "Run workflow" 测试任一流程。
3. 交易日 9:26 竞价、19:15 形态自动运行,结果在 `results/` 查看。

> **时区**:GitHub Actions cron 用 UTC,北京时间 = UTC + 8。

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
