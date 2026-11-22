import importlib.util
from pathlib import Path
import unittest
import json
import subprocess
import sys
import tempfile

import pandas as pd

spec = importlib.util.spec_from_file_location(
    "processing", Path(__file__).parents[2] / "processing/process_wrds_daily_data.py")
processing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(processing)


class ShareBasisTests(unittest.TestCase):
    def test_adr_and_ordinary_share_bases(self):
        d = pd.DataFrame({"cshoc": [8000., 100., 500., 200.],
                          "tpci": ["F", "0", "F", "S"],
                          "adrrc": [8., float("nan"), float("nan"), 2.]})
        shares = processing.listed_shares(d)
        self.assertEqual(shares.iloc[0], 1000.)
        self.assertEqual(shares.iloc[1], 100.)
        self.assertTrue(pd.isna(shares.iloc[2]))
        self.assertEqual(shares.iloc[3], 100.)

    def test_standalone_processing_preserves_na_ticker_and_adr_basis(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "raw", Path(tmp) / "processed"
            (source / "months").mkdir(parents=True)
            d = pd.DataFrame({c: [1., 1.] for c in processing.raw.COMP_REQUIRED})
            d["gvkey"], d["iid"] = ["000001", "000002"], "01"
            d["datadate"] = "2026-10-16"
            d["tic"], d["historical_tic"] = ["NA", "ADR"], ["NA", "ADR"]
            d["curcdd"], d["historical_exchg"] = "USD", "14"
            d["tpci"], d["adrrc"] = ["0", "F"], [float("nan"), 5.]
            d["prcstd"], d["prccd"], d["cshoc"] = [3, 4], 10., 500.
            path = source / "months/202610.csv.gz"
            d.to_csv(path, index=False)
            processing.raw.save_json(path.with_suffix(".gz.json"), dict(
                request=dict(table="comp_na_daily_all.secd", start="2026-10-16", end="2026-10-16"),
                rows=2, sha256=processing.raw.digest(path)))
            result = subprocess.run([sys.executable, str(Path(processing.__file__)),
                                     "--input", str(source), "--output", str(output),
                                     "--start", "2026-10-16", "--end", "2026-10-16"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            clean = pd.read_csv(output / "daily_clean.csv.gz", keep_default_na=False)
            self.assertEqual(clean.ticker.tolist(), ["NA", "ADR"])
            self.assertEqual(clean.CUR_MKT_CAP.tolist(), [5000., 1000.])
            self.assertEqual(clean.PX_HIGH.iloc[1], "")
            self.assertEqual(clean.closing_ask.iloc[1], "1.0")
            report = json.loads((output / "quality_summary.json").read_text())
            self.assertEqual(report["observed_sessions"], 1)
            self.assertEqual(report["validated_output_rows"], 2)


if __name__ == "__main__":
    unittest.main()
