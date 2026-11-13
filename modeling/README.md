# NYSE Modeling

This folder contains the current NYSE closing-auction prediction experiment. It is separate from the earlier three-market EDA in `data_processing/`.

## Files

- `prepare_nyse_model.py` builds the NYSE stock-day panel, applies the point-in-time feature rules, and runs the pre-open and intraday model comparisons.
- `../output/jupyter-notebook/nyse_model_results.ipynb` shows the executed analysis, diagnostics, tables, and figures.
- `../results/nyse/nyse_model_metrics.csv` contains compact model metrics only.
- `../docs/NYSE_MODEL_RESULTS_REPORT.md` explains the sample, features, model methods, results, and limitations in Chinese.

## Reproduce the run

The repository intentionally does not include Bloomberg or WRDS source files, the large processed panel, or row-level test predictions. To run the script, place the existing local data under a project directory with this layout:

```text
<project-dir>/
  Data/
    raw/exchanges/market_assignments.csv
    raw/exchanges/NYSE/WRDS_TAQ/days/*.csv.gz
    raw/exchanges/NYSE/Bloomberg/daily/*.csv
    reference/us_index_rebalance_calendar.csv
```

Install Python with `numpy`, `pandas`, and `scikit-learn`. `xgboost`, `lightgbm`, and `catboost` are optional model dependencies; the script records and skips a model when its package is unavailable.

From the repository root, run:

```bash
python modeling/prepare_nyse_model.py --project-dir /path/to/project-data
```

The script reads the supplied files without changing them and writes its generated panel, test predictions, metrics, and data-quality audit to `<project-dir>/Data/processed/nyse/`. The panel, predictions, and audit are intentionally kept out of GitHub. After generating these local outputs, run the Notebook to refresh its analysis and figures.

## Current scope

The target is the WRDS TAQ condition-6 closing-auction print volume. The pre-open model uses 84 historical and calendar features; the intraday comparisons use the actual cumulative TAQ checkpoint available at each time. The current universe is based on the available Bloomberg assignment list, so historical membership and split-adjustment conventions remain limitations described in the report.
