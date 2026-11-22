"""Versions, market codes and fixed conventions shared by every stage."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# Versions
RAW_VERSION = 'wrds-v1'          # raw partition metadata + query_version; bumping forces re-download
PROCESS_VERSION = 'wrds-process-v3'  # processing rules; bumping only rebuilds processed outputs
MARKETS = {'NYSE': '11', 'NASDAQ': '14'}

# TAQ conventions: intraday checkpoints (New York time) and closing-cross venues
CHECKPOINTS = ["10:00", "12:00", "14:00", "15:00", "15:30", "15:50"]
CROSS_VENUES = ["N", "Q", "P", "A", "Z"]

# Research universe and listing-venue label
COMMON_STOCK_TPCI = '0'
VENUE_COLUMNS = ['xvol_n', 'xvol_q', 'xvol_p', 'xvol_a', 'xvol_z']
LISTING_VENUE = {'NYSE': ('xvol_n', 'N'), 'NASDAQ': ('xvol_q', 'Q')}
CHECKPOINT_COLUMNS = ['cumvol_10:00', 'cumvol_12:00', 'cumvol_14:00', 'cumvol_15:00', 'cumvol_15:30', 'cumvol_15:50']


class PipelineError(RuntimeError):
    """A safe, credential-free public error."""
