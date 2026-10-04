"""新品组合市场适配复盘命令行冒烟入口。"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from product_portfolio import MarketRelease


def main() -> None:
    item = MarketRelease(**{'release_code': 'release-code-001', 'product_code': 'product-code-001', 'segment': 'segment-001', 'state': 'draft'})
    print(json.dumps({"item": asdict(item), "fingerprint": item.fingerprint()}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
