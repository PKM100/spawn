import math


def evaluate(records: list[dict]) -> dict:
    """Score held-out forecasts; this function never fits the forecasting policy."""
    if not records:
        raise ValueError("Supply at least one held-out prediction/outcome record")
    errors = {"cost_usd": [], "seconds": []}
    covered = {"cost_usd": [], "seconds": []}
    probability_pairs = []
    seen = set()
    for row in records:
        if row.get("split") != "test":
            raise ValueError(
                "Evaluation accepts only records explicitly marked split=test"
            )
        run_id = row["run_id"]
        if not isinstance(run_id, str) or not run_id or run_id in seen:
            raise ValueError("Supply unique, nonempty run IDs")
        seen.add(run_id)
        for field in errors:
            interval = row["prediction"][field]
            actual = row["outcome"][field]
            values = [*interval, actual]
            if (
                len(interval) != 2
                or any(
                    isinstance(v, bool)
                    or not isinstance(v, (int, float))
                    or not math.isfinite(v)
                    or v < 0
                    for v in values
                )
                or interval[0] > interval[1]
            ):
                raise ValueError(
                    "Predictions need ordered, nonnegative finite intervals"
                )
            errors[field].append(abs(sum(interval) / 2 - actual))
            covered[field].append(interval[0] <= actual <= interval[1])
        probability = row["prediction"].get("success_probability")
        success = row["outcome"]["success"]
        if type(success) is not bool:
            raise ValueError("Outcome success must be a human-verified boolean")
        if probability is not None:
            if (
                isinstance(probability, bool)
                or not isinstance(probability, (int, float))
                or not math.isfinite(probability)
                or not 0 <= probability <= 1
            ):
                raise ValueError(
                    "success_probability must be null or a number in [0,1]"
                )
            probability_pairs.append((probability, int(success)))
    bins = []
    for index in range(10):
        pairs = [(p, y) for p, y in probability_pairs if min(9, int(p * 10)) == index]
        if pairs:
            bins.append(
                {
                    "bin": [index / 10, (index + 1) / 10],
                    "count": len(pairs),
                    "mean_probability": sum(p for p, _ in pairs) / len(pairs),
                    "success_rate": sum(y for _, y in pairs) / len(pairs),
                }
            )
    count = len(probability_pairs)
    return {
        "records": len(records),
        "probability_records": count,
        "brier_score": (
            sum((p - y) ** 2 for p, y in probability_pairs) / count if count else None
        ),
        "calibration_bins": bins,
        "mean_absolute_error": {
            field: sum(values) / len(values) for field, values in errors.items()
        },
        "observed_interval_coverage": {
            field: sum(values) / len(values) for field, values in covered.items()
        },
        "warning": (
            "Observed coverage is not a guarantee or a calibration certificate. "
            "Do not reuse evaluation outcomes to fit the policy being evaluated. "
            "Independence and label quality must be established by the experimenter."
        ),
    }
