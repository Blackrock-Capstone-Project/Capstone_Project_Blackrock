"""Build the NYSE model panel and compare fixed and expanding-window forecasts.

Only the existing NYSE Bloomberg daily files, WRDS TAQ day files, and the
project rebalance calendar are used. Raw inputs are read-only. Outputs are
written under <project-dir>/Data/processed/nyse.
"""

from __future__ import annotations

import argparse
import csv
import gc
import gzip
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.cluster import KMeans
from sklearn.linear_model import Ridge
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
LOCAL_PROJECT_CANDIDATE = WORKSPACE_ROOT / "Eqiuty-Closing-Auction-2026-Yiying"
DEFAULT_PROJECT_DIR = LOCAL_PROJECT_CANDIDATE if LOCAL_PROJECT_CANDIDATE.is_dir() else WORKSPACE_ROOT
TAQ_CP = [
    "cumvol_10:00",
    "cumvol_12:00",
    "cumvol_14:00",
    "cumvol_15:00",
    "cumvol_15:30",
    "cumvol_15:50",
]
CP_TAGS = ["1000", "1200", "1400", "1500", "1530", "1550"]
TAQ_VENUE_FIELDS = ["xvol_n", "xvol_q", "xvol_p", "xvol_a", "xvol_z"]
BBG_FIELDS = [
    "PX_LAST",
    "PX_OPEN",
    "PX_HIGH",
    "PX_LOW",
    "PX_VOLUME",
    "TURNOVER",
    "CUR_MKT_CAP",
]
TARGETS = [("auction_volume", "auction")] + list(zip(TAQ_CP, CP_TAGS, strict=True))
EVENT_CALENDAR = "Data/reference/us_index_rebalance_calendar.csv"
PRICE_STALENESS_LIMIT = 5
FLOW_MEDIAN_WINDOW = 5
FLOW_MEDIAN_MIN_OBS = 3
MOMENTUM_5_MIN_OBS = 4
MOMENTUM_20_MIN_OBS = 15
VOLATILITY_20_MIN_OBS = 15
VOLATILITY_60_MIN_OBS = 45
KMEANS_PROFILE_FEATURES = [
    "hist_auction_log_mean_20",
    "hist_auction_log_std_20",
    "hist_1000_log_mean_20",
    "hist_1200_log_mean_20",
    "hist_1400_log_mean_20",
    "hist_1500_log_mean_20",
    "hist_1530_log_mean_20",
    "hist_1550_log_mean_20",
    "hist_auction_to_1000_ratio_median_20",
    "hist_auction_to_1550_ratio_median_20",
    "feat_market_cap_log",
    "feat_volatility_20d",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=DEFAULT_PROJECT_DIR)
    parser.add_argument("--alpha", type=float, default=1.0, help="Ridge regularization strength")
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    return parser.parse_args()


def audit_row(
    audit: list[dict[str, Any]],
    dataset: str,
    source_file: str,
    status: str,
    *,
    date: Any = "",
    ticker: str = "",
    rows: int = 0,
    detail: str = "",
    action: str = "",
) -> None:
    audit.append(
        {
            "dataset": dataset,
            "source_file": source_file,
            "date": date,
            "ticker": ticker,
            "status": status,
            "rows": int(rows),
            "detail": detail,
            "action": action,
        }
    )


def bbg_file_key(ticker: str) -> str:
    return ticker.strip().replace(" ", "_").replace("/", "-") + ".csv"


def load_nyse_universe(project_dir: Path, audit: list[dict[str, Any]]) -> tuple[list[str], dict[str, str], dict[str, str]]:
    path = project_dir / "Data/raw/exchanges/market_assignments.csv"
    assignments = pd.read_csv(path, dtype=str).fillna("")
    selected = assignments.loc[
        assignments["market"].eq("NYSE") & assignments["status"].eq("assigned"), "ticker"
    ].drop_duplicates()
    tickers = sorted(selected.astype(str).str.strip().tolist())

    exact: dict[str, str] = {}
    no_slash: dict[str, str] = {}
    ambiguous: set[str] = set()
    for ticker in tickers:
        symbol = ticker.split()[0]
        for mapping, key in ((exact, symbol), (no_slash, symbol.replace("/", ""))):
            prior = mapping.get(key)
            if prior is not None and prior != ticker:
                ambiguous.add(key)
            else:
                mapping[key] = ticker
    for key in ambiguous:
        exact.pop(key, None)
        no_slash.pop(key, None)
        audit_row(
            audit,
            "universe",
            path.name,
            "ambiguous_symbol_mapping",
            detail=f"Multiple Bloomberg securities map to symbol key {key!r}.",
            action="Exclude unmatched TAQ symbols; retain ambiguity in audit.",
        )
    if not tickers:
        raise ValueError(f"No NYSE tickers with assigned status found in {path}")
    return tickers, exact, no_slash


def _date_from_day_filename(path: Path) -> pd.Timestamp | None:
    match = re.fullmatch(r"(\d{8})\.csv\.gz", path.name)
    if not match:
        return None
    return pd.to_datetime(match.group(1), format="%Y%m%d", errors="coerce")


def load_taq_days(
    project_dir: Path,
    exact_symbol_map: dict[str, str],
    no_slash_symbol_map: dict[str, str],
    audit: list[dict[str, Any]],
) -> tuple[pd.DataFrame, set[pd.Timestamp], dict[pd.Timestamp, float], set[pd.Timestamp]]:
    folder = project_dir / "Data/raw/exchanges/NYSE/WRDS_TAQ/days"
    paths = sorted(folder.glob("*.csv.gz"))
    if not paths:
        raise FileNotFoundError(f"No NYSE TAQ daily files found in {folder}")

    frames: list[pd.DataFrame] = []
    file_dates: set[pd.Timestamp] = set()
    half_day: dict[pd.Timestamp, float] = {}
    invalid_file_dates: set[pd.Timestamp] = set()
    core = ["date", "sym_root", "sym_suffix"]
    keep = core + ["auction_volume", "auction_volume_all", "auction_price", "n_prints", "listing_ex"] + TAQ_CP + TAQ_VENUE_FIELDS

    for i, path in enumerate(paths, 1):
        file_date = _date_from_day_filename(path)
        if file_date is not None and not pd.isna(file_date):
            file_dates.add(file_date)
        if path.stat().st_size == 0:
            if file_date is not None and not pd.isna(file_date):
                invalid_file_dates.add(file_date)
            audit_row(audit, "WRDS_TAQ", path.name, "empty_file", date=file_date, action="Mark date as a data gap; do not refill or redownload.")
            continue
        try:
            with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
                header = handle.readline().strip()
            if not header:
                if file_date is not None and not pd.isna(file_date):
                    invalid_file_dates.add(file_date)
                audit_row(audit, "WRDS_TAQ", path.name, "empty_file", date=file_date, action="Mark date as a data gap; do not refill or redownload.")
                continue
            header_cols = next(csv.reader([header]))
            frame = pd.read_csv(
                path,
                dtype={"sym_root": "string", "sym_suffix": "string", "listing_ex": "string"},
                low_memory=False,
            )
        except Exception as exc:
            if file_date is not None and not pd.isna(file_date):
                invalid_file_dates.add(file_date)
            audit_row(audit, "WRDS_TAQ", path.name, "unreadable_file", date=file_date, detail=str(exc), action="Mark affected date as a data gap.")
            continue

        if frame.empty:
            if file_date is not None and not pd.isna(file_date):
                invalid_file_dates.add(file_date)
            audit_row(audit, "WRDS_TAQ", path.name, "header_only_file", date=file_date, detail="Column names exist, but there are no data rows.", action="Mark date as a data gap; do not refill or redownload.")
            continue
        missing_core = sorted(set(core).difference(frame.columns))
        if missing_core:
            if file_date is not None and not pd.isna(file_date):
                invalid_file_dates.add(file_date)
            audit_row(audit, "WRDS_TAQ", path.name, "missing_key_fields", date=file_date, detail=", ".join(missing_core), action="Exclude file rows that cannot be identified; mark date as a gap if all rows are affected.")
            continue

        available_cp = [col for col in TAQ_CP if col in header_cols]
        if available_cp == TAQ_CP[:2]:
            half_day[file_date] = 1.0
        elif available_cp == TAQ_CP:
            half_day[file_date] = 0.0
        else:
            half_day[file_date] = np.nan
            missing_cp = sorted(set(TAQ_CP).difference(available_cp))
            audit_row(audit, "WRDS_TAQ", path.name, "partial_checkpoint_columns", date=file_date, detail=", ".join(missing_cp), action="Keep available checkpoint values; leave missing fields blank.")

        for col in keep:
            if col not in frame.columns:
                frame[col] = pd.NA
        frame = frame[keep].copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        if file_date is not None and not pd.isna(file_date):
            mismatch = frame["date"].notna() & frame["date"].ne(file_date)
            if mismatch.any():
                audit_row(audit, "WRDS_TAQ", path.name, "row_date_mismatch", date=file_date, rows=int(mismatch.sum()), detail="Rows disagree with the date in the filename.", action="Exclude mismatched rows.")
                frame = frame.loc[~mismatch].copy()
        frame = frame.loc[frame["date"].notna()].copy()
        frame["sym_root"] = frame["sym_root"].fillna("").astype(str).str.strip()
        frame["sym_suffix"] = frame["sym_suffix"].fillna("").astype(str).str.strip()
        taq_symbol = frame["sym_root"].where(frame["sym_suffix"].eq(""), frame["sym_root"] + "/" + frame["sym_suffix"])
        frame["ticker"] = taq_symbol.map(exact_symbol_map)
        fallback = frame["ticker"].isna() & frame["sym_suffix"].str.len().eq(1)
        fallback_key = frame.loc[fallback, "sym_root"] + frame.loc[fallback, "sym_suffix"]
        frame.loc[fallback, "ticker"] = fallback_key.map(no_slash_symbol_map)
        unmatched = frame["ticker"].isna()
        if unmatched.any():
            audit_row(audit, "WRDS_TAQ", path.name, "unmatched_taq_symbols", date=file_date, rows=int(unmatched.sum()), detail="TAQ symbols did not map to an assigned NYSE Bloomberg security.", action="Exclude unmatched rows; preserve the count.")
            frame = frame.loc[~unmatched].copy()

        for col in ["auction_volume", "auction_volume_all", "auction_price", "n_prints", *TAQ_CP, *TAQ_VENUE_FIELDS]:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        before = len(frame)
        frame = frame.drop_duplicates()
        exact_removed = before - len(frame)
        if exact_removed:
            audit_row(audit, "WRDS_TAQ", path.name, "exact_duplicates_removed", date=file_date, rows=exact_removed, action="Keep one identical row per source record.")
        key_cols = ["ticker", "date"]
        conflict_mask = frame.duplicated(key_cols, keep=False)
        if conflict_mask.any():
            conflict_keys = frame.loc[conflict_mask, key_cols].drop_duplicates()
            audit_row(audit, "WRDS_TAQ", path.name, "conflicting_stock_date_duplicates", date=file_date, rows=len(conflict_keys), detail="Conflicting rows within the same WRDS TAQ day file.", action="Exclude the affected ticker-date; do not sum or average.")
            frame = frame.merge(conflict_keys.assign(_conflict=True), on=key_cols, how="left")
            frame = frame.loc[frame["_conflict"].isna()].drop(columns="_conflict")
        if len(frame):
            frames.append(frame)
        if not (exact_removed or conflict_mask.any() or unmatched.any()):
            audit_row(audit, "WRDS_TAQ", path.name, "ok", date=file_date, rows=len(frame), action="Use as the TAQ source for labels and checkpoints.")
        if i % 200 == 0:
            print(f"  read TAQ files: {i:,}/{len(paths):,}", flush=True)

    taq = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=keep + ["ticker"])
    taq = taq.drop_duplicates(["ticker", "date"], keep=False)
    taq["auction_volume"] = taq["auction_volume"].where(taq["auction_volume"].gt(0))
    return taq, file_dates, half_day, invalid_file_dates


def load_bloomberg_daily(
    project_dir: Path,
    tickers: list[str],
    audit: list[dict[str, Any]],
) -> pd.DataFrame:
    folder = project_dir / "Data/raw/exchanges/NYSE/Bloomberg/daily"
    frames: list[pd.DataFrame] = []
    for i, ticker in enumerate(tickers, 1):
        path = folder / bbg_file_key(ticker)
        if not path.is_file():
            audit_row(audit, "Bloomberg_daily", path.name, "missing_file", ticker=ticker, action="Leave Bloomberg features missing; do not refill or redownload.")
            continue
        if path.stat().st_size == 0:
            audit_row(audit, "Bloomberg_daily", path.name, "empty_file", ticker=ticker, action="Leave Bloomberg features missing; do not refill or redownload.")
            continue
        try:
            frame = pd.read_csv(path, low_memory=False)
        except Exception as exc:
            audit_row(audit, "Bloomberg_daily", path.name, "unreadable_file", ticker=ticker, detail=str(exc), action="Leave Bloomberg features missing.")
            continue
        if frame.empty:
            audit_row(audit, "Bloomberg_daily", path.name, "header_only_file", ticker=ticker, detail="Column names exist, but there are no data rows.", action="Leave Bloomberg features missing; do not refill or redownload.")
            continue
        if "date" not in frame.columns:
            audit_row(audit, "Bloomberg_daily", path.name, "missing_date_field", ticker=ticker, action="Leave Bloomberg features missing.")
            continue
        missing_fields = sorted(set(BBG_FIELDS).difference(frame.columns))
        for col in BBG_FIELDS:
            if col not in frame.columns:
                frame[col] = np.nan
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        frame = frame.loc[frame["date"].notna(), ["date", *BBG_FIELDS]].copy()
        frame["ticker"] = ticker
        exact_before = len(frame)
        frame = frame.drop_duplicates()
        if exact_before > len(frame):
            audit_row(audit, "Bloomberg_daily", path.name, "exact_duplicates_removed", ticker=ticker, rows=exact_before - len(frame), action="Keep one identical ticker-date row.")
        dup = frame.duplicated(["ticker", "date"], keep=False)
        if dup.any():
            bad = frame.loc[dup, ["ticker", "date"]].drop_duplicates()
            audit_row(audit, "Bloomberg_daily", path.name, "conflicting_stock_date_duplicates", ticker=ticker, rows=len(bad), detail="Conflicting Bloomberg daily observations for one ticker-date.", action="Retain the date key and set its Bloomberg fields to missing; keep TAQ targets.")
            frame = frame.merge(bad.assign(_bad=True), on=["ticker", "date"], how="left")
            frame.loc[frame["_bad"].fillna(False), BBG_FIELDS] = np.nan
            frame = frame.drop(columns="_bad").drop_duplicates(["ticker", "date"])
        if missing_fields:
            audit_row(audit, "Bloomberg_daily", path.name, "missing_daily_fields", ticker=ticker, rows=len(frame), detail=", ".join(missing_fields), action="Keep available fields; missing features remain blank.")
        else:
            audit_row(audit, "Bloomberg_daily", path.name, "ok", ticker=ticker, rows=len(frame), action="Use lagged daily values as model features.")
        frames.append(frame)
        if i % 250 == 0:
            print(f"  read Bloomberg files: {i:,}/{len(tickers):,}", flush=True)
    if not frames:
        raise ValueError(f"No usable Bloomberg daily rows found in {folder}")
    return pd.concat(frames, ignore_index=True)


def get_rebalance_dates(project_dir: Path, audit: list[dict[str, Any]]) -> tuple[set[pd.Timestamp], pd.DataFrame]:
    path = project_dir / EVENT_CALENDAR
    calendar = pd.read_csv(path, dtype=str).fillna("")
    if "rebalance_day" not in calendar.columns:
        raise ValueError(f"rebalance_day not found in {path}")
    calendar["rebalance_day"] = pd.to_datetime(calendar["rebalance_day"], errors="coerce").dt.normalize()
    calendar = calendar.loc[calendar["rebalance_day"].notna()].copy()
    dates = set(calendar["rebalance_day"].unique())
    for date in sorted(dates):
        items = calendar.loc[calendar["rebalance_day"].eq(date)]
        names = "; ".join(items.get("index_name", pd.Series(dtype=str)).astype(str).unique())
        audit_row(audit, "calendar", path.name, "excluded_rebalance_day", date=date, rows=len(items), detail=names, action="Exclude this date from outcomes and all historical rolling features.")
    return dates, calendar


def make_bloomberg_asof_features(
    bbg: pd.DataFrame,
    sessions: list[pd.Timestamp],
    rebalance_dates: set[pd.Timestamp],
    model_dates: list[pd.Timestamp],
) -> pd.DataFrame:
    """Build point-in-time Bloomberg features with field-specific gap handling."""
    session_index = {date: i for i, date in enumerate(sessions)}
    eligible = [date for date in sessions if date not in rebalance_dates]
    next_eligible = dict(zip(eligible[:-1], eligible[1:], strict=True))
    all_rows: list[pd.DataFrame] = []
    required_dates = set(model_dates)

    for ticker, source in bbg.groupby("ticker", sort=False):
        indexed = source.set_index("date").sort_index().reindex(sessions)
        px = pd.to_numeric(indexed["PX_LAST"], errors="coerce")
        prev_px = px.shift(1)
        ret = px.div(prev_px).sub(1.0)
        dates = pd.Series(sessions, index=sessions)
        previous_event = dates.shift(1).isin(rebalance_dates).to_numpy()
        current_event = dates.isin(rebalance_dates).to_numpy()
        ret.iloc[np.logical_or(previous_event, current_event)] = np.nan

        eligible_mask = np.array([date not in rebalance_dates for date in sessions])
        e = indexed.loc[eligible_mask].copy()
        r = ret.loc[eligible_mask]
        px_raw = pd.to_numeric(e["PX_LAST"], errors="coerce").where(pd.to_numeric(e["PX_LAST"], errors="coerce").gt(0))
        cap_raw = pd.to_numeric(e["CUR_MKT_CAP"], errors="coerce").where(pd.to_numeric(e["CUR_MKT_CAP"], errors="coerce").gt(0))
        flow_volume = pd.to_numeric(e["PX_VOLUME"], errors="coerce").where(pd.to_numeric(e["PX_VOLUME"], errors="coerce").ge(0))
        flow_turnover = pd.to_numeric(e["TURNOVER"], errors="coerce").where(pd.to_numeric(e["TURNOVER"], errors="coerce").ge(0))

        source_dates = list(e.index)
        target_dates = [next_eligible.get(date, pd.NaT) for date in source_dates]
        target_positions = np.array(
            [session_index.get(date, np.nan) if pd.notna(date) else np.nan for date in target_dates],
            dtype=float,
        )
        date_series = pd.Series(e.index, index=e.index)
        px_source_date = date_series.where(px_raw.notna()).ffill()
        cap_source_date = date_series.where(cap_raw.notna()).ffill()
        px_source_positions = np.array(
            [session_index.get(date, np.nan) if pd.notna(date) else np.nan for date in px_source_date],
            dtype=float,
        )
        cap_source_positions = np.array(
            [session_index.get(date, np.nan) if pd.notna(date) else np.nan for date in cap_source_date],
            dtype=float,
        )
        px_age = pd.Series(target_positions - px_source_positions, index=e.index)
        cap_age = pd.Series(target_positions - cap_source_positions, index=e.index)

        px_asof = px_raw.ffill().where(px_age.le(PRICE_STALENESS_LIMIT))
        cap_asof = cap_raw.ffill().where(cap_age.le(PRICE_STALENESS_LIMIT))
        volume_prior_median = flow_volume.shift(1).rolling(
            FLOW_MEDIAN_WINDOW, min_periods=FLOW_MEDIAN_MIN_OBS
        ).median()
        turnover_prior_median = flow_turnover.shift(1).rolling(
            FLOW_MEDIAN_WINDOW, min_periods=FLOW_MEDIAN_MIN_OBS
        ).median()
        volume_feature = flow_volume.fillna(volume_prior_median)
        turnover_feature = flow_turnover.fillna(turnover_prior_median)

        log_ret = np.log1p(r.where(r.gt(-1.0)))
        features = pd.DataFrame(index=e.index)
        features["feat_px_last_log"] = np.log(px_asof)
        features["feat_px_volume_log1p"] = np.log1p(volume_feature)
        features["feat_turnover_log1p"] = np.log1p(turnover_feature)
        features["feat_market_cap_log"] = np.log(cap_asof)
        features["feat_px_last_was_asof_filled"] = (px_raw.isna() & px_asof.notna()).astype(float)
        features["feat_market_cap_was_asof_filled"] = (cap_raw.isna() & cap_asof.notna()).astype(float)
        features["feat_px_volume_was_rolling_filled"] = (flow_volume.isna() & volume_feature.notna()).astype(float)
        features["feat_turnover_was_rolling_filled"] = (flow_turnover.isna() & turnover_feature.notna()).astype(float)
        features["feat_return_1d"] = r
        features["feat_momentum_5d"] = np.expm1(log_ret.rolling(5, min_periods=MOMENTUM_5_MIN_OBS).sum())
        features["feat_momentum_20d"] = np.expm1(log_ret.rolling(20, min_periods=MOMENTUM_20_MIN_OBS).sum())
        features["feat_volatility_20d"] = r.rolling(20, min_periods=VOLATILITY_20_MIN_OBS).std()
        features["feat_volatility_60d"] = r.rolling(60, min_periods=VOLATILITY_60_MIN_OBS).std()
        last = pd.to_numeric(e["PX_LAST"], errors="coerce").abs()
        high = pd.to_numeric(e["PX_HIGH"], errors="coerce").abs()
        low = pd.to_numeric(e["PX_LOW"], errors="coerce").abs()
        open_ = pd.to_numeric(e["PX_OPEN"], errors="coerce").abs()
        features["feat_range_pct"] = (high - low).div(last.where(last.gt(0)))
        features["feat_open_close_return"] = last.div(open_.where(open_.gt(0))).sub(1.0)

        features["date"] = target_dates
        features["market_feature_date"] = source_dates
        features["market_px_last_source_date"] = px_source_date.to_numpy()
        features["market_cap_source_date"] = cap_source_date.to_numpy()
        features["market_feature_age_sessions"] = pd.concat([px_age, cap_age], axis=1).max(axis=1)
        features["ticker"] = ticker
        features = features.loc[features["date"].isin(required_dates)].copy()
        all_rows.append(features.reset_index(drop=True))

    if not all_rows:
        return pd.DataFrame(columns=[
            "ticker", "date", "market_feature_date", "market_px_last_source_date",
            "market_cap_source_date", "market_feature_age_sessions",
        ])
    return pd.concat(all_rows, ignore_index=True)


def make_calendar_features(
    model_dates: list[pd.Timestamp],
    all_sessions: list[pd.Timestamp],
    half_day_by_date: dict[pd.Timestamp, float],
) -> pd.DataFrame:
    dates = pd.DatetimeIndex(model_dates)
    session_frame = pd.DataFrame({"date": all_sessions})
    session_frame = session_frame.loc[session_frame["date"].between(min(model_dates), max(model_dates))].copy()
    session_frame["month"] = session_frame["date"].dt.to_period("M")
    session_frame["quarter"] = session_frame["date"].dt.to_period("Q")
    max_period = session_frame["month"].max()
    max_quarter = session_frame["quarter"].max()
    last_by_month = session_frame.groupby("month")["date"].max()
    last_by_quarter = session_frame.groupby("quarter")["date"].max()
    out = pd.DataFrame({"date": dates})
    out["feat_weekday_sin"] = np.sin(2 * np.pi * out["date"].dt.weekday / 5.0)
    out["feat_weekday_cos"] = np.cos(2 * np.pi * out["date"].dt.weekday / 5.0)
    out["feat_month_sin"] = np.sin(2 * np.pi * (out["date"].dt.month - 1) / 12.0)
    out["feat_month_cos"] = np.cos(2 * np.pi * (out["date"].dt.month - 1) / 12.0)
    out["feat_is_month_end"] = [
        float(last_by_month.get(d.to_period("M"), pd.NaT) == d and d.to_period("M") < max_period)
        for d in out["date"]
    ]
    out["feat_is_quarter_end"] = [
        float(last_by_quarter.get(d.to_period("Q"), pd.NaT) == d and d.to_period("Q") < max_quarter)
        for d in out["date"]
    ]
    out["feat_is_half_day"] = out["date"].map(half_day_by_date).astype(float)

    expiry_dates: set[pd.Timestamp] = set()
    sessions = pd.DatetimeIndex(all_sessions)
    months = sorted(set(sessions.to_period("M")))
    for month in months:
        month_days = sessions[sessions.to_period("M") == month]
        fridays = [d for d in pd.date_range(month.start_time.normalize(), month.end_time.normalize(), freq="D") if d.weekday() == 4]
        if len(fridays) < 3:
            continue
        third_friday = fridays[2]
        if third_friday in month_days:
            expiry_dates.add(third_friday)
        else:
            prior = month_days[month_days < third_friday]
            if len(prior):
                expiry_dates.add(prior[-1])
    out["feat_is_monthly_option_expiry"] = out["date"].isin(expiry_dates).astype(float)
    return out


def _rolling_features(panel: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    panel = panel.sort_values(["ticker", "date"]).reset_index(drop=True)
    n = len(panel)
    new: dict[str, np.ndarray] = {}

    def allocate(name: str) -> np.ndarray:
        if name not in new:
            new[name] = np.full(n, np.nan, dtype=np.float32)
        return new[name]

    target_sources: list[tuple[str, str]] = [("auction", "auction_volume")] + [(tag, source) for source, tag in zip(TAQ_CP, CP_TAGS, strict=True)]
    for _, group in panel.groupby("ticker", sort=False):
        idx = group.index.to_numpy()
        for tag, source in target_sources:
            values = pd.to_numeric(group[source], errors="coerce")
            if tag == "auction":
                values = values.where(values.gt(0))
            else:
                values = values.where(values.ge(0))
            log_values = np.log1p(values)
            prior = log_values.shift(1)
            allocate(f"hist_{tag}_log_lag1")[idx] = prior.to_numpy(dtype=np.float32)
            allocate(f"hist_{tag}_log_last_valid")[idx] = log_values.ffill().shift(1).to_numpy(dtype=np.float32)
            age_source = pd.Series(np.arange(len(group), dtype=float), index=group.index).where(log_values.notna()).ffill().shift(1)
            allocate(f"hist_{tag}_age_sessions")[idx] = (np.arange(len(group), dtype=float) - age_source.to_numpy()).astype(np.float32)
            windows = [5, 20, 60] if tag == "auction" else [5, 20]
            for window in windows:
                roll = prior.rolling(window, min_periods=1)
                allocate(f"hist_{tag}_log_mean_{window}")[idx] = roll.mean().to_numpy(dtype=np.float32)
                allocate(f"hist_{tag}_count_{window}")[idx] = roll.count().to_numpy(dtype=np.float32)
                if tag == "auction" and window in (20, 60):
                    allocate(f"hist_{tag}_log_median_{window}")[idx] = roll.median().to_numpy(dtype=np.float32)
            if tag == "auction":
                allocate("hist_auction_log_std_20")[idx] = prior.rolling(20, min_periods=2).std().to_numpy(dtype=np.float32)

        auc = pd.to_numeric(group["auction_volume"], errors="coerce").where(pd.to_numeric(group["auction_volume"], errors="coerce").gt(0))
        for cp_source, cp_tag in zip(TAQ_CP, CP_TAGS, strict=True):
            cp_volume = pd.to_numeric(group[cp_source], errors="coerce")
            ratio = auc.div(cp_volume.where(cp_volume.gt(0)))
            ratio_prior = ratio.shift(1)
            allocate(f"hist_auction_to_{cp_tag}_ratio_median_20")[idx] = ratio_prior.rolling(20, min_periods=1).median().to_numpy(dtype=np.float32)
            allocate(f"hist_auction_to_{cp_tag}_ratio_count_20")[idx] = ratio_prior.rolling(20, min_periods=1).count().to_numpy(dtype=np.float32)

    for name, values in new.items():
        panel[name] = values

    features = [name for name in new if name not in {f"hist_{tag}_age_sessions" for tag in CP_TAGS}]
    return panel, features


def _make_preopen_features(panel: pd.DataFrame) -> list[str]:
    history = [
        "hist_auction_log_lag1",
        "hist_auction_log_last_valid",
        "hist_auction_age_sessions",
        "hist_auction_log_mean_5",
        "hist_auction_log_mean_20",
        "hist_auction_log_mean_60",
        "hist_auction_log_median_20",
        "hist_auction_log_median_60",
        "hist_auction_log_std_20",
        "hist_auction_count_5",
        "hist_auction_count_20",
        "hist_auction_count_60",
    ]
    for tag in CP_TAGS:
        history += [f"hist_auction_to_{tag}_ratio_median_20", f"hist_auction_to_{tag}_ratio_count_20"]
    for tag in CP_TAGS:
        history += [
            f"hist_{tag}_log_lag1",
            f"hist_{tag}_log_last_valid",
            f"hist_{tag}_log_mean_5",
            f"hist_{tag}_log_mean_20",
            f"hist_{tag}_count_5",
            f"hist_{tag}_count_20",
        ]
    market = [
        "feat_px_last_log",
        "feat_px_volume_log1p",
        "feat_turnover_log1p",
        "feat_market_cap_log",
        "feat_px_last_was_asof_filled",
        "feat_market_cap_was_asof_filled",
        "feat_px_volume_was_rolling_filled",
        "feat_turnover_was_rolling_filled",
        "feat_return_1d",
        "feat_momentum_5d",
        "feat_momentum_20d",
        "feat_volatility_20d",
        "feat_volatility_60d",
        "feat_range_pct",
        "feat_open_close_return",
        "market_feature_age_sessions",
    ]
    calendar = [
        "feat_weekday_sin",
        "feat_weekday_cos",
        "feat_month_sin",
        "feat_month_cos",
        "feat_is_month_end",
        "feat_is_quarter_end",
        "feat_is_half_day",
        "feat_is_monthly_option_expiry",
    ]
    return [name for name in history + market + calendar if name in panel.columns]


class FoldPreprocessor:
    """Training-window winsorization, median imputation, then standardization."""

    def __init__(self) -> None:
        self.lower: pd.Series | None = None
        self.upper: pd.Series | None = None
        self.imputer: SimpleImputer | None = None
        self.scaler: StandardScaler | None = None
        self.columns: list[str] = []

    def _array(self, frame: pd.DataFrame) -> np.ndarray:
        assert self.lower is not None and self.upper is not None
        clipped = frame.loc[:, self.columns].clip(lower=self.lower, upper=self.upper, axis="columns")
        return clipped.to_numpy(dtype=np.float32, copy=True)

    def fit(self, frame: pd.DataFrame) -> "FoldPreprocessor":
        self.columns = list(frame.columns)
        self.lower = frame.quantile(0.005, numeric_only=True)
        self.upper = frame.quantile(0.995, numeric_only=True)
        arr = self._array(frame)
        self.imputer = SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)
        imputed = self.imputer.fit_transform(arr)
        self.scaler = StandardScaler()
        self.scaler.fit(imputed)
        return self

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        if self.imputer is None or self.scaler is None:
            raise RuntimeError("Preprocessor has not been fitted")
        arr = self._array(frame)
        imputed = self.imputer.transform(arr)
        return np.asarray(self.scaler.transform(imputed), dtype=np.float32)

    def clipping_summary(self, frame: pd.DataFrame, window: str) -> list[dict[str, Any]]:
        assert self.lower is not None and self.upper is not None
        rows = []
        for col in self.columns:
            vals = pd.to_numeric(frame[col], errors="coerce")
            rows.append(
                {
                    "dataset": "model_transform",
                    "source_file": window,
                    "date": "",
                    "ticker": col,
                    "status": "feature_winsor_limits",
                    "rows": int(vals.notna().sum()),
                    "detail": f"lower_0.5%={self.lower[col]:.8g}; upper_99.5%={self.upper[col]:.8g}",
                    "action": "Bounds estimated from training dates only; applied to later dates.",
                }
            )
        return rows


def fit_ridge_targets(
    x_train: np.ndarray,
    x_eval: np.ndarray,
    train_panel: pd.DataFrame,
    eval_panel: pd.DataFrame,
    alpha: float,
    tag: str,
) -> tuple[dict[str, np.ndarray], dict[str, Ridge]]:
    predictions: dict[str, np.ndarray] = {}
    models: dict[str, Ridge] = {}
    for target, name in TARGETS:
        y = pd.to_numeric(train_panel[target], errors="coerce").to_numpy(dtype=float)
        if target == "auction_volume":
            valid = np.isfinite(y) & (y > 0)
        else:
            valid = np.isfinite(y) & (y >= 0)
        if valid.sum() < 100:
            predictions[name] = np.full(len(eval_panel), np.nan)
            continue
        y_log = np.log1p(y[valid])
        model = Ridge(alpha=alpha, solver="lsqr", tol=1e-3)
        model.fit(x_train[valid], y_log)
        predictions[name] = np.maximum(model.predict(x_eval), 0.0)
        models[name] = model
        print(f"  {tag}: fitted {name} ridge on {int(valid.sum()):,} rows", flush=True)
    return predictions, models


def fit_ridge_auction(
    x_train: np.ndarray,
    x_eval: np.ndarray,
    train_panel: pd.DataFrame,
    alpha: float,
) -> tuple[np.ndarray, int]:
    """Fit the final-auction Ridge model for one expanding-window fold."""
    y = pd.to_numeric(train_panel["auction_volume"], errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(y) & (y > 0)
    if valid.sum() < 100:
        return np.full(len(x_eval), np.nan), int(valid.sum())
    model = Ridge(alpha=alpha, solver="lsqr", tol=1e-3)
    model.fit(x_train[valid], np.log1p(y[valid]))
    return np.maximum(model.predict(x_eval), 0.0), int(valid.sum())


def make_quarterly_cluster_features(
    panel: pd.DataFrame,
    train_indices: np.ndarray,
    eval_indices: np.ndarray,
    quarter_start: pd.Timestamp,
    audit: list[dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Cluster stocks using only their latest available pre-quarter profiles."""
    profile_columns = [name for name in KMEANS_PROFILE_FEATURES if name in panel.columns]
    prior = panel.loc[
        panel["date"].lt(quarter_start), ["ticker", "date", *profile_columns]
    ].sort_values(["ticker", "date"])
    latest = prior.groupby("ticker", sort=False, as_index=False).tail(1).copy()
    latest = latest.loc[latest[profile_columns].notna().any(axis=1)].copy()

    if len(latest) < 3 or not profile_columns:
        detail = f"available pre-quarter stock profiles={len(latest)}; profile columns={profile_columns}"
        audit_row(
            audit,
            "model",
            "KMeans",
            "quarterly_clusters_skipped",
            date=quarter_start,
            rows=len(latest),
            detail=detail,
            action="Skip this fold because too few usable pre-quarter profiles are available.",
        )
        return (
            np.zeros((len(train_indices), 1), dtype=np.float32),
            np.zeros((len(eval_indices), 1), dtype=np.float32),
            {"k": 1, "silhouette": np.nan, "mapping": {}},
        )

    imputer = SimpleImputer(strategy="median", keep_empty_features=True)
    scaler = StandardScaler()
    profile_values = scaler.fit_transform(imputer.fit_transform(latest[profile_columns]))
    max_k = min(8, len(latest) - 1)
    best_k = 2
    best_score = -np.inf
    best_model: KMeans | None = None
    for k in range(2, max_k + 1):
        candidate = KMeans(n_clusters=k, n_init=10, random_state=42)
        candidate_labels = candidate.fit_predict(profile_values)
        if len(np.unique(candidate_labels)) < 2 or len(np.unique(candidate_labels)) >= len(latest):
            continue
        score = float(
            silhouette_score(
                profile_values,
                candidate_labels,
                sample_size=min(2_000, len(latest)),
                random_state=42,
            )
        )
        if score > best_score:
            best_k, best_score, best_model = k, score, candidate

    if best_model is None:
        best_k = 1
        labels = np.zeros(len(latest), dtype=int)
    else:
        labels = best_model.labels_

    mapping = dict(zip(latest["ticker"].astype(str), labels.astype(int), strict=True))

    def encode(indices: np.ndarray) -> np.ndarray:
        tickers = panel.iloc[indices]["ticker"].astype(str).to_numpy()
        cluster_ids = np.array([mapping.get(ticker, -1) for ticker in tickers], dtype=int)
        encoded_ids = np.where(cluster_ids < 0, best_k, cluster_ids)
        return np.eye(best_k + 1, dtype=np.float32)[encoded_ids]

    profile_max_date = pd.Timestamp(latest["date"].max())
    audit_row(
        audit,
        "model",
        "KMeans",
        "quarterly_clusters_fitted",
        date=quarter_start,
        rows=len(latest),
        detail=(
            f"profile_available_through={profile_max_date.date()}; profile_features={profile_columns}; "
            f"selected_k={best_k}; silhouette={best_score:.6f}; unknown_tickers_use_separate_group"
        ),
        action="Fit clusters only from stock profiles observed before the forecast quarter; hold assignments fixed within the quarter.",
    )
    return encode(train_indices), encode(eval_indices), {
        "k": best_k,
        "silhouette": best_score,
        "mapping": mapping,
    }


def fit_alternative_auction_models(
    x_train: np.ndarray,
    x_eval: np.ndarray,
    train_panel: pd.DataFrame,
    eval_panel: pd.DataFrame,
    audit: list[dict[str, Any]],
    *,
    max_training_rows: int = 250_000,
) -> tuple[dict[str, np.ndarray], dict[str, int]]:
    """Fit optional tree models on a deterministic sample from the training window."""
    y = pd.to_numeric(train_panel["auction_volume"], errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(y) & (y > 0)
    valid_rows = np.flatnonzero(valid)
    if not len(valid_rows):
        return {}, {}
    if len(valid_rows) > max_training_rows:
        rng = np.random.default_rng(42)
        valid_rows = np.sort(rng.choice(valid_rows, size=max_training_rows, replace=False))
    y_train = np.log1p(y[valid_rows]).astype(np.float32)
    x_fit = x_train[valid_rows]
    results: dict[str, np.ndarray] = {}
    fit_counts: dict[str, int] = {}

    model_factories: list[tuple[str, Any]] = []
    try:
        from xgboost import XGBRegressor

        model_factories.append((
            "XGBoost_preopen",
            lambda: XGBRegressor(
                n_estimators=300,
                max_depth=6,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                reg_lambda=1.0,
                objective="reg:squarederror",
                tree_method="hist",
                max_bin=128,
                n_jobs=4,
                random_state=42,
                verbosity=0,
            ),
        ))
    except Exception as exc:
        audit_row(audit, "model", "xgboost", "model_dependency_unavailable", detail=str(exc).splitlines()[0], action="Skip XGBoost when its optional runtime dependency is unavailable.")

    try:
        from lightgbm import LGBMRegressor

        model_factories.append((
            "LightGBM_preopen",
            lambda: LGBMRegressor(
                n_estimators=300,
                num_leaves=31,
                learning_rate=0.05,
                subsample=0.8,
                subsample_freq=1,
                colsample_bytree=0.8,
                min_child_samples=100,
                reg_lambda=1.0,
                max_bin=127,
                n_jobs=4,
                random_state=42,
                verbosity=-1,
            ),
        ))
    except Exception as exc:
        audit_row(audit, "model", "lightgbm", "model_dependency_unavailable", detail=str(exc).splitlines()[0], action="Skip LightGBM when its optional runtime dependency is unavailable.")

    try:
        from catboost import CatBoostRegressor

        model_factories.append((
            "CatBoost_preopen",
            lambda: CatBoostRegressor(
                iterations=300,
                depth=8,
                learning_rate=0.05,
                loss_function="RMSE",
                l2_leaf_reg=3.0,
                thread_count=4,
                random_seed=42,
                allow_writing_files=False,
                verbose=False,
            ),
        ))
    except Exception as exc:
        audit_row(audit, "model", "catboost", "model_dependency_unavailable", detail=str(exc).splitlines()[0], action="Skip CatBoost when its optional dependency is unavailable.")

    model_factories.append((
        "HistGradientBoosting_preopen",
        lambda: HistGradientBoostingRegressor(
            max_iter=150,
            max_leaf_nodes=31,
            min_samples_leaf=100,
            learning_rate=0.08,
            l2_regularization=1.0,
            early_stopping=False,
            random_state=42,
        ),
    ))

    for name, factory in model_factories:
        print(f"  fitting {name} on {len(valid_rows):,} sampled training rows", flush=True)
        try:
            model = factory()
            model.fit(x_fit, y_train)
            results[name] = np.maximum(np.asarray(model.predict(x_eval), dtype=float), 0.0)
            fit_counts[name] = len(valid_rows)
            audit_row(
                audit,
                "model",
                name,
                "model_fitted",
                rows=len(valid_rows),
                detail="Final auction target trained on log1p(shares); deterministic sample from dates before the evaluation window.",
                action="Score on the untrimmed evaluation stock-dates alongside the baseline and Ridge.",
            )
            del model
            gc.collect()
        except Exception as exc:
            audit_row(audit, "model", name, "model_fit_failed", rows=len(valid_rows), detail=str(exc), action="Retain other model results and review the recorded failure.")
            print(f"  {name} skipped after fit failure: {exc}", flush=True)

    return results, fit_counts


def make_intraday_direct_predictions(
    x_train: np.ndarray,
    x_eval: np.ndarray,
    train_panel: pd.DataFrame,
    eval_panel: pd.DataFrame,
    alpha: float,
    tag: str,
) -> dict[str, np.ndarray]:
    result: dict[str, np.ndarray] = {}
    y_all = pd.to_numeric(train_panel["auction_volume"], errors="coerce").to_numpy(dtype=float)
    y_valid = np.isfinite(y_all) & (y_all > 0)
    for source, cp_tag in zip(TAQ_CP, CP_TAGS, strict=True):
        cp = pd.to_numeric(train_panel[source], errors="coerce").to_numpy(dtype=float)
        cp_valid = np.isfinite(cp) & (cp >= 0)
        fit_mask = y_valid & cp_valid
        eval_cp = pd.to_numeric(eval_panel[source], errors="coerce").to_numpy(dtype=float)
        eval_valid = np.isfinite(eval_cp) & (eval_cp >= 0)
        pred = np.full(len(eval_panel), np.nan)
        if fit_mask.sum() < 100 or not eval_valid.any():
            result[cp_tag] = pred
            continue
        cp_log_train = np.log1p(cp[fit_mask])
        lo, hi = np.quantile(cp_log_train, [0.005, 0.995])
        mean = cp_log_train.clip(lo, hi).mean()
        std = cp_log_train.clip(lo, hi).std() or 1.0
        cp_fit_scaled = ((cp_log_train.clip(lo, hi) - mean) / std).astype(np.float32)
        cp_log_eval = np.log1p(eval_cp[eval_valid]).clip(lo, hi)
        cp_eval_scaled = ((cp_log_eval - mean) / std).astype(np.float32)
        x_fit = np.column_stack([x_train[fit_mask], cp_fit_scaled])
        x_out = np.column_stack([x_eval[eval_valid], cp_eval_scaled])
        model = Ridge(alpha=alpha, solver="lsqr", tol=1e-3)
        model.fit(x_fit, np.log1p(y_all[fit_mask]))
        pred[eval_valid] = np.maximum(model.predict(x_out), 0.0)
        result[cp_tag] = pred
        print(f"  {tag}: fitted direct update at {cp_tag} on {int(fit_mask.sum()):,} rows", flush=True)
    return result


def calculate_metrics(
    actual_raw: np.ndarray,
    pred_log: np.ndarray,
    model: str,
    target: str,
    split: str,
    common_rows: int | None = None,
) -> dict[str, Any]:
    pred_raw = np.maximum(np.expm1(np.maximum(pred_log, 0.0)), 0.0)
    actual_log = np.log1p(np.maximum(actual_raw, 0.0))
    valid = np.isfinite(actual_raw) & np.isfinite(pred_log)
    if not valid.any():
        return {"split": split, "target": target, "model": model, "n": 0, "coverage": 0.0, "mae_log": np.nan, "rmse_log": np.nan, "mae_shares": np.nan, "rmse_shares": np.nan, "common_n": common_rows or 0}
    err_log = pred_log[valid] - actual_log[valid]
    err_raw = pred_raw[valid] - actual_raw[valid]
    return {
        "split": split,
        "target": target,
        "model": model,
        "n": int(valid.sum()),
        "coverage": float(valid.sum() / max(int(np.isfinite(actual_raw).sum()), 1)),
        "mae_log": float(np.mean(np.abs(err_log))),
        "rmse_log": float(np.sqrt(np.mean(np.square(err_log)))),
        "mae_shares": float(np.mean(np.abs(err_raw))),
        "rmse_shares": float(np.sqrt(np.mean(np.square(err_raw)))),
        "common_n": common_rows if common_rows is not None else int(valid.sum()),
    }


def main() -> None:
    args = parse_args()
    project_dir = args.project_dir.resolve()
    data_dir = project_dir / "Data/raw"
    output_dir = project_dir / "Data/processed/nyse"
    output_dir.mkdir(parents=True, exist_ok=True)
    audit: list[dict[str, Any]] = []

    print("Loading the current NYSE universe and raw TAQ/Bloomberg files...", flush=True)
    audit_row(
        audit,
        "Bloomberg_daily",
        "HistoricalDataRequest",
        "split_adjustment_unverified",
        detail="The local request does not record adjustmentFollowDPDF/adjustmentSplit; stored historical values are used as provided.",
        action="Lag fields by date, retain raw values, and do not claim point-in-time split rebasing.",
    )
    tickers, exact_symbol_map, no_slash_symbol_map = load_nyse_universe(project_dir, audit)
    taq, taq_file_dates, half_day_by_date, invalid_taq_file_dates = load_taq_days(
        project_dir, exact_symbol_map, no_slash_symbol_map, audit
    )
    bbg = load_bloomberg_daily(project_dir, tickers, audit)
    rebalance_dates, calendar = get_rebalance_dates(project_dir, audit)

    taq_dir = data_dir / "exchanges/NYSE/WRDS_TAQ/days"
    taq_paths = sorted(taq_dir.glob("*.csv.gz"))
    min_taq_file_date = min(taq_file_dates)
    max_taq_file_date = max(taq_file_dates)
    bbg_dates = set(pd.to_datetime(bbg["date"], errors="coerce").dropna().dt.normalize().unique())
    all_sessions = sorted(
        date for date in (taq_file_dates | bbg_dates)
        if min_taq_file_date <= date <= max_taq_file_date
    )
    if not all_sessions:
        raise ValueError("Could not infer NYSE trading dates from the existing files")
    model_sessions = [date for date in all_sessions if date not in rebalance_dates]
    min_date, max_date = min_taq_file_date, max_taq_file_date
    model_dates = [date for date in model_sessions if min_date <= date <= max_date]

    missing_day_dates = sorted(set(bbg_dates).intersection(set(all_sessions)).difference(taq_file_dates))
    for date in missing_day_dates:
        audit_row(audit, "WRDS_TAQ", "", "missing_day_file", date=date, action="Treat the full TAQ date as a data gap; do not redownload.")
    bbg_months = bbg.loc[bbg["date"].between(min_date, max_date)].groupby("ticker")["date"].agg(["min", "max"])
    taq_months = taq.groupby("ticker")["date"].agg(["min", "max"]) if len(taq) else pd.DataFrame(columns=["min", "max"])
    bounds = pd.concat([bbg_months, taq_months]).groupby(level=0).agg({"min": "min", "max": "max"})
    universe_with_dates = [ticker for ticker in tickers if ticker in bounds.index]
    no_date_tickers = sorted(set(tickers).difference(universe_with_dates))
    for ticker in no_date_tickers:
        audit_row(audit, "panel", "", "ticker_without_observations", ticker=ticker, action="Do not create invented stock-day rows.")

    print(f"  universe: {len(tickers):,} current NYSE Bloomberg tickers", flush=True)
    print(f"  TAQ: {len(taq):,} mapped stock-day rows; {len(taq_file_dates):,} dated files", flush=True)
    print(f"  Bloomberg daily: {len(bbg):,} rows across {bbg['ticker'].nunique():,} tickers", flush=True)
    print(f"  calendar: {len(rebalance_dates.intersection(set(all_sessions))):,} rebalance dates to exclude", flush=True)

    # Each row is a ticker on an observed date in its own history. Missing TAQ
    # rows inside that span remain explicit gaps; no label is filled with zero.
    pieces: list[pd.DataFrame] = []
    sessions_array = np.array(model_dates, dtype="datetime64[ns]")
    for ticker in universe_with_dates:
        first = max(pd.Timestamp(bounds.loc[ticker, "min"]).normalize(), min_date)
        last = min(pd.Timestamp(bounds.loc[ticker, "max"]).normalize(), max_date)
        dates = sessions_array[(sessions_array >= np.datetime64(first)) & (sessions_array <= np.datetime64(last))]
        if len(dates):
            pieces.append(pd.DataFrame({"ticker": ticker, "date": pd.to_datetime(dates)}))
    panel = pd.concat(pieces, ignore_index=True)
    panel = panel.merge(taq, on=["ticker", "date"], how="left", validate="one_to_one", suffixes=("", "_taq"))
    panel = panel.merge(bbg, on=["ticker", "date"], how="left", validate="one_to_one", suffixes=("", "_bbg"))
    panel["taq_row_present"] = panel["sym_root"].notna()
    panel["target_missing_reason"] = np.select(
        [
            ~panel["taq_row_present"] & panel["date"].isin(invalid_taq_file_dates),
            ~panel["taq_row_present"] & ~panel["date"].isin(taq_file_dates),
            ~panel["taq_row_present"],
            panel["auction_volume"].isna(),
        ],
        ["empty_or_invalid_taq_day_file", "missing_taq_day_file", "no_taq_symbol_row", "no_condition6_label"],
        default="label_observed",
    )
    panel["auction_volume"] = pd.to_numeric(panel["auction_volume"], errors="coerce").where(pd.to_numeric(panel["auction_volume"], errors="coerce").gt(0))
    for cp in TAQ_CP:
        panel[cp] = pd.to_numeric(panel[cp], errors="coerce")
    panel["is_half_day"] = panel["date"].map(half_day_by_date)
    panel["sample_split"] = "train"

    # Capture actual TAQ rows removed by index events before building features.
    event_taq = taq.loc[taq["date"].isin(rebalance_dates)]
    audit_row(
        audit,
        "sample",
        EVENT_CALENDAR,
        "rebalance_rows_excluded",
        rows=len(event_taq),
        detail=f"positive auction labels={int(event_taq['auction_volume'].gt(0).sum())}; event dates={len(rebalance_dates.intersection(set(all_sessions)))}",
        action="Exclude event dates before computing every rolling feature.",
    )

    sessions_full = sorted(set(pd.to_datetime(bbg["date"]).dropna().dt.normalize()).union(set(all_sessions)))
    price_features = make_bloomberg_asof_features(bbg, sessions_full, rebalance_dates, model_dates)
    panel = panel.merge(price_features, on=["ticker", "date"], how="left", validate="one_to_one")
    calendar_features = make_calendar_features(model_dates, all_sessions, half_day_by_date)
    panel = panel.merge(calendar_features, on="date", how="left", validate="many_to_one")
    panel, _ = _rolling_features(panel)
    del taq, bbg, price_features, calendar_features, pieces
    gc.collect()

    # Time splits are by whole trading dates: train, then validation, then test.
    unique_dates = pd.DatetimeIndex(sorted(panel["date"].drop_duplicates()))
    train_end_i = max(1, int(math.floor(len(unique_dates) * args.train_fraction)))
    val_count = max(1, int(math.floor(len(unique_dates) * args.validation_fraction)))
    validation_end_i = min(len(unique_dates) - 1, train_end_i + val_count)
    train_end_date = unique_dates[train_end_i - 1]
    val_end_date = unique_dates[validation_end_i - 1]
    panel.loc[panel["date"].gt(train_end_date) & panel["date"].le(val_end_date), "sample_split"] = "validation"
    panel.loc[panel["date"].gt(val_end_date), "sample_split"] = "test"
    train_mask = panel["sample_split"].eq("train").to_numpy()
    val_mask = panel["sample_split"].eq("validation").to_numpy()
    test_mask = panel["sample_split"].eq("test").to_numpy()
    feature_cols = _make_preopen_features(panel)
    if not feature_cols:
        raise ValueError("No model features were constructed")
    print(f"  model panel: {len(panel):,} ticker-date rows; {len(feature_cols)} pre-open features", flush=True)
    print(f"  chronological split: train through {train_end_date.date()}, validation through {val_end_date.date()}, test starts after that", flush=True)

    panel_out = output_dir / "nyse_model_panel.csv.gz"
    panel.to_csv(panel_out, index=False, compression={"method": "gzip", "compresslevel": 1, "mtime": 0}, float_format="%.8g")
    print(f"  wrote {panel_out.relative_to(project_dir)}", flush=True)

    features = panel[feature_cols]
    train_panel = panel.loc[train_mask]
    val_panel = panel.loc[val_mask]
    trainval_panel = panel.loc[train_mask | val_mask]
    test_panel = panel.loc[test_mask]
    x_train_raw = features.loc[train_mask]
    x_val_raw = features.loc[val_mask]
    x_trainval_raw = features.loc[train_mask | val_mask]
    x_test_raw = features.loc[test_mask]

    print("Fitting pre-open Ridge forecasts...", flush=True)
    pre_train = FoldPreprocessor().fit(x_train_raw)
    x_train = pre_train.transform(x_train_raw)
    x_val = pre_train.transform(x_val_raw)
    val_preopen_log, _ = fit_ridge_targets(x_train, x_val, train_panel, val_panel, args.alpha, "validation")
    audit.extend(pre_train.clipping_summary(x_train_raw, "train"))
    del x_train, x_val, pre_train
    gc.collect()

    pre_test = FoldPreprocessor().fit(x_trainval_raw)
    x_trainval = pre_test.transform(x_trainval_raw)
    x_test = pre_test.transform(x_test_raw)
    test_preopen_log, _ = fit_ridge_targets(x_trainval, x_test, trainval_panel, test_panel, args.alpha, "test")
    audit.extend(pre_test.clipping_summary(x_trainval_raw, "train_plus_validation"))

    predictions = pd.DataFrame({
        "ticker": test_panel["ticker"].to_numpy(),
        "date": test_panel["date"].dt.strftime("%Y-%m-%d").to_numpy(),
        "actual_auction_volume": pd.to_numeric(test_panel["auction_volume"], errors="coerce").to_numpy(),
        "auction_persistence": np.expm1(pd.to_numeric(test_panel["hist_auction_log_last_valid"], errors="coerce").to_numpy()),
        "auction_ridge_log": test_preopen_log["auction"],
    })
    predictions["auction_ridge"] = np.maximum(np.expm1(predictions["auction_ridge_log"]), 0.0)
    tree_preopen_logs, tree_training_counts = fit_alternative_auction_models(
        x_trainval,
        x_test,
        trainval_panel,
        test_panel,
        audit,
    )
    for model_name, prediction_log in tree_preopen_logs.items():
        predictions[f"{model_name}_log"] = prediction_log
        predictions[model_name] = np.maximum(np.expm1(prediction_log), 0.0)
    for source, tag in zip(TAQ_CP, CP_TAGS, strict=True):
        predictions[f"actual_cumvol_{tag}"] = pd.to_numeric(test_panel[source], errors="coerce").to_numpy()
        predictions[f"cumvol_{tag}_persistence"] = np.expm1(pd.to_numeric(test_panel[f"hist_{tag}_log_last_valid"], errors="coerce").to_numpy())
        predictions[f"cumvol_{tag}_ridge_log"] = test_preopen_log[tag]
        predictions[f"cumvol_{tag}_ridge"] = np.maximum(np.expm1(test_preopen_log[tag]), 0.0)

    # A plain checkpoint benchmark: current cumulative volume times the
    # stock's historical median final-auction/checkpoint ratio.
    test_final_log = np.log1p(pd.to_numeric(test_panel["auction_volume"], errors="coerce").to_numpy())
    val_final_log = np.log1p(pd.to_numeric(val_panel["auction_volume"], errors="coerce").to_numpy())
    for source, tag in zip(TAQ_CP, CP_TAGS, strict=True):
        cp_test = pd.to_numeric(test_panel[source], errors="coerce").to_numpy(dtype=float)
        cp_log_test = np.log1p(np.maximum(cp_test, 0.0))
        ratio = pd.to_numeric(test_panel[f"hist_auction_to_{tag}_ratio_median_20"], errors="coerce").to_numpy(dtype=float)
        naive_log = np.log1p(np.maximum(cp_test, 0.0) * ratio)
        naive_log[~(np.isfinite(cp_test) & np.isfinite(ratio) & (ratio >= 0))] = np.nan
        predictions[f"intraday_{tag}_naive_log"] = naive_log
        predictions[f"intraday_{tag}_naive"] = np.maximum(np.expm1(np.maximum(naive_log, 0.0)), 0.0)

    print("Fitting direct actual-checkpoint update models...", flush=True)
    # Validation updates use train-only preprocessing; final test updates use
    # an expanding training window through the validation period.
    direct_train_pre = FoldPreprocessor().fit(x_train_raw)
    x_train_direct = direct_train_pre.transform(x_train_raw)
    x_val_direct = direct_train_pre.transform(x_val_raw)
    val_direct_log = make_intraday_direct_predictions(x_train_direct, x_val_direct, train_panel, val_panel, args.alpha, "validation")
    del x_train_direct, x_val_direct, direct_train_pre
    gc.collect()
    test_direct_log = make_intraday_direct_predictions(x_trainval, x_test, trainval_panel, test_panel, args.alpha, "test")
    for tag in CP_TAGS:
        predictions[f"intraday_{tag}_direct_log"] = test_direct_log[tag]
        predictions[f"intraday_{tag}_direct"] = np.maximum(np.expm1(test_direct_log[tag]), 0.0)

    print("Training checkpoint forecast-error corrections from validation-only forecasts...", flush=True)
    correction_counts: dict[str, int] = {}
    for source, tag in zip(TAQ_CP, CP_TAGS, strict=True):
        val_actual_cp = pd.to_numeric(val_panel[source], errors="coerce").to_numpy(dtype=float)
        test_actual_cp = pd.to_numeric(test_panel[source], errors="coerce").to_numpy(dtype=float)
        val_cp_log = np.log1p(np.maximum(val_actual_cp, 0.0))
        test_cp_log = np.log1p(np.maximum(test_actual_cp, 0.0))
        e_val = val_cp_log - val_preopen_log[tag]
        val_resid_final = val_final_log - val_preopen_log["auction"]
        fit_mask = np.isfinite(e_val) & np.isfinite(val_resid_final) & np.isfinite(val_actual_cp)
        e_test = test_cp_log - test_preopen_log[tag]
        corrected = np.full(len(test_panel), np.nan)
        if fit_mask.sum() >= 100:
            correction = Ridge(alpha=args.alpha, solver="lsqr", tol=1e-3)
            correction.fit(e_val[fit_mask].reshape(-1, 1), val_resid_final[fit_mask])
            valid = np.isfinite(e_test) & np.isfinite(test_preopen_log["auction"])
            corrected[valid] = np.maximum(
                test_preopen_log["auction"][valid] + correction.predict(e_test[valid].reshape(-1, 1)),
                0.0,
            )
        correction_counts[tag] = int(fit_mask.sum())
        predictions[f"intraday_{tag}_error_correction_log"] = corrected
        predictions[f"intraday_{tag}_error_correction"] = np.maximum(np.expm1(corrected), 0.0)
        audit_row(audit, "model", "validation_predictions", "error_correction_fit", ticker=tag, rows=correction_counts[tag], detail="Out-of-sample validation residuals; one checkpoint error predicts final-auction forecast error.", action="Apply correction to later test forecasts only.")

    # Target-winsorized sensitivity is reported for final auction only. The
    # primary target and every score remain on the untrimmed observed labels.
    target_values = pd.to_numeric(trainval_panel["auction_volume"], errors="coerce").to_numpy(dtype=float)
    valid_target = np.isfinite(target_values) & (target_values > 0)
    target_log = np.log1p(target_values[valid_target])
    target_cap = float(np.quantile(target_log, 0.995)) if len(target_log) else np.nan
    if valid_target.sum() > 100 and np.isfinite(target_cap):
        winsor_model = Ridge(alpha=args.alpha, solver="lsqr", tol=1e-3)
        winsor_model.fit(x_trainval[valid_target], np.minimum(target_log, target_cap))
        predictions["auction_ridge_target_winsor_sens_log"] = np.maximum(winsor_model.predict(x_test), 0.0)
        predictions["auction_ridge_target_winsor_sens"] = np.maximum(np.expm1(predictions["auction_ridge_target_winsor_sens_log"]), 0.0)
        audit_row(audit, "model_transform", "train_plus_validation", "target_winsor_sensitivity", rows=int(valid_target.sum()), detail=f"upper_99.5%_log_target={target_cap:.8g}", action="Sensitivity model only; scores use original test labels.")
    else:
        predictions["auction_ridge_target_winsor_sens_log"] = np.nan
        predictions["auction_ridge_target_winsor_sens"] = np.nan

    # Refit at each calendar-quarter boundary. Each fold uses only labeled
    # stock-dates strictly before that quarter, including earlier test quarters.
    del x_trainval, x_test, x_trainval_raw, x_test_raw, x_train_raw, x_val_raw
    gc.collect()
    print("Fitting quarterly expanding-window auction forecasts...", flush=True)
    test_periods = test_panel["date"].dt.to_period("Q")
    walkforward_quarters = sorted(test_periods.dropna().unique())
    walkforward_predictions: dict[str, np.ndarray] = {
        "Ridge_quarterly_expanding": np.full(len(test_panel), np.nan, dtype=float),
        "Ridge_KMeans_quarterly_expanding": np.full(len(test_panel), np.nan, dtype=float),
    }
    tree_walkforward_names = {
        name: name.replace("_preopen", "_quarterly_expanding")
        for name in tree_preopen_logs
    }
    for walk_name in tree_walkforward_names.values():
        walkforward_predictions[walk_name] = np.full(len(test_panel), np.nan, dtype=float)
    walkforward_training_counts: dict[str, list[int]] = {
        name: [] for name in walkforward_predictions
    }
    walkforward_clusters = np.full(len(test_panel), "", dtype=object)
    walkforward_cluster_k = np.full(len(test_panel), np.nan, dtype=float)

    target_all = pd.to_numeric(panel["auction_volume"], errors="coerce").to_numpy(dtype=float)
    label_available = np.isfinite(target_all) & (target_all > 0)
    for quarter in walkforward_quarters:
        quarter_mask = test_periods.eq(quarter).to_numpy()
        test_positions = np.flatnonzero(quarter_mask)
        test_indices = test_panel.index.to_numpy()[test_positions]
        quarter_start = pd.Timestamp(test_panel.iloc[test_positions]["date"].min())
        prior_dates = panel["date"].lt(quarter_start).to_numpy()
        train_indices = np.flatnonzero(prior_dates & label_available)
        if len(train_indices) < 100:
            audit_row(
                audit,
                "walk_forward",
                str(quarter),
                "fold_skipped_insufficient_labels",
                date=quarter_start,
                rows=len(train_indices),
                detail=f"test stock-dates={len(test_positions)}",
                action="Keep the fold unscored because fewer than 100 earlier labels are available.",
            )
            continue

        train_features = features.iloc[train_indices]
        eval_features = features.loc[test_indices]
        fold_preprocessor = FoldPreprocessor().fit(train_features)
        x_fold_train = fold_preprocessor.transform(train_features)
        x_fold_eval = fold_preprocessor.transform(eval_features)
        train_label_panel = panel.iloc[train_indices]
        ridge_log, ridge_n = fit_ridge_auction(
            x_fold_train,
            x_fold_eval,
            train_label_panel,
            args.alpha,
        )
        walkforward_predictions["Ridge_quarterly_expanding"][test_positions] = ridge_log
        walkforward_training_counts["Ridge_quarterly_expanding"].append(ridge_n)

        cluster_train, cluster_eval, cluster_result = make_quarterly_cluster_features(
            panel,
            train_indices,
            test_indices,
            quarter_start,
            audit,
        )
        cluster_ridge_log, cluster_ridge_n = fit_ridge_auction(
            np.column_stack([x_fold_train, cluster_train]),
            np.column_stack([x_fold_eval, cluster_eval]),
            train_label_panel,
            args.alpha,
        )
        walkforward_predictions["Ridge_KMeans_quarterly_expanding"][test_positions] = cluster_ridge_log
        walkforward_training_counts["Ridge_KMeans_quarterly_expanding"].append(cluster_ridge_n)
        cluster_mapping = cluster_result["mapping"]
        test_tickers = test_panel.iloc[test_positions]["ticker"].astype(str).to_numpy()
        walkforward_clusters[test_positions] = [
            f"{quarter}_C{cluster_mapping[ticker]}" if ticker in cluster_mapping else f"{quarter}_unknown"
            for ticker in test_tickers
        ]
        walkforward_cluster_k[test_positions] = cluster_result["k"]
        audit.extend(fold_preprocessor.clipping_summary(train_features, f"walk_forward_{quarter}"))

        fold_tree_logs, fold_tree_counts = fit_alternative_auction_models(
            x_fold_train,
            x_fold_eval,
            train_label_panel,
            test_panel.iloc[test_positions],
            audit,
        )
        for static_name, walk_name in tree_walkforward_names.items():
            if static_name in fold_tree_logs:
                walkforward_predictions[walk_name][test_positions] = fold_tree_logs[static_name]
                walkforward_training_counts[walk_name].append(fold_tree_counts.get(static_name, 0))

        fold_train_end = pd.Timestamp(panel.iloc[train_indices]["date"].max())
        audit_row(
            audit,
            "walk_forward",
            str(quarter),
            "quarterly_expanding_fold",
            date=quarter_start,
            rows=len(train_indices),
            detail=(
                f"training_end={fold_train_end.date()}; "
                f"labeled_training_rows={len(train_indices)}; test_stock_dates={len(test_positions)}"
            ),
            action="Fit preprocessing and models using only labeled dates before the quarter; score this quarter once.",
        )
        print(
            f"  {quarter}: trained through {fold_train_end.date()} "
            f"on {len(train_indices):,} labeled rows; scored {len(test_positions):,} stock-dates",
            flush=True,
        )
        del train_features, eval_features, x_fold_train, x_fold_eval, train_label_panel
        del cluster_train, cluster_eval, fold_preprocessor, fold_tree_logs, fold_tree_counts
        gc.collect()

    for model_name, prediction_log in walkforward_predictions.items():
        column_name = model_name.replace("_quarterly_expanding", "_expanding")
        column_name = column_name.replace("Ridge", "ridge").replace("XGBoost", "xgboost")
        column_name = column_name.replace("LightGBM", "lightgbm").replace("CatBoost", "catboost")
        column_name = column_name.replace("HistGradientBoosting", "hist_gradient_boosting")
        predictions[f"auction_{column_name}_log"] = prediction_log
        predictions[f"auction_{column_name}"] = np.maximum(np.expm1(prediction_log), 0.0)
    predictions["quarterly_kmeans_cluster"] = walkforward_clusters
    predictions["quarterly_kmeans_selected_k"] = walkforward_cluster_k

    # Attach current same-day checkpoint data for transparent intraday review.
    test_actual = test_panel["auction_volume"].to_numpy(dtype=float)
    metrics: list[dict[str, Any]] = []
    for source, tag in TARGETS:
        if tag == "auction":
            actual = pd.to_numeric(test_panel[source], errors="coerce").to_numpy(dtype=float)
            hist_pred = pd.to_numeric(test_panel["hist_auction_log_last_valid"], errors="coerce").to_numpy(dtype=float)
            ridge_pred = test_preopen_log[tag]
            winsor_sens_pred = predictions["auction_ridge_target_winsor_sens_log"].to_numpy(dtype=float)
            tree_metric_logs = {
                model_name: predictions[f"{model_name}_log"].to_numpy(dtype=float)
                for model_name in tree_preopen_logs
            }
            common = np.isfinite(actual) & np.isfinite(hist_pred) & np.isfinite(ridge_pred) & np.isfinite(winsor_sens_pred)
            for tree_pred in tree_metric_logs.values():
                common &= np.isfinite(tree_pred)
            common_n = int(common.sum())
            metrics.append(calculate_metrics(actual[common], hist_pred[common], "Last_valid_auction", tag, "test_common", common_n))
            metrics.append(calculate_metrics(actual[common], ridge_pred[common], "Ridge_preopen", tag, "test_common", common_n))
            metrics.append(calculate_metrics(actual[common], winsor_sens_pred[common], "Ridge_target_winsor_sensitivity", tag, "test_common", common_n))
            for model_name, tree_pred in tree_metric_logs.items():
                metric = calculate_metrics(actual[common], tree_pred[common], model_name, tag, "test_common", common_n)
                metric["training_n"] = tree_training_counts.get(model_name, 0)
                metrics.append(metric)

            # Fixed holdout and quarterly expanding-window forecasts use the
            # same test stock-dates wherever every forecast is available.
            walk_common = np.isfinite(actual) & np.isfinite(hist_pred)
            for walk_pred in walkforward_predictions.values():
                walk_common &= np.isfinite(walk_pred)
            walk_n = int(walk_common.sum())
            if walk_n:
                metrics.append(calculate_metrics(actual[walk_common], hist_pred[walk_common], "Last_valid_auction", tag, "walk_forward_common", walk_n))
                for walk_name, walk_pred in walkforward_predictions.items():
                    metric = calculate_metrics(actual[walk_common], walk_pred[walk_common], walk_name, tag, "walk_forward_common", walk_n)
                    counts = walkforward_training_counts.get(walk_name, [])
                    metric["fold_count"] = len(counts)
                    metric["training_n_min"] = min(counts) if counts else 0
                    metric["training_n_max"] = max(counts) if counts else 0
                    metrics.append(metric)
        else:
            actual = pd.to_numeric(test_panel[source], errors="coerce").to_numpy(dtype=float)
            hist_pred = pd.to_numeric(test_panel[f"hist_{tag}_log_last_valid"], errors="coerce").to_numpy(dtype=float)
            ridge_pred = test_preopen_log[tag]
            metrics.append(calculate_metrics(actual, hist_pred, "Last_valid_checkpoint", tag, "test"))
            metrics.append(calculate_metrics(actual, ridge_pred, "Ridge_preopen", tag, "test"))

    # Score all intraday update methods on the same stock-date rows at each cutoff.
    for source, tag in zip(TAQ_CP, CP_TAGS, strict=True):
        cp_actual = pd.to_numeric(test_panel[source], errors="coerce").to_numpy(dtype=float)
        common = (
            np.isfinite(test_actual)
            & np.isfinite(cp_actual)
            & np.isfinite(predictions[f"intraday_{tag}_naive_log"].to_numpy(dtype=float))
            & np.isfinite(predictions[f"intraday_{tag}_direct_log"].to_numpy(dtype=float))
            & np.isfinite(predictions[f"intraday_{tag}_error_correction_log"].to_numpy(dtype=float))
        )
        common_n = int(common.sum())
        actual_common = test_actual[common]
        for model_name in ["naive", "direct", "error_correction"]:
            pred = predictions[f"intraday_{tag}_{model_name}_log"].to_numpy(dtype=float)[common]
            metrics.append(calculate_metrics(actual_common, pred, model_name, f"auction_at_{tag}", "test_common", common_n))

    metrics_frame = pd.DataFrame(metrics)
    if "training_n" not in metrics_frame.columns:
        metrics_frame["training_n"] = np.nan
    else:
        metrics_frame["training_n"] = metrics_frame["training_n"].fillna(
            metrics_frame["model"].map(tree_training_counts)
        )
    metrics_frame["test_start"] = pd.Timestamp(test_panel["date"].min()).strftime("%Y-%m-%d")
    metrics_frame["test_end"] = pd.Timestamp(test_panel["date"].max()).strftime("%Y-%m-%d")
    metrics_frame["train_end"] = train_end_date.strftime("%Y-%m-%d")
    metrics_frame["validation_end"] = val_end_date.strftime("%Y-%m-%d")
    metrics_out = output_dir / "nyse_model_metrics.csv"
    metrics_frame.to_csv(metrics_out, index=False, float_format="%.8g")

    predictions_out = output_dir / "nyse_test_predictions.csv.gz"
    predictions.to_csv(predictions_out, index=False, compression={"method": "gzip", "compresslevel": 1, "mtime": 0}, float_format="%.8g")

    audit_frame = pd.DataFrame(audit)
    audit_out = output_dir / "nyse_data_audit.csv"
    audit_frame.to_csv(audit_out, index=False)

    print("\nTest-set metrics (log RMSE; shares MAE):", flush=True)
    show = metrics_frame.loc[
        metrics_frame["split"].isin(["test_common", "walk_forward_common"]) & metrics_frame["target"].eq("auction"),
        [column for column in ["split", "target", "model", "n", "training_n", "fold_count", "training_n_min", "training_n_max", "rmse_log", "mae_shares"] if column in metrics_frame.columns],
    ]
    print(show.to_string(index=False), flush=True)
    print(f"\nWrote:\n  {panel_out}\n  {predictions_out}\n  {metrics_out}\n  {audit_out}", flush=True)
    print(f"Rows: panel={len(panel):,}; test predictions={len(predictions):,}; audit records={len(audit_frame):,}", flush=True)


if __name__ == "__main__":
    main()
