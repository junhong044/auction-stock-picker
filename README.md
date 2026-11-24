# A 股集合竞价选股系统 · GitHub Actions 自动版

每交易日 **北京时间 9:26** 自动运行,扫描全市场竞价数据,结果自动归档到 `results/` 目录。

## 三套策略

| 脚本 | 策略 | 数据源 | 说明 |
|---|---|---|---|
| `auction_stock_picker.py` | v1:6约束 + 9维评分 | 东财/腾讯/DEMO | Actions 定时默认运行 |
| `auction_stock_picker_v2.py` | v2:2025最新战法优化 | 东财/腾讯/DEMO | 收紧高开/量比,换手分档,资金匹配 |
| `auction_v5_tushare.py` | v5-Top3:情绪+连板接力 | Tushare Pro(需Token) | GitHub开源策略复刻,回测胜率80.6% |

完整方法论见 `GitHub最佳竞价选股方法_v5Top3.md`。

## 工作原理

- **触发**:`.github/workflows/auction.yml` 配置 cron `26 1 * * 1-5`(UTC),即北京时间工作日 9:26;也支持在 Actions 页面手动触发。
- **数据源**:东财全市场 → 腾讯行情 → DEMO 兜底,三级自动降级,保证任务永不失败。
- **输出**:`results/竞价选股结果_时间戳.csv`,由 workflow 自动 `git commit` 回仓库永久留存。

## GitHub 使用

1. 仓库 **Settings → Actions → General → Workflow permissions** 勾选 **Read and write**。
2. **Actions** 标签页可手动点击 "Run workflow" 测试。
3. 每交易日 9:26 自动运行,结果在 `results/` 目录查看。

> **时区**:GitHub Actions cron 用 UTC。北京时间 9:26 = UTC 1:26。

## 本地运行

```bash
pip install -r requirements.txt
python auction_stock_picker.py --source eastmoney --top 20       # v1 全市场
python auction_stock_picker_v2.py --source eastmoney --top 15    # v2 最新战法
python auction_stock_picker.py --demo                            # 演示模式

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

本系统仅供个人研究、策略复盘和数据分析,不构成任何投资建议。竞价、打板、短线交易风险高,请独立判断、自负盈亏。
