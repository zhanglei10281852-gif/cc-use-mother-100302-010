"""新品组合市场适配复盘基础契约测试。"""

import unittest

from product_portfolio import MarketRelease, unique_by_identity


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.values = {'release_code': 'release-code-001', 'product_code': 'product-code-001', 'segment': 'segment-001', 'state': 'draft'}

    def test_fingerprint_is_stable(self) -> None:
        left = MarketRelease(**self.values)
        right = MarketRelease(**dict(reversed(list(self.values.items()))))
        self.assertEqual(left.fingerprint(), right.fingerprint())

    def test_evolve_keeps_original(self) -> None:
        original = MarketRelease(**self.values)
        change_key = next(key for key, value in self.values.items() if isinstance(value, str))
        changed = original.evolve(**{change_key: "revised-value"})
        self.assertNotEqual(original.fingerprint(), changed.fingerprint())
        self.assertEqual(getattr(original, change_key), self.values[change_key])

    def test_conflicting_identity_is_rejected(self) -> None:
        first = MarketRelease(**self.values)
        changed_values = dict(self.values)
        change_key = next(key for key in self.values if key != "release_code")
        changed_values[change_key] = 2 if isinstance(changed_values[change_key], int) else "conflict"
        second = MarketRelease(**changed_values)
        with self.assertRaises(ValueError):
            unique_by_identity([first, second])


if __name__ == "__main__":
    unittest.main()
