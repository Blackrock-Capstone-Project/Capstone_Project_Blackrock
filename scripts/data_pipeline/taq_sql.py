"""TAQ server-side aggregation; original auction convention, calendar closes."""
import datetime as dt
from .common import close_time
CHECKPOINTS = ["10:00", "12:00", "14:00", "15:00", "15:30", "15:50"]
CROSS_VENUES = ["N", "Q", "P", "A", "Z"]

def shift(hms, minutes):
    return (dt.datetime.strptime(hms, "%H:%M:%S") + dt.timedelta(minutes=minutes)).strftime("%H:%M:%S")


def day_sql(d):
    iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    c = close_time(iso)
    lo, hi, trf_hi = shift(c, -1), shift(c, 5), shift(c, 30)
    tbl = f"taqm_{d[:4]}.ctm_{d}"
    is_cross = f"(time_m BETWEEN '{lo}' AND '{hi}' AND tr_scond LIKE '%%6%%')"
    xcols = ",\n            ".join(
        f"SUM(CASE WHEN {is_cross} AND ex = '{e}' THEN size END) AS xvol_{e.lower()}" for e in CROSS_VENUES)
    cps = ",\n            ".join(
        f"SUM(CASE WHEN time_m < '{cp}:00' THEN size ELSE 0 END) AS \"cumvol_{cp}\""
        for cp in CHECKPOINTS if f"{cp}:00" <= c)
    return f"""
    WITH agg AS (
        SELECT sym_root, COALESCE(sym_suffix, '') AS sym_suffix,
            {cps},
            SUM(CASE WHEN time_m < '{c}' THEN size ELSE 0 END)        AS continuous_volume,
            SUM(CASE WHEN {is_cross} THEN size END)                    AS auction_volume_all,
            {xcols},
            MAX(CASE WHEN {is_cross} THEN price END)                   AS auction_price,
            COUNT(CASE WHEN {is_cross} THEN 1 END)                     AS n_prints,
            MIN(CASE WHEN {is_cross} THEN ex END)                      AS listing_ex
        FROM {tbl}
        WHERE tr_corr = '00' AND time_m >= '09:30:00' AND time_m <= '{hi}'
        GROUP BY sym_root, COALESCE(sym_suffix, '')
    ),
    trf AS (
        SELECT t.sym_root, COALESCE(t.sym_suffix, '') AS sym_suffix, SUM(t.size) AS trf_close_volume
        FROM {tbl} t
        JOIN agg a ON a.sym_root = t.sym_root AND a.sym_suffix = COALESCE(t.sym_suffix, '')
        WHERE t.ex = 'D' AND t.tr_corr = '00'
          AND t.time_m BETWEEN '{c}' AND '{trf_hi}'
          AND t.price = a.auction_price
        GROUP BY t.sym_root, COALESCE(t.sym_suffix, '')
    )
    SELECT a.*, COALESCE(f.trf_close_volume, 0) AS trf_close_volume
    FROM agg a LEFT JOIN trf f USING (sym_root, sym_suffix)
    """


def finalize(df):
    """auction_volume = the cross printed by the venue with the largest cross (the listing exchange);
    small '6' prints reported by other venues are ignored (kept in auction_volume_all for reference)."""
    xv = df[[f"xvol_{e.lower()}" for e in CROSS_VENUES]].astype(float)
    has = xv.notna().any(axis=1)
    df["listing_ex"] = None
    df.loc[has, "listing_ex"] = xv[has].idxmax(axis=1).str[-1].str.upper()
    df["auction_volume"] = xv.max(axis=1)
    return df

