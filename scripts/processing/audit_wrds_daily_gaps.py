#!/usr/bin/env python3
"""Re-query WRDS to distinguish source/universe gaps from missing downloads."""
import argparse
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/download"))
import wrds_daily_pull as raw


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, default=raw.DEFAULT_OUT)
    p.add_argument("--start", default="2022-01-03")
    p.add_argument("--end", default=__import__("datetime").date.today().isoformat())
    p.add_argument("--user", default=None)
    p.add_argument("--host", default="wrds-pgdata.wharton.upenn.edu")
    p.add_argument("--port", type=int, default=9737)
    p.add_argument("--connect-timeout", type=int, default=60)
    args = p.parse_args()
    cov = pd.read_csv(args.input / "security_date_coverage.csv", dtype={"security_id": str})
    subjects = cov[cov.internal_missing_sessions.gt(0)]
    ids = set(subjects.security_id)
    observed = set()
    for first, last in raw.months(args.start, args.end):
        path = args.input / "months" / f"{first:%Y%m}.csv.gz"
        d = pd.read_csv(path, usecols=["gvkey", "iid", "datadate"], dtype={"gvkey": str, "iid": str})
        d["security_id"] = d.gvkey + ":" + d.iid
        d = d[d.security_id.isin(ids)]
        observed.update(zip(d.security_id, d.datadate))
    calendar = sorted(raw.sessions(args.start, args.end))
    gaps = []
    for r in subjects.itertuples():
        gaps.extend((r.security_id, day) for day in calendar
                    if r.min <= day <= r.max and (r.security_id, day) not in observed)
    if len(gaps) != int(subjects.internal_missing_sessions.sum()):
        raise ValueError("Reconstructed gap count differs from original audit")
    missing = pd.DataFrame(gaps, columns=["security_id", "date"])
    if not gaps:
        raw.save_json(args.input / "gap_diagnostics.json", dict(internal_gaps=0, unexpected_download_gaps=0))
        return
    # Date-effective exchange history is queried again without NYSE/Nasdaq or
    # currency filters, so legitimate exclusions can be distinguished from bugs.
    pairs = tuple(tuple(x.split(":")) for x in sorted(ids))
    conn = raw.connect(args)
    try:
        data = raw.query(conn, """
            SELECT d.gvkey, d.iid, d.datadate, d.curcdd, e.itemvalue AS historical_exchg
            FROM comp_na_daily_all.secd d
            LEFT JOIN comp_na_daily_all.sec_idhist e
              ON d.gvkey=e.gvkey AND d.iid=e.iid AND e.item='EXCHG'
             AND d.datadate>=e.efffrom AND (e.effthru IS NULL OR d.datadate<=e.effthru)
            WHERE (d.gvkey,d.iid) IN %s AND d.datadate BETWEEN %s AND %s
        """, (pairs, args.start, args.end))
    finally:
        conn.close()
    data["security_id"] = data.gvkey + ":" + data.iid
    data["date"] = data.datadate.astype(str)
    missing = missing.merge(data[["security_id", "date", "curcdd", "historical_exchg"]],
                            on=["security_id", "date"], how="left", indicator=True)
    if missing.duplicated(["security_id", "date"]).any():
        raise ValueError("Overlapping source exchange history; review before classifying gaps")
    missing["reason"] = "outside_nyse_nasdaq_at_date"
    missing.loc[missing.historical_exchg.isna(), "reason"] = "missing_exchange_history"
    missing.loc[missing.curcdd.ne("USD"), "reason"] = "outside_usd_scope_or_missing_currency"
    unexpected = missing.curcdd.eq("USD") & missing.historical_exchg.isin(["11", "14"])
    missing.loc[unexpected, "reason"] = "unexpected_download_gap"
    missing.loc[missing._merge.eq("left_only"), "reason"] = "no_daily_row_in_wrds_source"
    missing.drop(columns="_merge").to_csv(args.input / "internal_gap_diagnostics.csv.gz", index=False)
    report = dict(internal_gaps=len(gaps), securities=len(ids),
                  reasons={k: int(v) for k, v in missing.reason.value_counts().items()},
                  unexpected_download_gaps=int(unexpected.sum()),
                  source="WRDS re-query of secd and effective-dated EXCHG history")
    raw.save_json(args.input / "gap_diagnostics.json", report)
    raw.log(report)
    if unexpected.any():
        raise SystemExit("Unexpected missing downloads found; completeness requires investigation")


if __name__ == "__main__":
    main()
