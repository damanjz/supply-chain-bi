"""Stockout risk model: the chance a product runs out at a centre in the next 14 days, against the reorder-point rule.

Reads stockout_snapshots from DuckDB and writes back:
model_metrics, model_calibration, model_importance, stockout_scores.

Validation is out-of-time: train on snapshots up to 30 September 2024, test on snapshots from 1 April 2025.
The test year includes the festive pre-build, a policy the model never saw in training. The chosen model is
refit on every labelled snapshot and scores all snapshots, including the last two weeks, which have no outcome
yet: that is the current watch list.
"""
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

SEED = 7
TRAIN_END = "2024-09-30"
TEST_START = "2025-04-01"


def make_models(numeric, categorical):
    def prep(scale):
        num = [("scale", StandardScaler())] if scale else [("pass", "passthrough")]
        return ColumnTransformer([("num", Pipeline(num), numeric),
                                  ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), categorical)])
    return {
        "Logistic regression": Pipeline([("prep", prep(True)), ("clf", LogisticRegression(max_iter=3000, C=0.5))]),
        "Gradient boosting": Pipeline([("prep", prep(False)),
                                       ("clf", HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05,
                                                                              max_iter=300, random_state=SEED))]),
    }


def capture_at(y, p, share):
    """Share of stockouts among the top `share` of scores (ties broken by row order, which is fixed)."""
    flagged = np.argsort(-p, kind="stable")[: int(round(share * len(p)))]
    return y[flagged].sum() / max(y.sum(), 1)


def run(con):
    feats = con.sql("SELECT feature, kind FROM model_features").df()
    numeric = feats[feats.kind == "numeric"].feature.tolist()
    categorical = feats[feats.kind == "categorical"].feature.tolist()
    cols = numeric + categorical

    # fixed row order: the gradient-boosting validation split follows it
    d = con.sql("SELECT * FROM stockout_snapshots ORDER BY snapshot_date, dc_id, sku").df()
    d["snapshot_date"] = pd.to_datetime(d.snapshot_date)
    d[numeric] = d[numeric].fillna(0)
    labelled = d[d.outcome_known]
    train = labelled[labelled.snapshot_date <= TRAIN_END]
    test = labelled[labelled.snapshot_date >= TEST_START]
    y_test = test.stockout_14d.to_numpy().astype(int)
    rule = test.below_reorder_point.to_numpy().astype(float)
    rule_share = rule.mean()

    metrics, preds = [], {}
    for name, model in make_models(numeric, categorical).items():
        model.fit(train[cols], train.stockout_14d.astype(int))
        p = model.predict_proba(test[cols])[:, 1]
        preds[name] = (model, p)
        metrics.append(dict(model=name, roc_auc=roc_auc_score(y_test, p), pr_auc=average_precision_score(y_test, p),
                            brier=brier_score_loss(y_test, p), capture_at_rule_share=capture_at(y_test, p, rule_share)))
    metrics.append(dict(model="Reorder-point rule", roc_auc=roc_auc_score(y_test, rule),
                        pr_auc=average_precision_score(y_test, rule), brier=None,
                        capture_at_rule_share=y_test[rule == 1].sum() / max(y_test.sum(), 1)))
    metrics = pd.DataFrame(metrics)
    ml = metrics[metrics.model != "Reorder-point rule"]
    lr_auc = ml.loc[ml.model == "Logistic regression", "roc_auc"].item()
    # prefer the interpretable model unless the other is clearly better
    chosen = "Logistic regression" if ml.roc_auc.max() - lr_auc < 0.01 else ml.loc[ml.roc_auc.idxmax(), "model"]
    metrics["chosen"] = metrics.model == chosen
    metrics["base_rate"] = y_test.mean()
    metrics["rule_share"] = rule_share
    metrics["train_rows"], metrics["test_rows"] = len(train), len(test)
    metrics["train_end"], metrics["test_start"] = TRAIN_END, TEST_START

    model, p_test = preds[chosen]
    bins = pd.qcut(p_test, 10, labels=False, duplicates="drop")
    calib = pd.DataFrame({"decile": bins + 1, "predicted": p_test, "actual": y_test}) \
        .groupby("decile").agg(predicted=("predicted", "mean"), actual=("actual", "mean"),
                               snapshots=("actual", "size")).reset_index()
    imp = permutation_importance(model, test[cols], y_test, scoring="roc_auc", n_repeats=5, random_state=SEED)
    importance = pd.DataFrame({"feature": cols, "importance": imp.importances_mean, "std": imp.importances_std}) \
        .sort_values("importance", ascending=False)

    # refit on every labelled snapshot, then score every snapshot
    final = make_models(numeric, categorical)[chosen].fit(labelled[cols], labelled.stockout_14d.astype(int))
    d["risk_score"] = final.predict_proba(d[cols])[:, 1]
    d["risk_band"] = np.where(d.risk_score >= 0.5, "High", np.where(d.risk_score >= 0.2, "Watch", "Low"))
    d["reasons"] = reasons(final, d[cols], labelled)
    scores = d[["snapshot_date", "dc_id", "sku", "risk_score", "risk_band", "reasons"]].assign(
        snapshot_date=d.snapshot_date.dt.date)

    for name, df in dict(model_metrics=metrics, model_calibration=calib, model_importance=importance,
                         stockout_scores=scores).items():
        con.register("tmp_df", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM tmp_df")
        con.unregister("tmp_df")
    return metrics


# Each reason resets one factor to a typical value and measures how far the risk falls.
REASONS = {"cover_days": ("low stock cover", "median"), "pipeline_cover_days": ("little on order", "median"),
           "supplier_on_time_180d": ("unreliable supplier", "median"), "supplier_lead_sd_180d": ("erratic supplier lead time", "median"),
           "demand_trend": ("demand rising", 1.0), "days_to_diwali": ("festive season ahead", 60)}


def reasons(model, X, history):
    base = model.predict_proba(X)[:, 1]
    drops = {}
    for col, (label, ref) in REASONS.items():
        X2 = X.copy()
        X2[col] = history[col].median() if ref == "median" else ref
        drops[label] = base - model.predict_proba(X2)[:, 1]
    d = pd.DataFrame(drops)
    out = []
    for _, row in d.iterrows():
        top = row[row > 0.02].sort_values(ascending=False).index[:2]
        out.append(", ".join(top))
    return out
