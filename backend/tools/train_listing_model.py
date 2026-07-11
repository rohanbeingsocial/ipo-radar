"""Distill the IPO hype-cycle backtest into a linear (ridge) model.

Usage:
    python tools/train_listing_model.py <actuals.json> <id_map.json>

actuals.json — output of the price-history script (list of dicts with name,
listing_open_premium_pct, bottom.session, bottom.drawdown_vs_offer_pct).
id_map.json  — {"analysis_id": "actuals name", ...}

Trains one ridge regression per target with leave-one-out cross-validation and
writes app/listing_model.json, which listing_predictor.ml_forecast() serves.

Honesty note: with a two-digit sample this is a scaffold, not a model you
should trust — the LOO MAE vs the predict-the-mean baseline is printed so the
(lack of) skill is visible. Retrain as more listed IPOs are analyzed.
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.pipeline.listing_predictor import features_from_report, _ml_vector  # noqa: E402

FEATURES = ["bias", "overall_score", "risk_score", "ofs_share",
            "issue_size_cr_log", "overvalued", "forensic_flag_count"]
TARGETS = {
    "listing_open_premium_pct": lambda a: a["listing_open_premium_pct"],
    "bottom_session": lambda a: a["bottom"]["session"],
    "bottom_drawdown_vs_offer_pct": lambda a: a["bottom"]["drawdown_vs_offer_pct"],
}
LAMBDA = 0.5


def solve(A, b):
    """Gaussian elimination for the k×k normal equations (pure python)."""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        M[col], M[piv] = M[piv], M[col]
        d = M[col][col] or 1e-12
        M[col] = [v / d for v in M[col]]
        for r in range(n):
            if r != col and M[r][col]:
                f = M[r][col]
                M[r] = [v - f * M[col][i] for i, v in enumerate(M[r])]
    return [M[i][n] for i in range(n)]


def ridge(X, y):
    k = len(X[0])
    A = [[sum(X[r][i] * X[r][j] for r in range(len(X))) + (LAMBDA if i == j and i > 0 else 0)
          for j in range(k)] for i in range(k)]
    b = [sum(X[r][i] * y[r] for r in range(len(X))) for i in range(k)]
    return solve(A, b)


def main():
    actuals_path, idmap_path = sys.argv[1], sys.argv[2]
    actuals = {a["name"]: a for a in json.load(open(actuals_path)) if not a.get("error")}
    idmap = json.load(open(idmap_path))

    X, Y, names = [], {t: [] for t in TARGETS}, []
    for aid, name in idmap.items():
        if name not in actuals:
            continue
        with urllib.request.urlopen(f"http://localhost:8001/api/analyses/{aid}/report", timeout=60) as r:
            report = json.load(r)
        f = features_from_report(report)
        X.append(_ml_vector(f, FEATURES))
        names.append(name)
        for t, get in TARGETS.items():
            Y[t].append(float(get(actuals[name])))

    n = len(X)
    model = {"feature_names": FEATURES, "n": n, "lambda": LAMBDA,
             "targets": {}, "loo_mae": {}, "baseline_mae": {}}
    for t in TARGETS:
        y = Y[t]
        model["targets"][t] = [round(w, 4) for w in ridge(X, y)]
        errs, base_errs = [], []
        for i in range(n):
            Xt = X[:i] + X[i + 1:]
            yt = y[:i] + y[i + 1:]
            w = ridge(Xt, yt)
            pred = sum(c * v for c, v in zip(w, X[i]))
            errs.append(abs(pred - y[i]))
            base_errs.append(abs(sum(yt) / len(yt) - y[i]))
        model["loo_mae"][t] = round(sum(errs) / n, 1)
        model["baseline_mae"][t] = round(sum(base_errs) / n, 1)
        print(f"{t:32} LOO-MAE {model['loo_mae'][t]:>7}  baseline(mean) {model['baseline_mae'][t]:>7}")

    out = Path(__file__).resolve().parent.parent / "app" / "listing_model.json"
    out.write_text(json.dumps(model, indent=1))
    print(f"n={n} → {out}")


if __name__ == "__main__":
    main()
