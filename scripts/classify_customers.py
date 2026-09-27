"""
RetailSense Nepal - Customer Pricing Tier Classifier
Classifies customers into Regular/Standard pricing tiers using RFM
(Recency, Frequency, Monetary) scoring, and suggests a discount % for
Regular-tier customers, scaled by how strong their RFM score is.

Manually-set tiers/discounts (owner overrides) are never touched by
this classifier -- see tier_manually_set / discount_manually_set.

Run standalone: python scripts/classify_customers.py [--dry-run]
Or:             called via FastAPI endpoints in routers/classification.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
from supabase import create_client
from datetime import date
import numpy as np

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

STORE_NAME = "Bijeta Auto Parts"
TIER_PERCENTILE = 0.65  # top 35% of RFM scores -> "regular"
MIN_DISCOUNT = 5.0
MAX_DISCOUNT = 15.0


def get_store_id():
    """Used only for standalone CLI runs -- API callers pass store_id directly."""
    result = supabase.table("stores").select("id") \
        .eq("name", STORE_NAME).single().execute()
    if not result.data:
        raise ValueError(f"Store '{STORE_NAME}' not found")
    return result.data["id"]


def fetch_customers(store_id: str):
    result = supabase.table("customers") \
        .select("id, name, pricing_tier, discount_percent, tier_manually_set, discount_manually_set") \
        .eq("store_id", store_id) \
        .order("name") \
        .execute()
    return result.data or []


def fetch_invoices(store_id: str):
    result = supabase.table("invoices") \
        .select("customer_id, invoice_date, total") \
        .eq("store_id", store_id) \
        .not_.is_("customer_id", "null") \
        .execute()
    return result.data or []


def compute_rfm(customers: list, invoices: list) -> list:
    """
    Returns a list of dicts, one per customer WITH purchase history:
      { id, name, recency, frequency, monetary, rfm_score }
    Customers with no invoices are excluded -- caller decides how to
    handle them (left at their existing tier/discount, typically default).
    """
    today = date.today()
    by_customer = {}
    for inv in invoices:
        cid = inv["customer_id"]
        by_customer.setdefault(cid, {"dates": [], "total": 0.0})
        by_customer[cid]["dates"].append(date.fromisoformat(inv["invoice_date"]))
        by_customer[cid]["total"] += float(inv["total"])

    scored = []
    for c in customers:
        data = by_customer.get(c["id"])
        if not data:
            continue
        recency_days = (today - max(data["dates"])).days
        scored.append({
            "id": c["id"],
            "name": c["name"],
            "recency": recency_days,
            "frequency": len(data["dates"]),
            "monetary": data["total"],
        })

    if len(scored) < 4:
        return scored  # not enough signal to percentile-rank meaningfully

    def percentile_rank(values, value, invert=False):
        arr = np.array(values)
        rank = (arr < value).sum() / len(arr)
        return 1 - rank if invert else rank

    recencies   = [r["recency"] for r in scored]
    frequencies = [r["frequency"] for r in scored]
    monetaries  = [r["monetary"] for r in scored]

    for r in scored:
        r_score = percentile_rank(recencies, r["recency"], invert=True)  # lower recency = better
        f_score = percentile_rank(frequencies, r["frequency"])
        m_score = percentile_rank(monetaries, r["monetary"])
        r["rfm_score"] = round((r_score + f_score + m_score) / 3, 4)

    return scored


def classify(scored: list, tier_percentile: float = TIER_PERCENTILE) -> list:
    """
    Assigns suggested_tier + suggested_discount to each scored customer,
    based on where their rfm_score falls relative to the cutoff percentile.
    """
    if not scored or "rfm_score" not in scored[0]:
        return []

    scores = sorted(r["rfm_score"] for r in scored)
    cutoff_idx = int(len(scores) * tier_percentile)
    cutoff_score = scores[cutoff_idx] if cutoff_idx < len(scores) else scores[-1]
    top_score = max(scores)

    results = []
    for r in scored:
        is_regular = r["rfm_score"] >= cutoff_score
        if is_regular:
            span = max(top_score - cutoff_score, 1e-6)
            frac = (r["rfm_score"] - cutoff_score) / span
            discount = round(MIN_DISCOUNT + frac * (MAX_DISCOUNT - MIN_DISCOUNT), 2)
        else:
            discount = 0.0

        results.append({
            **r,
            "suggested_tier": "regular" if is_regular else "standard",
            "suggested_discount": discount,
        })
    return results


def update_customers(results: list, customers_map: dict, dry_run: bool = False) -> dict:
    """
    Writes suggested_tier/suggested_discount to the DB, skipping any field
    the owner has locked via tier_manually_set / discount_manually_set.
    """
    updated = 0
    regular_count = 0
    skipped_locked = 0

    for r in results:
        customer = customers_map[r["id"]]
        update = {"rfm_score": r["rfm_score"], "last_classified_at": "now()"}
        locked = False

        if customer["tier_manually_set"]:
            locked = True
        else:
            update["pricing_tier"] = r["suggested_tier"]

        if customer["discount_manually_set"]:
            locked = True
        else:
            update["discount_percent"] = r["suggested_discount"]

        if locked:
            skipped_locked += 1

        if not dry_run:
            supabase.table("customers").update(update).eq("id", r["id"]).execute()

        updated += 1
        if r["suggested_tier"] == "regular":
            regular_count += 1

    return {"updated": updated, "regular": regular_count,
            "standard": updated - regular_count, "locked_fields_skipped": skipped_locked}


def classify_customers(store_id: str = None, dry_run: bool = False,
                        tier_percentile: float = TIER_PERCENTILE):
    if not store_id:
        store_id = get_store_id()

    print(f"\nCustomer Pricing Tier Classification")
    print("-" * 50)

    customers = fetch_customers(store_id)
    invoices = fetch_invoices(store_id)
    customers_map = {c["id"]: c for c in customers}

    no_history = len(customers) - len({inv["customer_id"] for inv in invoices})
    print(f"  {len(customers)} customers total ({no_history} with no purchase history, skipped)")

    scored = compute_rfm(customers, invoices)
    if len(scored) < 4:
        print("  Not enough purchase history to classify customers yet.")
        return {"results": [], "stats": {"updated": 0, "regular": 0, "standard": 0, "locked_fields_skipped": 0}}

    results = classify(scored, tier_percentile)

    results_sorted = sorted(results, key=lambda x: x["rfm_score"], reverse=True)
    print(f"\n  Top 10 by RFM score:")
    for r in results_sorted[:10]:
        print(f"    {r['name']:<35} rfm={r['rfm_score']:.3f} -> {r['suggested_tier']} @ {r['suggested_discount']}%")

    stats = update_customers(results, customers_map, dry_run=dry_run)
    tag = " [DRY RUN - not saved]" if dry_run else ""
    print(f"\n  Classified {stats['updated']} customers "
          f"({stats['regular']} regular, {stats['standard']} standard, "
          f"{stats['locked_fields_skipped']} had owner-locked fields preserved){tag}")
    print("  Done!")

    return {"results": results, "stats": stats}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()
    classify_customers(dry_run=args.dry_run)
