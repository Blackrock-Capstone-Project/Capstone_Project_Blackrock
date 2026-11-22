#!/usr/bin/env python3
"""Build a standalone US daily table from WRDS Compustat monthly partitions.

This stage has no Bloomberg inputs. Prices/volume are as quoted, in USD/shares;
adjustment factors stay explicit. These are contemporaneous daily observations,
not lagged model features. No missing prices, shares or labels are imputed.
"""
import argparse
import gzip
import json
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/download"))
import wrds_daily_pull as raw


def listed_shares(d):
    """Compustat ADRRC is underlying ordinary shares per depositary receipt."""
    shares = d.cshoc.copy()
    adr = d.tpci.isin(["F", "S"])
    shares.loc[adr] = d.loc[adr, "cshoc"] / d.loc[adr, "adrrc"].where(d.loc[adr, "adrrc"] > 0)
    return shares


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, default=raw.DEFAULT_OUT)
    p.add_argument("--output", type=Path, default=ROOT / "Data/processed/us_wrds_daily")
    p.add_argument("--start", default="2022-01-03")
    p.add_argument("--end", help="Default: latest verified local daily partition end")
    args = p.parse_args()
    if args.end is None:
        metadata = sorted((args.input / 'months').glob('*.csv.gz.json'))
        if not metadata:
            p.error('No local daily metadata; run the unified update command first')
        args.end = max(json.loads(f.read_text())['request']['end'] for f in metadata)
    process_daily(args)


def process_daily(args):
    """Offline daily standardization for a caller-supplied configuration namespace."""
    expected = raw.sessions(args.start, args.end)
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "daily_clean.csv.gz"
    tmp = path.with_name(path.name + ".tmp")
    by_date, by_security, by_type, counts, price_flags = [], [], [], [], []
    observed, rows, first_write = set(), 0, True
    cols = sorted(raw.COMP_REQUIRED | {"historical_exchg", "historical_tic", "prcstd", "tpci", "adrrc"})
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as handle:
        for first, last in raw.months(args.start, args.end):
            source = args.input / "months" / f"{first:%Y%m}.csv.gz"
            metadata = json.loads(source.with_suffix(source.suffix + ".json").read_text())
            if not raw.is_comp(metadata["request"]["table"]) or not raw.partition_valid(source, metadata["request"]):
                raise ValueError(f"Unverified Compustat partition: {source}")
            d = pd.read_csv(source, usecols=cols, keep_default_na=False, na_values=[""], dtype={"gvkey": str, "iid": str, "cusip": str,
                                                        "historical_exchg": str, "historical_tic": str, "tpci": str})
            if len(d) != metadata["rows"]:
                raise ValueError(f"Raw row count mismatch: {source}")
            raw.validate_frame(d, metadata["request"]["start"], metadata["request"]["end"])
            d = d[d.datadate.between(str(first), str(last))].copy()
            observed.update(d.datadate)
            shares = listed_shares(d)
            price_flags.append(d.prcstd.value_counts(dropna=False))
            bid_ask = d.prcstd.eq(4)
            clean = pd.DataFrame({
                "security_id": d.gvkey + ":" + d.iid,
                "gvkey": d.gvkey, "iid": d.iid, "cusip": d.cusip,
                "date": d.datadate, "ticker": d.historical_tic,
                "market": d.historical_exchg.map({"11": "NYSE", "14": "NASDAQ"}),
                "currency": d.curcdd,
                "PX_OPEN": d.prcod,
                "PX_HIGH": d.prchd.mask(bid_ask), "PX_LOW": d.prcld.mask(bid_ask),
                "source_prchd": d.prchd, "source_prcld": d.prcld,
                "closing_ask": d.prchd.where(bid_ask), "closing_bid": d.prcld.where(bid_ask),
                "PX_LAST": d.prccd, "PX_VOLUME": d.cshtrd,
                "shares_outstanding": shares,
                "source_cshoc": d.cshoc,
                "adr_ratio": d.adrrc,
                "CUR_MKT_CAP": d.prccd * shares,
                "adjustment_factor": d.ajexdi,
                "price_standard_code": d.prcstd,
                "security_type_code": d.tpci,
                "daily_source": "wrds_compustat_na_secd",
                "volume_basis": "compustat_reported_shares",
                "price_basis": "as_quoted",
                # This is a price-times-volume proxy, not traded-dollar turnover.
                "close_times_volume_usd": d.prccd * d.cshtrd,
            })
            if clean.duplicated(["security_id", "date"]).any():
                raise ValueError(f"Duplicate output keys: {source}")
            clean.to_csv(handle, index=False, header=first_write)
            first_write = False
            for col in ["PX_OPEN", "PX_HIGH", "PX_LOW", "PX_LAST", "PX_VOLUME", "CUR_MKT_CAP", "ticker"]:
                clean["missing_" + col] = clean[col].isna().astype(int)
            missing = [c for c in clean if c.startswith("missing_")]
            by_date.append(clean.groupby(["date", "market"]).agg(
                rows=("security_id", "size"), **{c: (c, "sum") for c in missing}).reset_index())
            by_type.append(clean.groupby("security_type_code", dropna=False).agg(
                rows=("security_id", "size"), **{c: (c, "sum") for c in missing}))
            by_security.append(clean.groupby(["security_id", "gvkey", "iid", "market"]).agg(
                rows=("date", "size"), first_date=("date", "min"), last_date=("date", "max"),
                **{c: (c, "sum") for c in missing}).reset_index())
            counts.append(dict(file=source.name, rows=len(clean), sha256=metadata["sha256"]))
            rows += len(clean)
            raw.log(f"Processed {source.name}: {len(clean):,} rows")
    if observed != expected:
        raise ValueError(f"Session mismatch; missing={sorted(expected-observed)}, unexpected={sorted(observed-expected)}")
    dates = pd.concat(by_date, ignore_index=True)
    sec = pd.concat(by_security, ignore_index=True)
    sums = [c for c in sec if c.startswith("missing_")] + ["rows"]
    sec = sec.groupby(["security_id", "gvkey", "iid", "market"], as_index=False).agg(
        first_date=("first_date", "min"), last_date=("last_date", "max"),
        **{c: (c, "sum") for c in sums})
    # Independent disk verification before promoting the finished daily table.
    checked_rows, checked_dates = 0, set()
    for chunk in pd.read_csv(tmp, compression="gzip", usecols=["date", "security_id", "PX_LAST", "CUR_MKT_CAP"],
                             chunksize=500_000):
        checked_rows += len(chunk)
        checked_dates.update(chunk.date)
        if chunk.duplicated(["security_id", "date"]).any():
            raise ValueError("Duplicate serialized daily keys")
        for col in ["PX_LAST", "CUR_MKT_CAP"]:
            if not np.isfinite(chunk[col].dropna()).all():
                raise ValueError(f"Nonfinite output field: {col}")
    if checked_rows != rows or checked_dates != expected:
        raise ValueError("Serialized output differs from inputs")
    os.replace(tmp, path)
    dates.to_csv(args.output / "daily_date_quality.csv", index=False)
    sec.to_csv(args.output / "security_daily_quality.csv", index=False)
    types = pd.concat(by_type).groupby(level=0).sum()
    types.to_csv(args.output / "security_type_quality.csv")
    raw.save_json(args.output / "quality_summary.json", dict(
        start=args.start, end=args.end, rows=rows, expected_sessions=len(expected),
        observed_sessions=len(observed), missing_sessions=[], duplicate_keys=0,
        security_market_pairs=len(sec), securities=int(sec.security_id.nunique()),
        missing_fields={c: int(dates[c].sum()) for c in dates if c.startswith("missing_")},
        price_standard_codes={str(k): int(v) for k, v in pd.concat(price_flags).groupby(level=0).sum().items()},
        source_partitions=counts, validated_output_rows=checked_rows,
        policy={
            "source": "WRDS Compustat North America secd + sec_idhist",
            "universe": "historical EXCHG 11/14, daily currency USD; no current-universe filter",
            "identifiers": "GVKEY+IID; ticker and exchange joined within effective date intervals",
            "prices": "as quoted; AJEXDI retained; no Bloomberg adjustment applied",
            "bid_ask_policy": "PRCSTD 4 rows retain PRCCD with its source flag; PRCHD/PRCLD become closing_ask/bid, not daily high/low",
            "volume": "Compustat CSHTRD in shares; not substituted for TAQ session-volume definition",
            "market_cap": "PRCCD * CSHOC / ADRRC for ADRs (TPCI F/S), otherwise PRCCD * CSHOC; USD, security-level; missing ADR ratio stays missing",
            "turnover": "not supplied; close_times_volume_usd is explicitly a proxy",
            "missing_data": "preserved, not filled with zero or stale observations",
            "model_features": "contemporaneous observations; not lagged model features",
        }))
    raw.log(f"Verified {rows:,} rows across {len(observed)} sessions -> {path}")


if __name__ == "__main__":
    main()
