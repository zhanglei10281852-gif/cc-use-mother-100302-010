"""新品市场适配复盘平台命令行冒烟入口。

运行端到端演示：八款新品导入（含幂等重复导入）→ 阶段评审冻结 →
迟到数据触发修订 → 管理层分行业对比与结论追溯。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from product_portfolio.demo import run_demo


def main() -> None:
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
