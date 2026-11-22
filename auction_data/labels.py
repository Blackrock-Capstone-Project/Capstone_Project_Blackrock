"""Listing-venue closing-cross label and venue reconciliation."""
import numpy as np
import pandas as pd

from .settings import LISTING_VENUE, VENUE_COLUMNS


def label_columns(grid):
    """Listing-venue closing-cross label; never falls back to another venue or the largest cross."""
    g = grid
    for c in VENUE_COLUMNS + ['auction_volume_all']:
        if c not in g:
            g[c] = np.nan
    xv = g[VENUE_COLUMNS].apply(pd.to_numeric, errors='coerce')
    venue = g.market.map({m: v[0] for m, v in LISTING_VENUE.items()})
    letter = g.market.map({m: v[1] for m, v in LISTING_VENUE.items()})
    label = pd.Series(np.nan, index=g.index)
    for col in ['xvol_n', 'xvol_q']:
        rows = venue.eq(col)
        label.loc[rows] = xv.loc[rows, col]
    g['label'] = label
    any_venue = xv.notna().any(axis=1)
    has_record = g.taq_count.notna() if 'taq_count' in g else pd.Series(True, index=g.index)
    outside = ~any_venue & pd.to_numeric(g.auction_volume_all, errors='coerce').gt(0)
    g['label_status'] = np.select([~has_record, label.notna(), outside],
                                  ['no_taq_record', 'ok', 'cross_outside_known_venues'],
                                  default='listing_venue_cross_missing')
    other = any_venue & label.isna()
    largest = g.listing_ex if 'listing_ex' in g else pd.Series('', index=g.index)
    conflict = other | (label.notna() & largest.notna() & largest.ne(letter))
    g['venue_reconciliation'] = np.select(
        [g.match_status.isin(['ambiguous', 'missing_identity']), conflict, label.notna()],
        ['identity_unresolved', 'venue_conflict', 'consistent'], default='listing_cross_missing')
    return g
