# Equity Closing Auction Prediction

This repository keeps the earlier three-market data exploration and the current NYSE modeling experiment in separate folders.

## Current NYSE experiment

- **Model code:** [`modeling/prepare_nyse_model.py`](modeling/prepare_nyse_model.py)
- **Executed Notebook and figures:** [`output/jupyter-notebook/nyse_model_results.ipynb`](output/jupyter-notebook/nyse_model_results.ipynb)
- **Chinese results report:** [`docs/NYSE_MODEL_RESULTS_REPORT.md`](docs/NYSE_MODEL_RESULTS_REPORT.md)
- **Compact model metrics:** [`results/nyse/nyse_model_metrics.csv`](results/nyse/nyse_model_metrics.csv)
- **Run instructions:** [`modeling/README.md`](modeling/README.md)

The experiment predicts the final NYSE closing-auction share volume. It compares a recent-volume baseline, Ridge and tree-based pre-open models, quarterly expanding-window refits, K-means grouping, and three intraday update methods.

## Earlier three-market EDA

[`data_processing/`](data_processing/) contains the earlier Bloomberg/TAQ market partition script and exploratory Notebook/report for NYSE, Nasdaq, and LSE. Those files are retained as historical work; the current prediction results are documented separately above.

## Data policy

Raw Bloomberg and WRDS files are not included. The large processed panel, ticker-day test predictions, and audit rows are also excluded. Only code, reports, the executed Notebook, and the compact aggregated model-metrics table are committed. See `modeling/README.md` for the local input layout and reproduction command.
