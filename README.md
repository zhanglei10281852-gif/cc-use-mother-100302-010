# 新品组合市场适配复盘

面向弗兰德中国区新品组合的复盘平台：为每款产品按 **行业 / 工况 / 客户阶段 / 配置版本**
统一登记机会与反馈，把可验证的采用结果、失单原因、现场问题和改进承诺归并到同一
时间线；阶段评审冻结当时样本并区分事实、推断与待验证假设，据此决定继续推广、限制
场景、安排改进或退出。管理层可通过 HTTP API 或命令行对比各新品在不同细分市场的
真实适配度，并追溯每项结论采用的记录。

纯 Python 标准库实现，不依赖浏览器、外部数据库或其他运行服务。

## 核心规则

- **统一口径**：行业、工况等文本维度登记时规范化（去空白、忽略大小写匹配），客户
  阶段、记录类型、严重度等使用受控词表，避免各部门各写各的口径。
- **幂等导入**：记录以来源系统的稳定标识登记，内容一致的重复导入直接判重跳过，
  统计不变；同一标识出现不同内容视为冲突，拒绝入库并明示。
- **配置版本隔离**：配置升级登记为新版本（可指明 `supersedes` 替代关系），旧版本
  的反馈永远保留在原版本上，统计按版本精确拆分。
- **冻结评审**：创建评审即冻结当时样本（记录标识 + 内容摘要 + 指标快照）。结论
  必须标注 事实 / 推断 / 待验证假设：事实与推断必须引用冻结样本内的记录，假设
  不得引用记录；引用样本外记录（例如把机器人客户的结论套到风电）会被拒绝。
- **迟到数据只能触发修订**：冻结后到达的记录不改写历史修订，只能通过 `revise`
  生成新修订重新冻结样本；历史修订及其决策原样保留。
- **全程可追溯**：每项统计指标附带证据记录标识；评审追溯接口会解析每项结论引用
  的记录原文，并校验冻结样本至今未被篡改。

## 适配度评分

透明、确定性的评分公式（见 `src/product_portfolio/analytics.py`）：

- `win_rate = 验证通过的采用结果数 / (采用结果数 + 失单数)`
- `issue_load = 严重度加权现场问题数 / max(采用结果数, 1)`（权重 low/medium/high/critical = 1/2/4/8）
- `fit_score = clamp(round(100 * win_rate - 10 * issue_load), 0, 100)`；没有任何采用/失单记录时为 `null`（样本不足）

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

## 端到端演示

```bash
python3 run_cli.py
```

演示完整闭环：登记产品与配置版本 → 幂等导入（含重复导入判重）→ 冻结评审 →
结论与决策 → 迟到数据触发修订 → 跨细分市场适配度对比。

## 命令行用法

```bash
export PYTHONPATH=src
python3 -m product_portfolio.cli --store portfolio.json add-product --code P1 --name 减速器1号 --launched-at 2025-10-01
python3 -m product_portfolio.cli --store portfolio.json add-config --product P1 --version V1 --released-at 2025-10-01
python3 -m product_portfolio.cli --store portfolio.json add-config --product P1 --version V2 --released-at 2026-03-01 --supersedes V1
python3 -m product_portfolio.cli --store portfolio.json import --file records.json        # 幂等，可重复执行
python3 -m product_portfolio.cli --store portfolio.json timeline --product P1 --industry 机器人
python3 -m product_portfolio.cli --store portfolio.json review-create --product P1 --industry 机器人
python3 -m product_portfolio.cli --store portfolio.json review-conclude --review RV-0001 --statement 试装转化成立 --evidence-class fact --records FB-001
python3 -m product_portfolio.cli --store portfolio.json review-decide --review RV-0001 --kind continue --rationale 结果可验证
python3 -m product_portfolio.cli --store portfolio.json late --review RV-0001              # 查看迟到数据
python3 -m product_portfolio.cli --store portfolio.json review-revise --review RV-0001 --reason 迟到采用结果到达
python3 -m product_portfolio.cli --store portfolio.json compare                            # 八款新品 × 细分市场
python3 -m product_portfolio.cli --store portfolio.json trace --product P1 --industry 机器人
python3 -m product_portfolio.cli --store portfolio.json trace-review --review RV-0001
python3 -m product_portfolio.cli --store portfolio.json serve --port 8000                  # 启动 HTTP API
```

导入文件格式：

```json
{
  "opportunities": [{"opportunity_code": "OP-1", "product_code": "P1", "industry": "机器人",
                     "conditions": "高频往复", "customer_stage": "trial", "config_version": "V1",
                     "source": "机器人行业组"}],
  "records": [{"record_id": "FB-001", "product_code": "P1", "industry": "机器人",
               "conditions": "高频往复", "customer_stage": "trial", "config_version": "V1",
               "kind": "adoption", "occurred_at": "2025-11-10", "source": "机器人行业组",
               "summary": "试装通过并下单", "details": {"verification_ref": "PO-2025-118"}}]
}
```

记录类型与必填字段：`adoption`（`verification_ref`，采用结果必须可验证）、
`lost_order`（`reason`）、`field_issue`（`severity`: low/medium/high/critical）、
`improvement_commitment`（`owner`，可选 `status`/`due_date`）。

## HTTP API

`serve` 命令启动后可用（全部为 JSON 请求/响应）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| GET/POST | `/products` | 产品列表 / 登记新品 |
| POST | `/config-versions` | 登记配置版本（可带 `supersedes`） |
| POST | `/imports` | 幂等导入 `{"opportunities": [...], "records": [...]}` |
| GET | `/timeline?product=P1&industry=机器人` | 统一时间线 |
| POST | `/reviews` | 创建评审并冻结样本 |
| GET | `/reviews` `/reviews/{id}` | 查询评审 |
| POST | `/reviews/{id}/conclusions` | 追加结论（事实/推断/假设） |
| POST | `/reviews/{id}/decision` | 决策（continue/restrict/improve/exit） |
| POST | `/reviews/{id}/revisions` | 迟到数据触发修订 |
| POST | `/reviews/{id}/close` | 关闭评审 |
| GET | `/compare?industry=...` | 各产品 × 细分市场适配度对比 |
| GET | `/trace?product=P1&industry=机器人` | 指标背后的记录原文 |
| GET | `/trace-review?review=RV-0001` | 评审结论追溯与样本完整性校验 |

## 代码结构

- `src/product_portfolio/contracts.py` — 基础领域契约（稳定标识、不可变演进、冲突检测）
- `src/product_portfolio/models.py` — 领域模型与受控词表（统一口径）
- `src/product_portfolio/analytics.py` — 适配度指标与细分市场对比（纯函数）
- `src/product_portfolio/store.py` — 内存 / JSON 文件存储（原子写入）
- `src/product_portfolio/service.py` — 应用服务（Python API，承载全部业务规则）
- `src/product_portfolio/http_api.py` — HTTP JSON API（仅标准库）
- `src/product_portfolio/cli.py` — 命令行入口
