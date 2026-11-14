"""HW17: descriptive stats -> period comparison -> correlations -> Bayesian update.

Daily Telegram metrics for posts about water outages (90 days). The script
prints everything needed for tasks 1-3 and the arithmetic behind task 4.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

BASELINE_START = pd.Timestamp("2026-08-01")
BASELINE_END = pd.Timestamp("2026-09-29")
CAMPAIGN_START = pd.Timestamp("2026-09-30")
CAMPAIGN_END = pd.Timestamp("2026-10-29")

METRICS: tuple[str, ...] = (
    "posts",
    "channels",
    "young_channels",
    "avg_views",
    "forwards_share",
)
REQUIRED_COLUMNS: tuple[str, ...] = ("date", *METRICS)
EXPECTED_DAYS = 90
ALPHA = 0.05
TUKEY_K = 1.5
MIN_WELCH_N = 2.0

# Task 4. The likelihoods are analyst judgement, not estimates: the baseline
# holds only two organic spikes, far too few to estimate P(sign | not H).
PRIOR_H = 0.15
P_A_GIVEN_H = 0.80
# Real outages do spawn new local channels, so A is not impossible without H.
P_A_GIVEN_NOT_H = 0.10
# B treated as if it were independent of A (the naive step).
P_B_GIVEN_H = 0.70
P_B_GIVEN_NOT_H = 0.15
# B once A is already known: young channels repost more under either
# hypothesis, so B adds little beyond A.
P_B_GIVEN_A_H = 0.90
P_B_GIVEN_A_NOT_H = 0.60

PHIA_BANDS: tuple[tuple[float, str], ...] = (
    (0.075, "remote chance (≤5%)"),
    (0.225, "highly unlikely (10–20%)"),
    (0.375, "unlikely (25–35%)"),
    (0.525, "realistic possibility (40–50%)"),
    (0.775, "likely / probable (55–75%)"),
    (0.925, "highly likely (80–90%)"),
    (1.0, "almost certain (≥95%)"),
)


@dataclass(frozen=True)
class BaselineStats:
    mean: float
    median: float
    std: float
    q1: float
    q3: float
    iqr: float
    upper_fence: float


@dataclass(frozen=True)
class WelchResult:
    t: float
    df: float
    p_value: float


def load_data(path: Path) -> pd.DataFrame:
    """Load the CSV and fail loudly if it is not the expected 90-day daily series."""
    df = pd.read_csv(path, parse_dates=["date"])
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing columns: {missing}")
    if df[list(REQUIRED_COLUMNS)].isna().any().any():
        raise ValueError("CSV contains missing values")

    df = df.sort_values("date").reset_index(drop=True)
    expected = pd.date_range(BASELINE_START, CAMPAIGN_END, freq="D")
    if len(df) != EXPECTED_DAYS or not df["date"].equals(pd.Series(expected)):
        raise ValueError(
            f"Expected {EXPECTED_DAYS} consecutive days "
            f"{BASELINE_START.date()}..{CAMPAIGN_END.date()}, got {len(df)} rows"
        )

    df["period"] = np.where(df["date"] <= BASELINE_END, "baseline", "campaign")
    df["young_share"] = df["young_channels"] / df["channels"]
    df["posts_per_channel"] = df["posts"] / df["channels"]
    return df


def baseline_stats(values: pd.Series) -> BaselineStats:
    """Sample statistics (ddof=1) and the Tukey upper fence.

    Quartiles use linear interpolation (numpy/pandas default, "type 7").
    """
    q1 = float(values.quantile(0.25))
    q3 = float(values.quantile(0.75))
    iqr = q3 - q1
    return BaselineStats(
        mean=float(values.mean()),
        median=float(values.median()),
        std=float(values.std(ddof=1)),
        q1=q1,
        q3=q3,
        iqr=iqr,
        upper_fence=q3 + TUKEY_K * iqr,
    )


def classify_spike_days(df: pd.DataFrame, upper_fence: float) -> pd.DataFrame:
    """Days above the fence, labelled by signature rather than by calendar period.

    A day carries the campaign signature when both forwards_share and
    young_channels exceed anything seen in the baseline. Labelling by period
    instead would make the organic/campaign comparison circular.
    """
    baseline = df[df["period"] == "baseline"]
    max_forwards = float(baseline["forwards_share"].max())
    max_young = int(baseline["young_channels"].max())

    spikes = df[df["posts"] > upper_fence].copy()
    is_campaign_like = (spikes["forwards_share"] > max_forwards) & (
        spikes["young_channels"] > max_young
    )
    spikes["signature"] = np.where(is_campaign_like, "campaign", "organic")
    return spikes


def mean_confidence_interval(
    values: pd.Series, confidence: float = 0.95
) -> tuple[float, float]:
    """Student-t confidence interval for the mean."""
    low, high = stats.t.interval(
        confidence,
        df=len(values) - 1,
        loc=float(values.mean()),
        scale=float(stats.sem(values)),
    )
    return float(low), float(high)


def welch_from_moments(
    mean_a: float,
    var_a: float,
    n_a: float,
    mean_b: float,
    var_b: float,
    n_b: float,
) -> WelchResult:
    """Welch's t-test from summary moments; n may be a fractional effective size."""
    se_a = var_a / n_a
    se_b = var_b / n_b
    t_stat = (mean_a - mean_b) / np.sqrt(se_a + se_b)
    dof = (se_a + se_b) ** 2 / (se_a**2 / (n_a - 1) + se_b**2 / (n_b - 1))
    p_value = 2 * stats.t.sf(abs(t_stat), dof)
    return WelchResult(t=float(t_stat), df=float(dof), p_value=float(p_value))


def effective_sample_size(n: int, lag1: float) -> float:
    """AR(1) approximation n_eff = n(1 - r1) / (1 + r1) for the variance of a mean.

    Capped at n: negative autocorrelation is not allowed to claim more
    information than there are observations.
    """
    return float(min(n, n * (1 - lag1) / (1 + lag1)))


def compare_periods(baseline: pd.DataFrame, campaign: pd.DataFrame) -> pd.DataFrame:
    """Per-metric means, differences, Welch and Mann-Whitney p-values, Bonferroni."""
    rows: list[dict[str, object]] = []
    n_tests = len(METRICS)
    for metric in METRICS:
        base, camp = baseline[metric], campaign[metric]
        welch = stats.ttest_ind(camp, base, equal_var=False)
        mwu = stats.mannwhitneyu(camp, base, alternative="two-sided")
        p_welch_adj = min(1.0, float(welch.pvalue) * n_tests)
        p_mwu_adj = min(1.0, float(mwu.pvalue) * n_tests)
        rows.append(
            {
                "metric": metric,
                "mean_base": float(base.mean()),
                "mean_camp": float(camp.mean()),
                "diff": float(camp.mean() - base.mean()),
                "ratio": float(camp.mean() / base.mean()),
                "p_welch": float(welch.pvalue),
                "p_mwu": float(mwu.pvalue),
                "p_welch_bonf": p_welch_adj,
                "p_mwu_bonf": p_mwu_adj,
                "significant": p_welch_adj < ALPHA and p_mwu_adj < ALPHA,
            }
        )
    return pd.DataFrame(rows).set_index("metric")


def autocorrelation_report(
    baseline: pd.Series, campaign: pd.Series
) -> tuple[pd.DataFrame, WelchResult, WelchResult]:
    """Lag-1 autocorrelation, effective sample sizes and the adjusted Welch test."""
    rows: list[dict[str, object]] = []
    effective: dict[str, float] = {}
    for name, series in (("baseline", baseline), ("campaign", campaign)):
        lag1 = float(series.autocorr(lag=1))
        effective[name] = effective_sample_size(len(series), lag1)
        rows.append(
            {"period": name, "n": len(series), "r_lag1": lag1, "n_eff": effective[name]}
        )

    moments = (
        float(campaign.mean()),
        float(campaign.var(ddof=1)),
        float(baseline.mean()),
        float(baseline.var(ddof=1)),
    )
    naive = welch_from_moments(
        moments[0], moments[1], len(campaign), moments[2], moments[3], len(baseline)
    )
    # Welch needs n > 1 on each side; a strongly trending series can fall below.
    adjusted = welch_from_moments(
        moments[0],
        moments[1],
        max(MIN_WELCH_N, effective["campaign"]),
        moments[2],
        moments[3],
        max(MIN_WELCH_N, effective["baseline"]),
    )
    return pd.DataFrame(rows).set_index("period"), naive, adjusted


def correlation_table(
    scopes: dict[str, pd.DataFrame], pairs: tuple[tuple[str, str], ...]
) -> pd.DataFrame:
    """Pearson and Spearman coefficients for every (pair, scope) combination."""
    rows: list[dict[str, object]] = []
    for x, y in pairs:
        for scope_name, frame in scopes.items():
            pearson = stats.pearsonr(frame[x], frame[y])
            spearman = stats.spearmanr(frame[x], frame[y])
            rows.append(
                {
                    "pair": f"{x} ~ {y}",
                    "scope": scope_name,
                    "n": len(frame),
                    "pearson_r": float(pearson.statistic),
                    "pearson_p": float(pearson.pvalue),
                    "spearman_rho": float(spearman.statistic),
                    "spearman_p": float(spearman.pvalue),
                }
            )
    return pd.DataFrame(rows).set_index(["pair", "scope"])


def partial_correlation(x: pd.Series, y: pd.Series, control: pd.Series) -> float:
    """Pearson correlation of x and y after regressing both on the control."""
    design = np.column_stack([np.ones(len(control)), control.to_numpy(dtype=float)])
    residuals: list[np.ndarray] = []
    for series in (x, y):
        target = series.to_numpy(dtype=float)
        coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
        residuals.append(target - design @ coefficients)
    return float(stats.pearsonr(residuals[0], residuals[1]).statistic)


def bayes_update(prior: float, likelihood_ratio: float) -> float:
    """Posterior probability from a prior probability and a likelihood ratio."""
    if not 0 < prior < 1:
        raise ValueError("prior must be strictly between 0 and 1")
    if likelihood_ratio <= 0:
        raise ValueError("likelihood ratio must be positive")
    odds = prior / (1 - prior) * likelihood_ratio
    return odds / (1 + odds)


def phia_label(probability: float) -> str:
    """Nearest band of the PHIA probability yardstick."""
    for upper, label in PHIA_BANDS:
        if probability <= upper:
            return label
    return PHIA_BANDS[-1][1]


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def report_task1(df: pd.DataFrame) -> BaselineStats:
    baseline = df[df["period"] == "baseline"]
    summary = baseline_stats(baseline["posts"])

    section("ЗАВДАННЯ 1. Базовий період (posts, 01.08–29.09, n=60)")
    print(f"Середнє:              {summary.mean:.2f}")
    print(f"Медіана:              {summary.median:.2f}")
    print(f"Ст. відхилення (ddof=1): {summary.std:.2f}")
    print(f"Q1 / Q3 / IQR:        {summary.q1:.2f} / {summary.q3:.2f} / {summary.iqr:.2f}")
    print(f"Верхня межа Tukey:    {summary.upper_fence:.2f}")

    spikes = classify_spike_days(df, summary.upper_fence)
    columns = [
        "date",
        "period",
        "signature",
        "posts",
        "channels",
        "young_channels",
        "avg_views",
        "forwards_share",
    ]
    print(f"\nДні з posts > {summary.upper_fence:.2f} (усього {len(spikes)}):")
    print(spikes[columns].assign(date=spikes["date"].dt.date).to_string(index=False))

    profile = spikes.groupby("signature").agg(
        days=("posts", "size"),
        posts=("posts", "mean"),
        avg_views=("avg_views", "mean"),
        forwards_share=("forwards_share", "mean"),
        young_channels=("young_channels", "mean"),
        young_share=("young_share", "mean"),
        posts_per_channel=("posts_per_channel", "mean"),
    )
    reference = baseline[
        [
            "avg_views",
            "forwards_share",
            "young_channels",
            "young_share",
            "posts_per_channel",
        ]
    ].median()
    print("\nСередній профіль двох типів днів вище межі:")
    print(profile.round(2).to_string())
    print("\nДовідково — медіани базового періоду:")
    print(reference.round(2).to_string())
    return summary


def report_task2(df: pd.DataFrame) -> None:
    baseline = df[df["period"] == "baseline"]
    campaign = df[df["period"] == "campaign"]

    section("ЗАВДАННЯ 2. Порівняння періодів (база n=60, кампанія n=30)")
    comparison = compare_periods(baseline, campaign)
    formatted = comparison.copy()
    for column in ("p_welch", "p_mwu", "p_welch_bonf", "p_mwu_bonf"):
        formatted[column] = formatted[column].map(lambda p: f"{p:.2e}")
    for column in ("mean_base", "mean_camp", "diff", "ratio"):
        formatted[column] = formatted[column].map(lambda v: f"{v:.2f}")
    print(formatted.to_string())
    print(
        f"\nБонферроні: m={len(METRICS)}, поріг α/m = {ALPHA / len(METRICS):.3f}; "
        "стовпці *_bonf = min(1, p·m)."
    )

    print("\n95% довірчі інтервали середнього posts (t-розподіл):")
    intervals = {
        name: mean_confidence_interval(frame["posts"])
        for name, frame in (("baseline", baseline), ("campaign", campaign))
    }
    for name, (low, high) in intervals.items():
        print(f"  {name:9s} [{low:.2f}; {high:.2f}]")
    overlap = intervals["baseline"][1] >= intervals["campaign"][0]
    print(f"  Перетинаються: {'так' if overlap else 'ні'}")

    print("\nБОНУС. Автокореляція posts (лаг 1) та ефективний розмір вибірки:")
    table, naive, adjusted = autocorrelation_report(baseline["posts"], campaign["posts"])
    print(table.round(3).to_string())
    print(f"  Welch, n як є:     t={naive.t:.2f}, df={naive.df:.1f}, p={naive.p_value:.2e}")
    print(
        f"  Welch, n_eff (не менше {MIN_WELCH_N:.0f}): t={adjusted.t:.2f}, "
        f"df={adjusted.df:.1f}, p={adjusted.p_value:.3f}"
    )


def report_task3(df: pd.DataFrame, upper_fence: float) -> None:
    baseline = df[df["period"] == "baseline"]
    campaign = df[df["period"] == "campaign"]
    scopes = {
        "all_90": df,
        "baseline": baseline,
        "baseline_no_spikes": baseline[baseline["posts"] <= upper_fence],
        "campaign": campaign,
    }
    pairs = (("posts", "avg_views"), ("posts", "forwards_share"))

    section("ЗАВДАННЯ 3. Кореляції Пірсона / Спірмена")
    print(correlation_table(scopes, pairs).round(4).to_string())

    print("\nТретя змінна young_channels (усі 90 днів):")
    third = correlation_table(
        {"all_90": df, "baseline": baseline},
        (("posts", "young_channels"), ("forwards_share", "young_channels")),
    )
    print(third[["pearson_r", "spearman_rho"]].round(4).to_string())

    raw = float(stats.pearsonr(df["posts"], df["forwards_share"]).statistic)
    partial = partial_correlation(df["posts"], df["forwards_share"], df["young_channels"])
    ranks = df[["posts", "forwards_share", "young_channels"]].rank()
    partial_rank = partial_correlation(
        ranks["posts"], ranks["forwards_share"], ranks["young_channels"]
    )
    print(
        f"\nposts ~ forwards_share, 90 днів: r = {raw:.4f}; "
        f"часткова кореляція за контролю young_channels: "
        f"Пірсон {partial:.4f}, Спірмен {partial_rank:.4f}"
    )


def weekly_profile(
    df: pd.DataFrame, baseline_mean: float, upper_fence: float
) -> pd.DataFrame:
    """Campaign period in 7-day blocks (the last block is 9 days) against the baseline."""
    campaign = df[df["period"] == "campaign"].copy()
    day_index = (campaign["date"] - CAMPAIGN_START).dt.days
    campaign["week"] = np.minimum(day_index // 7, 3) + 1
    campaign["above_fence"] = campaign["posts"] > upper_fence
    profile = campaign.groupby("week").agg(
        start=("date", "min"),
        end=("date", "max"),
        posts=("posts", "mean"),
        days_above_fence=("above_fence", "sum"),
        days=("posts", "size"),
        young_channels=("young_channels", "mean"),
        forwards_share=("forwards_share", "mean"),
        avg_views=("avg_views", "mean"),
    )
    profile.insert(3, "x_baseline", profile["posts"] / baseline_mean)
    profile["start"] = profile["start"].dt.date
    profile["end"] = profile["end"].dt.date
    return profile


def report_task4(df: pd.DataFrame, summary: BaselineStats) -> None:
    baseline = df[df["period"] == "baseline"]
    campaign = df[df["period"] == "campaign"]

    section("ЗАВДАННЯ 4. Оновлення ймовірності H = «скоординована кампанія»")
    print("Період кампанії по тижнях (останній блок — 9 днів):")
    print(weekly_profile(df, summary.mean, summary.upper_fence).round(2).to_string())
    print()
    max_young = int(baseline["young_channels"].max())
    max_forwards = float(baseline["forwards_share"].max())
    sign_a = df["young_channels"] > max_young
    sign_b = df["forwards_share"] > max_forwards
    print(
        f"A: young_channels > {max_young} (макс. бази); "
        f"B: forwards_share > {max_forwards:.2f} (макс. бази)"
    )
    overlap = pd.crosstab(
        sign_a.rename("A"), sign_b.rename("B"), margins=True, margins_name="разом"
    )
    print(overlap.to_string())
    for label, frame in (("усі 90 днів", df), ("кампанія", campaign)):
        rho = float(
            stats.spearmanr(frame["young_share"], frame["forwards_share"]).statistic
        )
        print(f"Спірмен young_share ~ forwards_share, {label}: {rho:.3f}")

    lr_a = P_A_GIVEN_H / P_A_GIVEN_NOT_H
    lr_b_naive = P_B_GIVEN_H / P_B_GIVEN_NOT_H
    lr_b_conditional = P_B_GIVEN_A_H / P_B_GIVEN_A_NOT_H
    after_a = bayes_update(PRIOR_H, lr_a)
    after_ab_naive = bayes_update(after_a, lr_b_naive)
    after_ab_conditional = bayes_update(after_a, lr_b_conditional)

    steps = (
        ("Prior", None, PRIOR_H),
        ("Після A", lr_a, after_a),
        ("Після A і B — наївно (B ⟂ A)", lr_b_naive, after_ab_naive),
        ("Після A і B — з урахуванням B|A", lr_b_conditional, after_ab_conditional),
    )
    print()
    for name, ratio, probability in steps:
        ratio_text = "—" if ratio is None else f"{ratio:.2f}"
        print(
            f"  {name:34s} LR={ratio_text:>5s}  P(H)={probability:6.1%}  "
            f"{phia_label(probability)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "csv",
        nargs="?",
        type=Path,
        default=Path(__file__).with_name("hw17_water_posts.csv"),
        help="path to hw17_water_posts.csv",
    )
    args = parser.parse_args()

    try:
        df = load_data(args.csv)
    except (OSError, ValueError) as error:
        print(f"Cannot load {args.csv}: {error}", file=sys.stderr)
        return 1

    pd.set_option("display.width", 200)
    summary = report_task1(df)
    report_task2(df)
    report_task3(df, summary.upper_fence)
    report_task4(df, summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
