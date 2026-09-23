"""Campaign drafts expose checked tariff facts, not model claims or customer traits."""

from decimal import Decimal
import json
import unittest

from campaign_creatives import CHANNELS, generate_creatives


def context(**changes):
    facts = {
        "current_price": 3140.0,
        "target_price": 4000.25,
        "current_data_gb": 2.0,
        "target_data_gb": 12.5,
    }
    facts.update(changes)
    return facts


class CampaignCreativesTest(unittest.TestCase):
    def test_all_channels_are_json_safe_deterministic_review_only(self):
        for channel in CHANNELS:
            with self.subTest(channel=channel):
                result = generate_creatives("tariff_10", channel, context())
                self.assertEqual(result, generate_creatives("tariff_10", channel, context()))
                self.assertEqual(result["generator"], "templates-v1")
                self.assertTrue(result["requires_review"])
                self.assertEqual(result["channel"], channel)
                self.assertEqual(len(result["variants"]), 3)
                self.assertEqual(len({row["id"] for row in result["variants"]}), 3)
                self.assertIn("симуляции", " ".join(result["warnings"]))
                json.dumps(result, ensure_ascii=False, allow_nan=False)
                for row in result["variants"]:
                    self.assertEqual(set(row), {"id", "label", "headline", "body", "copy_text"})
                    self.assertIn("tariff_10", row["copy_text"])
                    self.assertIn("4 000,25 у.е./мес.", row["copy_text"])
                    self.assertIn("12,5 ГБ", row["copy_text"])
                    self.assertNotIn("..", row["copy_text"])

    def test_no_customer_or_model_values_in_copy(self):
        extra = context(arpu=999998, filter_arpu_segment="HIGH", lift=0.184,
                        profit=444444, customer_id="PRIVATE_USER", segment="INTERNAL_ONLY")
        for channel in CHANNELS:
            result = generate_creatives("tariff_10", channel, extra)
            self.assertEqual(result, generate_creatives("tariff_10", channel, context()))
            text = " ".join(row["copy_text"] for row in result["variants"]).lower()
            for forbidden in ("arpu", "high", "private_user", "internal_only", "18,4", "444444",
                              "скидк", "безлимит", "скорост", "гарант", "конверси", "прибыл"):
                self.assertNotIn(forbidden, text)

    def test_price_and_data_changes_never_turn_into_invented_benefits(self):
        for price in (0, 100, 3140, 8000):
            for data in (0, 0.25, 2, 100):
                with self.subTest(price=price, data=data):
                    facts = context(target_price=price, target_data_gb=data)
                    result = generate_creatives("tariff_10", "sms", facts)
                    self.assertEqual(len(result["variants"]), 3)
                    copy = " ".join(row["copy_text"] for row in result["variants"]).lower()
                    self.assertNotIn("..", copy)
                    for forbidden in ("сэконом", "дешев", "выгод", "больше", "лучше", "бесплат"):
                        self.assertNotIn(forbidden, copy)
                    if data == 0:
                        self.assertNotIn("гб", copy)
                    if price == 0:
                        self.assertIn("0,00 у.е./мес.", copy)

    def test_zero_current_facts_are_valid(self):
        result = generate_creatives("tariff_10", "push", context(current_price=0, current_data_gb=0))
        self.assertEqual(len(result["variants"]), 3)

    def test_prices_and_small_packages_do_not_round_away(self):
        cases = (
            (0.25, 0.25, "0,25 у.е./мес.", "0,25 ГБ"),
            (Decimal("1234.567"), 1 / 1024, "1 234,567 у.е./мес.", "0,0009765625 ГБ"),
            (Decimal("0.0001"), Decimal("0.00001"), "0,0001 у.е./мес.", "0,00001 ГБ"),
            (100, 2, "100,00 у.е./мес.", "2 ГБ"),
        )
        for price, data, price_text, data_text in cases:
            with self.subTest(price=price, data=data):
                result = generate_creatives("tariff_10", "sms", context(target_price=price, target_data_gb=data))
                for row in result["variants"]:
                    self.assertIn(price_text, row["copy_text"])
                    self.assertIn(data_text, row["copy_text"])

    def test_missing_negative_nonfinite_or_non_numeric_facts_fail_closed(self):
        invalid_values = (None, -1, float("nan"), float("inf"), -float("inf"),
                          Decimal("NaN"), Decimal("Infinity"), "100", True, [], {})
        for field in context():
            for invalid in invalid_values:
                with self.subTest(field=field, invalid=invalid):
                    result = generate_creatives("tariff_10", "sms", context(**{field: invalid}))
                    self.assertEqual(result["variants"], [])
                    self.assertIn(field, " ".join(result["warnings"]))
                    json.dumps(result, allow_nan=False)
            facts = context()
            del facts[field]
            self.assertEqual(generate_creatives("tariff_10", "push", facts)["variants"], [])

    def test_invalid_context_target_or_channel_does_not_crash(self):
        for bad_context in (None, [], "bad", 10):
            self.assertEqual(generate_creatives("tariff_10", "sms", bad_context)["variants"], [])
        for target in (None, "", " \n\t", 123):
            self.assertEqual(generate_creatives(target, "sms", context())["variants"], [])
        for channel in (None, "email", [], 123):
            result = generate_creatives("tariff_10", channel, context())
            self.assertEqual(result["variants"], [])
            json.dumps(result, allow_nan=False)

    def test_long_target_is_literal_single_line_not_truncated_or_interpreted(self):
        target = "tariff_" + "A" * 300 + "\r\n <b>literal</b>\t suffix"
        result = generate_creatives(target, "sms", context())
        expected = " ".join(target.split())
        for row in result["variants"]:
            self.assertIn(expected, row["copy_text"])
            self.assertNotIn("\n", row["copy_text"])
            self.assertNotIn("\r", row["copy_text"])
            self.assertNotIn("\t", row["copy_text"])
            self.assertGreater(len(row["copy_text"]), 300)

    def test_call_has_bounded_four_step_script_only_when_selected(self):
        for row in generate_creatives("tariff_10", "call", context())["variants"]:
            self.assertEqual(len(row["body"].splitlines()), 4)
        for channel in ("sms", "push", "digital_ads"):
            for row in generate_creatives("tariff_10", channel, context())["variants"]:
                self.assertNotIn("1. ", row["body"])


if __name__ == "__main__":
    unittest.main()
