from datetime import time, timedelta
from decimal import Decimal
import unittest

from pe14_etl.io import canonical_key, detect_header, parse_period, period_from_filename
from pe14_etl.normalize import clean_int, parse_total_hours


class ParserUnitTests(unittest.TestCase):
    def test_integer_parsing_and_localized_decimal(self):
        self.assertEqual(clean_int("1.234").value, 1234)
        self.assertEqual(clean_int("2,5").value, 3)
        self.assertEqual(clean_int("texto").value, 0)
        self.assertIsNotNone(clean_int("texto").issue)

    def test_total_hours_supported_representations(self):
        self.assertEqual(parse_total_hours("144,5").value, Decimal("144.50"))
        self.assertEqual(parse_total_hours("01:30:00").value, Decimal("1.50"))
        self.assertEqual(parse_total_hours(time(2, 15)).value, Decimal("2.25"))
        self.assertEqual(parse_total_hours(timedelta(hours=3, minutes=30)).value, Decimal("3.50"))
        self.assertEqual(parse_total_hours(Decimal("1.5"), numeric_mode="days").value, Decimal("36.00"))

    def test_invalid_and_negative_hours_are_safe_and_reviewable(self):
        for value in ("dato-invalido", -5, "-01:00", None):
            parsed = parse_total_hours(value)
            self.assertGreaterEqual(parsed.value, Decimal("0"))
            self.assertTrue(parsed.review)

    def test_header_and_period_detection(self):
        self.assertEqual(canonical_key("  Identificador\n Ficha "), "IDENTIFICADOR_FICHA")
        self.assertEqual(detect_header([["PE-14"], ["202605"], ["IDENTIFICADOR_FICHA"]]), 2)
        self.assertEqual(parse_period("2026-05"), 202605)
        self.assertEqual(period_from_filename("PE-14_OCT CORTE 21-10-2026.csv"), 202610)


if __name__ == "__main__":
    unittest.main()
