# A 股集合竞价选股系统 · GitHub Actions 自动版

每交易日 **北京时间 9:26** 自动运行,扫描全市场竞价数据,按「6 约束 + 9 维加权评分」选出 Top20,结果自动归档到 `results/` 目录。

## 工作原理

- **触发**:`.github/workflows/auction.yml` 配置 cron `26 1 * * 1-5`(UTC),即北京时间工作日 9:26;也支持在 Actions 页面手动触发。
- **数据源**:东财全市场 → 腾讯行情 → DEMO 兜底,三级自动降级,保证任务永不失败。
- **输出**:`results/竞价选股结果_时间戳.csv`,由 workflow 自动 `git commit` 回仓库永久留存。

## 如何在 GitHub 上使用

1. Fork 或 Clone 本仓库到你自己的 GitHub 账号。
2. 进入仓库 **Settings → Actions → General → Workflow permissions**,勾选 **Read and write permissions**(允许自动提交结果)。
3. 在 **Actions** 标签页可手动点击 "Run workflow" 立即测试。
4. 之后每交易日 9:26 自动运行,结果在 `results/` 目录查看。

> **时区说明**:GitHub Actions 的 cron 一律用 UTC。北京时间 = UTC+8,故 9:26 对应 UTC 1:26。

## 本地运行

```bash
pip install -r requirements.txt
python auction_stock_picker.py --source eastmoney --top 20       # 东财全市场
python auction_stock_picker.py --source tencent --pool mypool.csv # 腾讯自选池
python auction_stock_picker.py --demo                            # 演示模式
```

## 策略:6 约束 + 9 维评分

| 6 约束 | 默认 | | 9 维评分 | 权重 |
|--------|------|--|----------|------|
| 竞价涨幅 | 2%~9.5% | | 竞价涨幅 | 20 |
| 竞价成交额 | ≥1000 万 | | 量比 | 15 |
| 量比 | ≥1.2 | | 竞价额 | 12 |
| 流通市值 | 20~500 亿 | | 换手率 | 10 |
| 排除 ST | 是 | | 买盘力量 | 12 |
| 排除一字板 | 是 | | 市值适中 | 10 |
| | | | 涨停距离 | 8 |
| | | | 活跃度 | 8 |
| | | | 动量 | 5 |

> 风险提示:本工具为量化辅助筛选,不构成投资建议。竞价高开存在冲高回落风险,请结合题材、消息面二次确认。
