"""Conservative eligibility evidence for current performance inputs.
The age limits match the existing data-quality warnings; they are not fitted to losses.
"""
import pandas as pd

def recommendation_quality(pa, pb, asof, data_cutoff, quality, injury_uncertain=False):
    asof = pd.Timestamp(asof).normalize()
    reasons = []
    if quality == "LOW": reasons.append("insufficient player history")
    if injury_uncertain: reasons.append("injury/fitness uncertain")
    def age(value):
        try:
            d = pd.Timestamp(value)
            return None if pd.isna(d) else (asof - d.normalize()).days
        except (ValueError, TypeError): return None
    gap = age(data_cutoff)
    if gap is None or gap < 0 or gap > 45: reasons.append("point-statistics dataset missing, future-dated or older than 45 days")
    for p in (pa, pb):
        gap = age(p.get("last_stats_match"))
        if gap is None or gap < 0 or gap > 60: reasons.append(f"{p.get('name', 'player')}: serve/return statistics missing, future-dated or older than 60 days")
    return {"eligible": not reasons, "reasons": reasons, "policy": "current-performance-v1"}
