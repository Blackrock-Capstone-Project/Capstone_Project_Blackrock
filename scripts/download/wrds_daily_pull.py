#!/usr/bin/env python3
"""Download US daily data from WRDS, without a Bloomberg dependency.

Use `inspect` first to verify entitlements, columns and source freshness. `pull`
writes monthly raw partitions with atomic completion metadata; `audit` checks
every requested NYSE session and reports TAQ symbol-day matches separately.
Source fields and units are preserved, not renamed to Bloomberg fields.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "Data/raw/wrds/daily"
REQUIRED = {"permno", "dlycaldt", "ticker", "primaryexch", "dlyprc",
            "dlyvol", "dlycap", "dlycumfacpr", "dlycumfacshr"}
OPTIONAL = {"permco", "cusip", "hdrcusip", "dlyopen", "dlyhigh", "dlylow",
            "dlyclose", "dlyprcflg", "dlyret", "dlyretx", "dlyretmissflg",
            "dlycapflg", "dlyprcvol", "shrout", "securitytype", "securitysubtype",
            "sharetype", "issuertype", "usincflg", "conditionaltype",
            "tradingstatusflg", "exchangetier", "tradingsymbol"}
TABLES = ("crsp_m_stock.dsf_v2", "crsp_q_stock.dsf_v2",
          "crsp_a_stock.dsf_v2", "crsp.dsf_v2", "comp_na_daily_all.secd")
COMP_REQUIRED = {"gvkey", "iid", "datadate", "tic", "cusip", "curcdd",
                 "prccd", "prcod", "prchd", "prcld", "cshtrd", "cshoc", "ajexdi"}


def is_comp(table):
    return table.endswith(".secd")


def log(message):
    print(message, flush=True)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, default=str) + "\n")
    os.replace(tmp, path)


def connect(args):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.data_pipeline.auth import connect as runtime_connect
    return runtime_connect(username=getattr(args, 'user', None),
        host=args.host, port=args.port, connect_timeout=args.connect_timeout,
        use_pgpass=getattr(args, 'use_pgpass', False))


def table_name(value):
    if not re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*", value):
        raise ValueError("Expected a schema.table identifier")
    return value


def query(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return pd.DataFrame(cur.fetchall(), columns=[x[0] for x in cur.description])


def inspect_sources(conn, tables):
    reports = []
    for table in tables:
        table_name(table)
        report = {"table": table}
        try:
            # SELECT ... LIMIT 0 works for views too, and proves SELECT permission.
            cols = set(query(conn, f"SELECT * FROM {table} LIMIT 0").columns)
            report["columns"] = sorted(cols)
            required = COMP_REQUIRED if is_comp(table) else REQUIRED
            report["missing_required_columns"] = sorted(required - cols)
            if not required - cols:
                date = "datadate" if is_comp(table) else "dlycaldt"
                bounds = query(conn, f"SELECT min({date}) AS first_date, "
                               f"max({date}) AS last_date FROM {table}").iloc[0]
                report.update(bounds.to_dict())
            report["accessible"] = True
        except Exception as exc:
            conn.rollback()
            report.update(accessible=False, error="Source query failed; verify permissions/schema")
        reports.append(report)
        log(json.dumps(report, default=str))
    return reports


def select_source(reports, start, end):
    for r in reports:
        if (r.get("accessible") and not r.get("missing_required_columns")
                and r.get("first_date") is not None and r.get("last_date") is not None
                and str(r["first_date"])[:10] <= start
                and str(r["last_date"])[:10] >= end):
            return r
    raise RuntimeError("No accessible CRSP CIZ daily table covers the requested interval. "
                       "See source_inventory.json; incomplete sources are not silently accepted.")


def months(start, end):
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    while first <= last:
        nxt = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        stop = min(last, nxt - dt.timedelta(days=1))
        yield first, stop
        first = nxt


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def partition_valid(path, spec):
    meta_path = path.with_suffix(path.suffix + ".json")
    if not path.exists() or not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text())
        return meta.get("request") == spec and meta.get("sha256") == digest(path)
    except (ValueError, OSError):
        return False


def validate_frame(df, start, end):
    if df.empty:
        raise ValueError(f"Empty daily partition: {start}..{end}")
    comp = "gvkey" in df
    required = COMP_REQUIRED if comp else REQUIRED
    date = "datadate" if comp else "dlycaldt"
    key = ["gvkey", "iid", date] if comp else ["permno", date]
    if not required.issubset(df):
        raise ValueError("Missing required daily columns")
    dates = pd.to_datetime(df[date], errors="raise").dt.strftime("%Y-%m-%d")
    if not dates.between(start, end).all():
        raise ValueError("Dates outside requested partition")
    if df[key].isna().any().any() or df.duplicated(key).any():
        raise ValueError("Missing or duplicate permanent security/date keys")
    venue_ok = (df.historical_exchg.astype(str).isin(["11", "14"]) if comp
                else df.primaryexch.isin(["N", "Q"]))
    if not venue_ok.all():
        raise ValueError("Unexpected primary exchange")
    # Missing values remain missing. CRSP price-source and missing-return flags
    # are retained so a subsequent processing stage can apply explicit policy.
    for col in (["prccd", "cshtrd", "cshoc", "ajexdi"] if comp else ["dlyprc", "dlyvol", "dlycap"]):
        values = pd.to_numeric(df[col], errors="raise")
        if (values.dropna() < 0).any():
            raise ValueError(f"Negative CIZ {col}: inspect source semantics")
    return dates


def pull(conn, args, source):
    comp = is_comp(source["table"])
    cols = source["columns"] if comp else sorted((REQUIRED | OPTIONAL) & set(source["columns"]))
    partitions = args.out / "months"
    partitions.mkdir(parents=True, exist_ok=True)
    for first, last in months(args.start, args.end):
        start, end = str(first), str(last)
        path = partitions / f"{first:%Y%m}.csv.gz"
        spec = dict(table=source["table"], start=start, end=end,
                    exchanges=["11", "14"] if comp else ["N", "Q"], columns=cols,
                    historical_identifiers=comp, currency="USD" if comp else "source")
        if not args.refresh and partition_valid(path, spec):
            log(f"Validated checkpoint, skip {path.name}")
            continue
        if comp:
            schema = source["table"].split(".")[0]
            sql = (f"SELECT {', '.join('d.' + c for c in cols)}, "
                   "e.itemvalue AS historical_exchg, t.itemvalue AS historical_tic "
                   f"FROM {source['table']} d JOIN {schema}.sec_idhist e "
                   "ON d.gvkey=e.gvkey AND d.iid=e.iid AND e.item='EXCHG' "
                   "AND e.itemvalue IN ('11','14') AND d.datadate>=e.efffrom "
                   "AND (e.effthru IS NULL OR d.datadate<=e.effthru) "
                   f"LEFT JOIN {schema}.sec_idhist t ON d.gvkey=t.gvkey AND d.iid=t.iid "
                   "AND t.item='TIC' AND d.datadate>=t.efffrom "
                   "AND (t.effthru IS NULL OR d.datadate<=t.effthru) "
                   "WHERE d.datadate BETWEEN %s AND %s AND d.curcdd='USD' "
                   "ORDER BY d.gvkey, d.iid, d.datadate")
        else:
            sql = (f"SELECT {', '.join(cols)} FROM {source['table']} "
                   "WHERE dlycaldt BETWEEN %s AND %s AND primaryexch IN ('N','Q') "
                   "ORDER BY permno, dlycaldt")
        log(f"Downloading {start}..{end} from {source['table']}")
        df = query(conn, sql, (start, end))
        validate_frame(df, start, end)
        tmp = path.with_name(path.name + ".tmp")
        df.to_csv(tmp, index=False, compression="gzip")
        os.replace(tmp, path)
        save_json(path.with_suffix(path.suffix + ".json"), dict(
            request=spec, rows=len(df), sha256=digest(path),
            downloaded_at_utc=dt.datetime.now(dt.timezone.utc).isoformat()))
        log(f"Saved {path.name}: {len(df):,} security-days")


def sessions(start, end):
    import exchange_calendars as xc
    return set(xc.get_calendar("XNYS").sessions_in_range(start, end).strftime("%Y-%m-%d"))


def compustat_symbols(symbols, types):
    """Keep class separators: T.PC (preferred) must not collide with common TPC."""
    out = symbols.str.strip().str.upper()
    preferred = types.isin(["1", "G", "S"])
    out.loc[preferred] = out.loc[preferred].str.replace(r"\.P([A-Z]+)$", r"/PR\1", regex=True)
    return out.str.replace(".", "/", regex=False)


def audit(args):
    expected = sessions(args.start, args.end)
    frames, missing_partitions = [], []
    for first, last in months(args.start, args.end):
        path = args.out / "months" / f"{first:%Y%m}.csv.gz"
        if not path.exists():
            missing_partitions.append(path.name)
            continue
        meta_path = path.with_suffix(path.suffix + ".json")
        if not meta_path.exists():
            raise ValueError(f"Missing completion metadata: {path}")
        meta = json.loads(meta_path.read_text())
        if not partition_valid(path, meta["request"]):
            raise ValueError(f"Corrupt checkpoint: {path}")
        df = pd.read_csv(path, keep_default_na=False, na_values=[""], dtype={"ticker": str, "cusip": str, "hdrcusip": str,
                                     "gvkey": str, "iid": str, "historical_exchg": str, "tpci": str}, low_memory=False)
        if len(df) != meta["rows"]:
            raise ValueError(f"Row count differs from checkpoint: {path}")
        validate_frame(df, meta["request"]["start"], meta["request"]["end"])
        comp = "gvkey" in df
        date = "datadate" if comp else "dlycaldt"
        df = df[df[date].between(str(first), str(last))].copy()
        df["security_id"] = df.gvkey + ":" + df.iid if comp else df.permno.astype(str)
        df["date"] = df[date]
        df["historical_symbol"] = df.historical_tic if comp else df.ticker
        audit_cols = ["security_id", "date", "historical_symbol"] + (["tpci"] if comp else []) + sorted(COMP_REQUIRED if comp else REQUIRED)
        # Keep only audit columns in memory; raw dividends and metadata remain on disk.
        frames.append(df[list(dict.fromkeys(audit_cols))])
    report = dict(start=args.start, end=args.end, expected_sessions=len(expected),
                  missing_partitions=missing_partitions, downloaded_rows=0,
                  complete=False, source="WRDS only; no Bloomberg inputs")
    if frames:
        daily = pd.concat(frames, ignore_index=True)
        comp = "gvkey" in daily
        dates = set(daily.date)
        duplicates = int(daily.duplicated(["security_id", "date"]).sum())
        report.update(downloaded_rows=len(daily), securities=int(daily.security_id.nunique()),
                      first_date=daily.date.min(), last_date=daily.date.max(),
                      observed_sessions=len(dates), missing_sessions=sorted(expected-dates),
                      unexpected_sessions=sorted(dates-expected), duplicate_keys=duplicates,
                      missing_fields={c: int(daily[c].isna().sum()) for c in sorted(COMP_REQUIRED if comp else REQUIRED)})
        report["missing_historical_symbol_rows"] = int(daily.historical_symbol.isna().sum())
        report["complete"] = not (missing_partitions or expected-dates or dates-expected or duplicates)
        daily.groupby("date").agg(rows=("security_id", "size"), securities=("security_id", "nunique")).to_csv(
            args.out / "daily_date_coverage.csv")
        security_coverage = daily.groupby("security_id").date.agg(["min", "max", "nunique"])
        positions = {d: i for i, d in enumerate(sorted(expected))}
        security_coverage["sessions_between_first_last"] = (
            security_coverage["max"].map(positions) - security_coverage["min"].map(positions) + 1)
        security_coverage["internal_missing_sessions"] = (
            security_coverage.sessions_between_first_last - security_coverage["nunique"])
        security_coverage.to_csv(args.out / "security_date_coverage.csv")
        report["securities_with_internal_gaps"] = int((security_coverage.internal_missing_sessions > 0).sum())
        report["internal_missing_security_sessions"] = int(security_coverage.internal_missing_sessions.sum())
        # Tickers are date-specific here, unlike a current Bloomberg universe.
        # Matching ambiguity is reported rather than resolved arbitrarily.
        if args.taq.exists():
            keys = daily[["date", "historical_symbol", "security_id"]].rename(
                columns={"historical_symbol": "symbol_key"})
            keys["symbol_key"] = (compustat_symbols(keys.symbol_key, daily.tpci) if comp
                                  else keys.symbol_key.str.strip().str.upper())
            counts = keys.groupby(["date", "symbol_key"]).security_id.nunique().rename("daily_matches").reset_index()
            # Nasdaq single-letter class suffixes are sometimes concatenated in
            # Compustat (GOOGL vs TAQ GOOG/L). Use this alias only after exact
            # matching fails, and still count ambiguity rather than choosing.
            aliases = counts.rename(columns={"symbol_key": "alias_key", "daily_matches": "alias_matches"})
            coverage, unmatched = [], []
            for chunk in pd.read_csv(args.taq, usecols=["date", "taq_symbol", "listing_ex", "auction_volume"],
                                     chunksize=500_000, keep_default_na=False, na_values=[""]):
                chunk = chunk[chunk.date.between(args.start, args.end) & chunk.listing_ex.isin(["N", "Q"])]
                chunk["symbol_key"] = chunk.taq_symbol.str.strip().str.upper()
                matched = chunk.merge(counts, on=["date", "symbol_key"], how="left", validate="many_to_one")
                matched["alias_key"] = matched.symbol_key.str.replace("/", "", regex=False)
                matched = matched.merge(aliases, on=["date", "alias_key"], how="left", validate="many_to_one")
                use_alias = matched.daily_matches.isna() & matched.symbol_key.str.match(r"^[^/]+/[A-Z]$")
                matched.loc[use_alias, "daily_matches"] = matched.loc[use_alias, "alias_matches"]
                matched["match_status"] = matched.daily_matches.map(
                    lambda n: "unique_symbol_match" if n == 1 else "ambiguous" if pd.notna(n) else "unmatched")
                coverage.append(matched.groupby(["date", "match_status"]).size().rename("rows"))
                unmatched.append(matched.loc[matched.match_status.ne("unique_symbol_match"),
                                             ["date", "taq_symbol", "match_status"]])
            if coverage:
                by_date = pd.concat(coverage).groupby(level=[0, 1]).sum().unstack(fill_value=0)
                by_date.to_csv(args.out / "taq_daily_match_by_date.csv")
                report["taq_symbol_day_matching"] = {k: int(v) for k, v in by_date.sum().items()}
                pd.concat(unmatched, ignore_index=True).to_csv(args.out / "taq_unmatched.csv.gz", index=False)
        report["scope_note"] = ("Complete means all requested sessions and partitions, not every stock on every day. "
                                "IPOs, delistings, security types, missing fields and ambiguous symbol links require review. "
                                "TAQ symbol matching is diagnostic, not a validated PERMNO link.")
    else:
        report["missing_sessions"] = sorted(expected)
    save_json(args.out / "completeness.json", report)
    log(json.dumps(report, indent=2))
    return report["complete"]


def main():
    # Compatibility command; all production downloads use the same service.
    sys.path.insert(0, str(ROOT))
    from scripts.data_pipeline.__main__ import main as pipeline_main
    argv = sys.argv[1:]
    if argv and argv[0] == 'pull':
        argv[0] = 'update'
    elif argv and argv[0] == 'inspect':
        from scripts.download.wrds_taq_check import main as inspect_main
        return inspect_main()
    return pipeline_main(argv)


if __name__ == '__main__':
    sys.exit(main())
