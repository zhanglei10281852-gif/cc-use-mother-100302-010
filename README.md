# 新品组合市场适配复盘

面向新品组合的市场适配复盘平台：为每款产品按 **行业 / 工况 / 客户阶段 / 配置版本** 登记机会与反馈，
把可验证的采用结果、失单原因、现场问题和改进承诺归并到同一时间线；阶段评审冻结当时样本，
区分事实、推断与待验证假设后再决策；管理层可通过 API 或命令行对比各新品在不同细分市场的真实适配度，
并追溯每项结论采用的记录。纯 Python 标准库实现，不依赖浏览器、外部数据库或其他运行服务。

## 核心规则

- **统一登记口径**：机会、反馈（试装/报价/故障/维护）、采用、失单、现场问题、改进承诺共用一套
  记录模型与统计入口（`statistics.py`），各部门不再各算各的。
- **幂等导入**：记录以来源系统的稳定标识 + 内容指纹去重。重复导入零变化；同标识不同内容判定冲突并拒绝。
- **评审冻结**：开启阶段评审即冻结当时样本（记录标识 + 指纹）与统计结果，之后不可改写。
- **事实 / 推断 / 假设**：事实与推断必须引用冻结样本内的记录；假设必须附验证计划。
  结论声明细分范围，证据必须落在范围内——机器人客户的结论不能套用到风电或港口。
- **四种决策**：继续推广 / 限制场景 / 安排改进（须引用样本内的改进承诺）/ 退出。决策后评审关闭。
- **迟到数据只触发修订**：冻结后到达、但业务发生时间早于冻结时点的记录不进冻结统计，
  只标记为迟到数据，通过修订生成新版本（原版本完整保留）。
- **配置版本隔离**：配置升级只新增版本，历史反馈永远留在原版本上，按版本查询互不影响。
- **可追溯**：每项指标附带构成它的记录标识；结论可逐条回溯到冻结样本内的记录并校验指纹。

## 运行环境

- Python 3.11 或更高版本
- Linux、macOS 或 Windows

## 运行测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## 编译检查

```bash
python3 -m compileall -q src tests run_cli.py
```

## 命令行冒烟

```bash
python3 run_cli.py
```

端到端演示：八款新品导入（含幂等重复导入）→ 阶段评审冻结 → 迟到数据触发修订 →
分行业对比与结论追溯，输出 JSON 摘要。

## 命令行用法

```bash
PYTHONPATH=src python3 -m product_portfolio.cli --store data/portfolio.json add-product NPR-01 "机器人关节精密减速器"
PYTHONPATH=src python3 -m product_portfolio.cli --store data/portfolio.json add-config NPR-01 A1
PYTHONPATH=src python3 -m product_portfolio.cli --store data/portfolio.json add-config NPR-01 A2 --supersedes A1
PYTHONPATH=src python3 -m product_portfolio.cli --store data/portfolio.json import records.json
PYTHONPATH=src python3 -m product_portfolio.cli --store data/portfolio.json timeline NPR-01 --industry 机器人
PYTHONPATH=src python3 -m product_portfolio.cli --store data/portfolio.json review-freeze NPR-01 G2-上市复盘 --industry 机器人
PYTHONPATH=src python3 -m product_portfolio.cli --store data/portfolio.json review-finding RV-NPR-01-G2-上市复盘-v1 \
    --kind fact --text "试装反馈稳定" --evidence NPR-01-R002 --industry 机器人
PYTHONPATH=src python3 -m product_portfolio.cli --store data/portfolio.json review-decide RV-NPR-01-G2-上市复盘-v1 \
    --action continue --rationale "已验证采用且现场问题可控"
PYTHONPATH=src python3 -m product_portfolio.cli --store data/portfolio.json review-show RV-NPR-01-G2-上市复盘-v1
PYTHONPATH=src python3 -m product_portfolio.cli --store data/portfolio.json review-revise RV-NPR-01-G2-上市复盘-v1
PYTHONPATH=src python3 -m product_portfolio.cli --store data/portfolio.json compare --group-by industry
PYTHONPATH=src python3 -m product_portfolio.cli --store data/portfolio.json finding-evidence RV-NPR-01-G2-上市复盘-v1-F1
```

记录 JSON 文件格式（数组或含 `records` 数组的对象）：

```json
[
  {
    "record_id": "NPR-01-R001",
    "product_code": "NPR-01",
    "industry": "机器人",
    "condition": "冲击载荷",
    "customer_stage": "试装",
    "config_version": "A1",
    "kind": "feedback",
    "occurred_at": "2026-03-01T08:00:00+00:00",
    "source": "试装组",
    "details": {"channel": "trial", "summary": "试装运行平稳"}
  }
]
```

## HTTP API

```bash
PYTHONPATH=src python3 -m product_portfolio.http_api --store data/portfolio.json --port 8000
```

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/products` | 登记新品 |
| POST | `/config-versions` | 登记配置版本（升级用 `supersedes`） |
| POST | `/imports` | 批量导入记录（幂等，返回新增/重复/拒绝清单） |
| GET | `/timeline?product=...&industry=...` | 统一时间线 |
| GET | `/metrics?product=...` | 统一口径指标（含溯源清单） |
| POST | `/reviews` | 开启阶段评审并冻结样本 |
| GET | `/reviews/{id}` | 评审详情（含迟到数据与修订链） |
| POST | `/reviews/{id}/findings` | 登记结论（fact / inference / hypothesis） |
| POST | `/reviews/{id}/decision` | 登记决策（continue / restrict / improve / exit） |
| POST | `/reviews/{id}/revisions` | 生成修订版本 |
| GET | `/compare?group_by=industry` | 分细分市场对各新品适配度 |
| GET | `/findings/{id}/evidence` | 追溯结论采用的记录 |

## 适配度评分口径

`fit_score` 由四个组件加权汇总（权重：win_rate 0.4，adoption_rate / resolution_rate /
fulfillment_rate 各 0.2），分母为 0 的组件剔除后权重归一化，全部缺失时评分为空（数据不足）。
组件定义与截断规则见 `src/product_portfolio/statistics.py` 模块文档；评分永远落在 [0, 1]，
原始比率（如机会漏登导致的 adoption_rate > 1）单独保留以便发现登记口径问题。

## 模块结构

```
src/product_portfolio/
  contracts.py   基础领域契约（稳定标识、不可变版本、冲突检测）
  models.py      记录、细分、评审、结论、决策等领域模型
  store.py       内存索引 + JSON 原子写持久化
  statistics.py  统一统计口径（全平台唯一取数入口）
  service.py     应用服务层（程序化 API）
  http_api.py    标准库 HTTP API
  cli.py         命令行接口
  demo.py        确定性演示数据（八款新品）
```
