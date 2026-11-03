#!/usr/bin/env python3
"""Partition existing raw Bloomberg and TAQ files into current-market folders.

This script copies source data without applying cleaning, adjustments, feature
engineering, or date filters. Current US market membership is determined from
the Bloomberg universe ticker suffix and matching static EXCH_CODE:
UN -> NYSE; UW/UQ/UR -> NASDAQ. LSE requires both ticker suffix and static
EXCH_CODE to be LN. TAQ rows are assigned by matching sym_root/sym_suffix to
the Bloomberg current-market ticker symbols; listing_ex is not used as a
listing-market classifier.

Run from the repository root:
    python data_processing/partition_raw_markets.py

The original Data/raw/bloomberg and Data/raw/taq files are read only. The
output is Data/raw/exchanges. Use --force to replace an existing output folder.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_ROOT = Path("Data/raw")
DEFAULT_OUTPUT_ROOT = Path("Data/raw/exchanges")
US_SUFFIX_TO_MARKET = {"UN": "NYSE", "UW": "NASDAQ", "UQ": "NASDAQ", "UR": "NASDAQ"}
US_MARKETS = ("NYSE", "NASDAQ")
ALL_MARKETS = ("NYSE", "NASDAQ", "LSE")


def resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def ticker_file_key(ticker: str) -> str:
    """Match the project's existing Bloomberg filename convention."""
    return ticker.strip().replace(" ", "_").replace("/", "-") + ".csv"


def ticker_suffix(ticker: str) -> str:
    parts = ticker.strip().split()
    return parts[-2] if len(parts) >= 2 else ""


def load_market_assignments(input_root: Path):
    bbg = input_root / "bloomberg"
    us_root, uk_root = bbg / "us", bbg / "uk"
    required = [us_root, uk_root]
    missing = [str(p) for p in required if not p.is_dir()]
    if missing:
        raise FileNotFoundError("Missing Bloomberg input folder(s): " + ", ".join(missing))

    us_universe_files = sorted(us_root.glob("universe*.csv"))
    us_static_files = sorted(us_root.glob("static*.csv"))
    uk_universe_files = sorted(uk_root.glob("universe*.csv"))
    uk_static_files = sorted(uk_root.glob("static*.csv"))
    if not all((us_universe_files, us_static_files, uk_universe_files, uk_static_files)):
        raise FileNotFoundError("Expected Bloomberg universe*.csv and static*.csv files for US and UK.")

    def load_metadata(universe_files, static_files, market_label):
        universe = pd.concat((pd.read_csv(p, dtype=str).fillna("") for p in universe_files), ignore_index=True)
        static = pd.concat((pd.read_csv(p, dtype=str).fillna("") for p in static_files), ignore_index=True)
        if not {"market", "ticker"}.issubset(universe.columns):
            raise ValueError(f"Universe file for {market_label} must contain market and ticker columns.")
        if not {"ticker", "EXCH_CODE"}.issubset(static.columns):
            raise ValueError(f"Static file for {market_label} must contain ticker and EXCH_CODE columns.")
        static = static.drop_duplicates("ticker", keep="last")
        universe = universe.loc[universe["market"] == market_label, ["ticker"]].drop_duplicates()
        return universe.merge(static[["ticker", "EXCH_CODE"]], on="ticker", how="left")

    us = load_metadata(us_universe_files, us_static_files, "US")
    uk = load_metadata(uk_universe_files, uk_static_files, "UK")
    assignments: dict[str, str] = {}
    assignment_records = []

    for row in us.itertuples(index=False):
        ticker = str(row.ticker).strip()
        suffix = ticker_suffix(ticker)
        market = US_SUFFIX_TO_MARKET.get(suffix)
        static_code = str(row.EXCH_CODE).strip()
        if re.match(r"^\d+D ", ticker):
            reason = "delisted_placeholder"
            market = None
        elif market is None:
            reason = "suffix_outside_nyse_nasdaq"
        elif static_code != suffix:
            reason = "suffix_static_code_mismatch_or_missing"
            market = None
        else:
            reason = "assigned"
        assignment_records.append({"ticker": ticker, "market": market or "", "suffix": suffix,
                                  "EXCH_CODE": static_code, "status": reason})
        if market:
            assignments[ticker] = market

    for row in uk.itertuples(index=False):
        ticker = str(row.ticker).strip()
        suffix = ticker_suffix(ticker)
        static_code = str(row.EXCH_CODE).strip()
        if suffix == "LN" and static_code == "LN":
            assignments[ticker] = "LSE"
            reason = "assigned"
            market = "LSE"
        else:
            reason = "suffix_static_code_mismatch_or_not_lse"
            market = ""
        assignment_records.append({"ticker": ticker, "market": market, "suffix": suffix,
                                  "EXCH_CODE": static_code, "status": reason})

    key_to_ticker: dict[str, str] = {}
    symbol_to_market: dict[str, str] = {}
    for ticker, market in assignments.items():
        key = ticker_file_key(ticker)
        if key in key_to_ticker and key_to_ticker[key] != ticker:
            raise ValueError(f"Bloomberg filenames collide: {key_to_ticker[key]} and {ticker} -> {key}")
        key_to_ticker[key] = ticker
        if market in US_MARKETS:
            symbol = ticker.split()[0]
            prior = symbol_to_market.get(symbol)
            if prior and prior != market:
                raise ValueError(f"Bloomberg symbol maps to multiple current markets: {symbol}: {prior}, {market}")
            symbol_to_market[symbol] = market

    return assignments, key_to_ticker, symbol_to_market, pd.DataFrame(assignment_records)


def copy_bloomberg_tree(source_dir: Path, stage_root: Path, output_root: Path, market: str,
                        dataset: str, market_file_keys: set[str], manifest: list[dict]) -> Counter:
    copied = Counter()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Missing Bloomberg input directory: {source_dir}")
    for source in sorted(source_dir.glob("*.csv")):
        if source.name not in market_file_keys:
            continue
        relative = Path(market) / "Bloomberg" / dataset / source.name
        target = stage_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied[market] += 1
        manifest.append({"market": market, "dataset": f"Bloomberg/{dataset}",
                         "source": source.relative_to(PROJECT_ROOT).as_posix(),
                         "output": (output_root / relative).relative_to(PROJECT_ROOT).as_posix(),
                         "rows": "", "bytes": source.stat().st_size})
    return copied


def taq_row_market(frame: pd.DataFrame, symbol_to_market: dict[str, str]) -> pd.Series:
    if not {"sym_root", "sym_suffix"}.issubset(frame.columns):
        raise ValueError("TAQ day file must contain sym_root and sym_suffix columns.")
    root = frame["sym_root"].fillna("").astype(str).str.strip()
    suffix = frame["sym_suffix"].fillna("").astype(str).str.strip()
    taq_symbol = root.where(suffix.eq(""), root + "/" + suffix)
    market = taq_symbol.map(symbol_to_market)
    # Bloomberg sometimes writes a class ticker joined (e.g. CMCSA) while TAQ
    # represents it as root/suffix (e.g. CMCS/A). Exact matches take priority.
    fallback = market.isna() & suffix.str.len().eq(1)
    market.loc[fallback] = (root.loc[fallback] + suffix.loc[fallback]).map(symbol_to_market)
    return market


def split_taq_days(input_root: Path, stage_root: Path, output_root: Path,
                   symbol_to_market: dict[str, str], manifest: list[dict],
                   chunk_size: int) -> tuple[Counter, Counter]:
    source_dir = input_root / "taq" / "days"
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Missing TAQ input directory: {source_dir}")
    day_paths = sorted(source_dir.glob("*.csv.gz"))
    if not day_paths:
        raise FileNotFoundError(f"No TAQ .csv.gz day files found under {source_dir}")

    output_rows, unmatched_rows = Counter(), Counter()
    for index, source in enumerate(day_paths, start=1):
        output_paths = {
            market: stage_root / market / "WRDS_TAQ" / "days" / source.name
            for market in US_MARKETS
        }
        for path in output_paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)

        wrote_header = {market: False for market in US_MARKETS}
        day_rows = Counter()
        with gzip.open(output_paths["NYSE"], "wt", newline="", encoding="utf-8") as nyse_out, \
             gzip.open(output_paths["NASDAQ"], "wt", newline="", encoding="utf-8") as nasdaq_out:
            outputs = {"NYSE": nyse_out, "NASDAQ": nasdaq_out}
            for chunk in pd.read_csv(source, compression="gzip", dtype=str, keep_default_na=False,
                                     chunksize=chunk_size):
                row_markets = taq_row_market(chunk, symbol_to_market)
                unmatched_rows[source.name] += int(row_markets.isna().sum())
                for market in US_MARKETS:
                    selected = chunk.loc[row_markets.eq(market)]
                    if selected.empty:
                        continue
                    selected.to_csv(outputs[market], index=False, header=not wrote_header[market],
                                    lineterminator="\n")
                    wrote_header[market] = True
                    day_rows[market] += len(selected)
            # Preserve the schema even if one market has no rows on a date.
            if not wrote_header["NYSE"] or not wrote_header["NASDAQ"]:
                header = pd.read_csv(source, compression="gzip", dtype=str, keep_default_na=False, nrows=0)
                for market in US_MARKETS:
                    if not wrote_header[market]:
                        header.to_csv(outputs[market], index=False, header=True, lineterminator="\n")

        for market in US_MARKETS:
            output_rows[market] += day_rows[market]
            target = output_paths[market]
            manifest.append({"market": market, "dataset": "WRDS_TAQ/days",
                             "source": source.relative_to(PROJECT_ROOT).as_posix(),
                             "output": (output_root / target.relative_to(stage_root)).relative_to(PROJECT_ROOT).as_posix(),
                             "rows": day_rows[market], "bytes": target.stat().st_size})
        if index % 100 == 0 or index == len(day_paths):
            print(f"TAQ days: {index}/{len(day_paths)}")

    return output_rows, unmatched_rows


def write_manifest(stage_root: Path, output_root: Path, manifest: list[dict],
                   assignments: pd.DataFrame, summary: pd.DataFrame) -> None:
    pd.DataFrame(manifest).to_csv(stage_root / "split_manifest.csv", index=False)
    assignments.to_csv(stage_root / "market_assignments.csv", index=False)
    summary.to_csv(stage_root / "split_summary.csv", index=False)
    readme = """# Raw data partitioned by current exchange\n\n"""
    readme += "This folder is generated by `scripts/partition_raw_markets.py` from the untouched source folders under `Data/raw/bloomberg` and `Data/raw/taq`.\n\n"
    readme += "US assignment uses the current Bloomberg universe ticker suffix and matching static `EXCH_CODE`: `UN` = NYSE; `UW/UQ/UR` = Nasdaq. Numeric delisted placeholders are excluded. LSE requires suffix `LN` and static `EXCH_CODE=LN`. TAQ rows are assigned by matching `sym_root/sym_suffix` to those current Bloomberg symbols; `listing_ex` is not used as a listing-exchange classifier. This is a current-exchange cohort split, not historical point-in-time exchange membership.\n\n"
    readme += "Bloomberg source files are copied unchanged. TAQ day files are filtered by symbol and rewritten with the same columns and cell values. Source files remain untouched; unmatched TAQ rows stay in `Data/raw/taq/days` and are counted in `split_summary.csv`.\n\n"
    output_arg = output_root.relative_to(PROJECT_ROOT).as_posix()
    readme += f"Regenerate from the repository root with `python scripts/partition_raw_markets.py --output-root {output_arg} --force`. All default paths are relative to the repository root.\n"
    (stage_root / "README.md").write_text(readme, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", default=str(DEFAULT_INPUT_ROOT), help="raw input root, relative to repository root")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="market split output, relative to repository root")
    parser.add_argument("--force", action="store_true", help="replace existing output after a successful rebuild")
    parser.add_argument("--chunk-size", type=int, default=500_000, help="TAQ rows per input chunk")
    args = parser.parse_args()
    if args.chunk_size <= 0:
        parser.error("--chunk-size must be positive")

    input_root = resolve_project_path(args.input_root)
    output_root = resolve_project_path(args.output_root)
    protected_inputs = (input_root / "bloomberg", input_root / "taq")
    if (output_root == input_root or output_root in input_root.parents
            or any(source == output_root or source in output_root.parents for source in protected_inputs)):
        parser.error("Output cannot replace Data/raw or overlap its Bloomberg/TAQ source folders.")
    if output_root.exists() and not args.force:
        parser.error(f"Output already exists: {output_root.relative_to(PROJECT_ROOT)}. Use --force to replace it.")
    output_root.parent.mkdir(parents=True, exist_ok=True)

    assignments, key_to_ticker, symbol_to_market, assignment_df = load_market_assignments(input_root)
    stage_root = Path(tempfile.mkdtemp(prefix=f".{output_root.name}.build-", dir=output_root.parent))
    manifest: list[dict] = []
    try:
        market_files = {
            market: {key for key, ticker in key_to_ticker.items() if assignments[ticker] == market}
            for market in ALL_MARKETS
        }
        source_us_daily = input_root / "bloomberg" / "us" / "daily"
        source_us_checkpoints = input_root / "bloomberg" / "us" / "checkpoints"
        source_uk = input_root / "bloomberg" / "uk"
        for market in US_MARKETS:
            copy_bloomberg_tree(source_us_daily, stage_root, output_root, market, "daily", market_files[market], manifest)
            copy_bloomberg_tree(source_us_checkpoints, stage_root, output_root, market, "checkpoints", market_files[market], manifest)
        for dataset in ("daily", "checkpoints", "close_ticks"):
            if dataset == "close_ticks" and not (source_uk / dataset).is_dir():
                continue
            copy_bloomberg_tree(source_uk / dataset, stage_root, output_root, "LSE", dataset, market_files["LSE"], manifest)

        taq_rows, unmatched_by_day = split_taq_days(input_root, stage_root, output_root,
                                                    symbol_to_market, manifest, args.chunk_size)
        unmatched_total = sum(unmatched_by_day.values())
        file_counts = Counter((row["market"], row["dataset"]) for row in manifest)
        summary_rows = []
        for market in ALL_MARKETS:
            summary_rows.append({
                "market": market,
                "current_bloomberg_tickers": sum(value == market for value in assignments.values()),
                "daily_files": file_counts[(market, "Bloomberg/daily")],
                "checkpoint_files": file_counts[(market, "Bloomberg/checkpoints")],
                "close_tick_files": file_counts[(market, "Bloomberg/close_ticks")],
                "taq_day_files": file_counts[(market, "WRDS_TAQ/days")],
                "taq_rows": taq_rows.get(market, 0),
                "unmatched_taq_rows_in_source": "",
            })
        summary_rows.append({"market": "UNASSIGNED_TAQ", "current_bloomberg_tickers": "",
                             "daily_files": "", "checkpoint_files": "", "close_tick_files": "",
                             "taq_day_files": "", "taq_rows": unmatched_total,
                             "unmatched_taq_rows_in_source": unmatched_total})
        summary = pd.DataFrame(summary_rows)
        write_manifest(stage_root, output_root, manifest, assignment_df, summary)

        if output_root.exists():
            shutil.rmtree(output_root)
        os.replace(stage_root, output_root)
    except Exception:
        if stage_root.exists():
            shutil.rmtree(stage_root)
        raise

    print(f"Created {output_root.relative_to(PROJECT_ROOT)}")
    print(summary.to_string(index=False))
    print("Original Bloomberg and TAQ source folders were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
