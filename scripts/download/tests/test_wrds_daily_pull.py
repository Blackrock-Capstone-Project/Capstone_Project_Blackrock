import importlib.util
from pathlib import Path
import tempfile
import unittest

import pandas as pd

spec = importlib.util.spec_from_file_location("daily", Path(__file__).parents[1] / "wrds_daily_pull.py")
daily = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily)


class DownloadIntegrityTests(unittest.TestCase):
    def test_months_include_partial_endpoints_and_leap_day(self):
        self.assertEqual([(str(a), str(b)) for a, b in daily.months("2024-02-28", "2024-03-01")],
                         [("2024-02-28", "2024-02-29"), ("2024-03-01", "2024-03-01")])

    def test_stale_source_is_rejected(self):
        stale = dict(table="crsp.dsf_v2", accessible=True, missing_required_columns=[],
                     first_date="1925-12-31", last_date="2025-12-31")
        with self.assertRaises(RuntimeError):
            daily.select_source([stale], "2022-01-03", "2026-10-16")

    def test_resume_detects_corruption_and_changed_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "202201.csv.gz"
            path.write_bytes(b"complete partition")
            request = dict(start="2022-01-03", end="2022-01-31")
            daily.save_json(path.with_suffix(".gz.json"), dict(request=request, sha256=daily.digest(path)))
            self.assertTrue(daily.partition_valid(path, request))
            self.assertFalse(daily.partition_valid(path, dict(start="2022-01-04", end="2022-01-31")))
            path.write_bytes(b"truncated")
            self.assertFalse(daily.partition_valid(path, request))

    def test_compustat_security_date_validation(self):
        df = pd.DataFrame({c: [1] for c in daily.COMP_REQUIRED})
        df["gvkey"], df["iid"] = "001690", "01"
        df["datadate"], df["historical_exchg"] = "2022-01-03", "14"
        daily.validate_frame(df, "2022-01-03", "2022-01-31")
        with self.assertRaises(ValueError):
            daily.validate_frame(pd.concat([df, df]), "2022-01-03", "2022-01-31")
        with self.assertRaises(ValueError):
            daily.validate_frame(df, "2022-01-04", "2022-01-31")

    def test_actual_exchange_calendar(self):
        sessions = daily.sessions("2022-01-03", "2026-10-16")
        self.assertEqual(len(sessions), 1202)
        self.assertNotIn("2025-01-09", sessions)  # Carter national day of mourning
        self.assertIn("2026-10-12", sessions)     # equity market open on Columbus Day

    def test_preferred_symbols_do_not_collide_with_common_tickers(self):
        symbols = pd.Series(["T.PC", "TPC", "BRK.B", "JPM.PL"])
        types = pd.Series(["1", "0", "0", "1"])
        self.assertEqual(daily.compustat_symbols(symbols, types).tolist(),
                         ["T/PRC", "TPC", "BRK/B", "JPM/PRL"])


if __name__ == "__main__":
    unittest.main()
