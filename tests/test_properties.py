import random
import unittest
from decimal import Decimal

from pe14_etl.normalize import clean_int, parse_total_hours


class PropertyTests(unittest.TestCase):
    def test_nonnegative_integer_inputs_are_preserved(self):
        rng = random.Random(1405)
        for _ in range(1000):
            value = rng.randrange(0, 10**9)
            parsed = clean_int(value)
            self.assertEqual(parsed.value, value)
            self.assertIsNone(parsed.issue)

    def test_arbitrary_hour_inputs_never_emit_negative_values(self):
        rng = random.Random(1410)
        values = [rng.uniform(-100000, 100000) for _ in range(1000)]
        values.extend(["x", "99:99:99", "12,45", None, Decimal("NaN")])
        for value in values:
            parsed = parse_total_hours(value)
            self.assertGreaterEqual(parsed.value, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
