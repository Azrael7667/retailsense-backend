import difflib
import re
from datetime import date, timedelta
from typing import Any, Dict, List

TOL = 0.06
VOTE_TOL = 0.1
VAT_RATE = 0.13

UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
SCALES = {"thousand": 1000, "lakh": 100000, "lakhs": 100000,
          "crore": 10000000, "crores": 10000000}
VOCAB = list(UNITS) + list(SCALES) + ["hundred", "and"]
IGNORE = {"rs", "nrs", "npr", "rupees", "rupee", "only", "paisa", "paise"}


def _words_int(tokens: List[str]) -> int:
    total, cur = 0, 0
    for t in tokens:
        if t in UNITS:
            cur += UNITS[t]
        elif t == "hundred":
            cur = (cur or 1) * 100
        elif t in SCALES:
            total += (cur or 1) * SCALES[t]
            cur = 0
    return total + cur


_STOP_W = re.compile(r"[0-9]|\b(?:Total|Rounded|Net|Vat|Taxable|Only)\b", re.I)


def _word_tokens(chunk: str) -> List[str]:
    toks: List[str] = []
    for t in re.findall(r"[a-z]+", chunk.lower()):
        if t in IGNORE or t in ("palse", "pais", "paisha"):
            continue
        if t in ("ond", "nd", "aand", "an"):
            t = "and"
        if t not in VOCAB:
            c = difflib.get_close_matches(t, VOCAB, n=1, cutoff=0.67)
            if not c:
                continue
            t = c[0]
        toks.append(t)
    return toks


def words_amounts(text: str) -> List[float]:
    # Candidate numeric values of the amount-in-words text (tolerates OCR typos).
    m = re.search(r"Words(.{0,250})", text, re.I | re.S)
    if not m:  # no "Words" label (BNH): use the text after "Rs."
        m = re.search(r"\b(?:N?Rs|NPR|Rupees)\b\.?(.{0,250})", text, re.I | re.S)
        if not m:
            return []
    win = m.group(1)
    mk = re.search(r"\b(?:N?Rs|NPR|Rupees)\b\.?", win, re.I)
    chunk = _STOP_W.split(win[mk.end():] if mk else win, 1)[0]
    toks = _word_tokens(chunk)
    if not toks:
        return []
    out: List[float] = []
    if "and" in toks:
        i = len(toks) - 1 - toks[::-1].index("and")
        left, right = toks[:i], toks[i + 1:]
        if right and len(right) <= 2 and all(t in UNITS for t in right):
            out.append(round(_words_int(left) + _words_int(right) / 100, 2))
    out.append(float(_words_int([t for t in toks if t != "and"])))
    return out


def clean_desc(d: str) -> str:
    out = []
    for t in (d or "").split():
        if re.fullmatch(r"[A-Z0-9()\-/.,&]+", t):
            out.append(t)
        else:
            break  # first junk token ends the description
    s = " ".join(out)
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s+0$", "", s)
    return re.sub(r"\s+", " ", s).strip(" -.,")


def _close(a, b, tol=TOL) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def _grp(k):
    return "net" if k in ("net_minus_vat", "words_minus_vat") else k


def validate_bill(fields: Dict[str, Any], items: List[Dict], text: str) -> Dict[str, Any]:
    f = dict(fields)
    checks: List[Dict] = []
    issues: List[str] = []
    corrections: List[str] = []

    def add(name: str, ok: bool, detail: str):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            issues.append(f"{name}: {detail}")

    net, vat, tax = f.get("net_amount"), f.get("vat_amount"), f.get("taxable_amount")

    # 1. amount in words vs net amount
    words = words_amounts(text)
    if net is None and words:
        net = words[0]
        f["net_amount"] = net
        corrections.append(f"net_amount missing -> {net} (from amount in words)")
    words_net = next((w for w in words if _close(w, net)), None)
    if words:
        add("amount_in_words_matches_net", words_net is not None,
            f"words read as {words[0]}, net field {net}")
    else:
        add("amount_in_words_matches_net", False, "could not read the amount in words")

    # 2. taxable amount: vote between independent sources
    src: Dict[str, float] = {}
    if net is not None and vat is not None:
        src["net_minus_vat"] = round(net - vat, 2)
    if words_net is not None and vat is not None:
        src["words_minus_vat"] = round(words_net - vat, 2)
    if tax is not None:
        src["taxable_field"] = tax
    if vat is not None:
        src["vat_div_rate"] = round(vat / VAT_RATE, 2)

    best, best_votes = None, []
    for v in src.values():
        votes = [k for k, u in src.items() if abs(u - v) <= VOTE_TOL]
        if len({_grp(k) for k in votes}) > len({_grp(k) for k in best_votes}):
            best, best_votes = v, votes
    if best is not None and len({_grp(k) for k in best_votes}) >= 2:
        if tax is None or abs(tax - best) > TOL:
            corrections.append(
                f"taxable_amount {tax} -> {best} (agreed by {', '.join(best_votes)})")
            f["taxable_amount"] = best
            tax = best
        add("taxable_consistent", True, f"{len(best_votes)} sources agree on {best}")
    else:
        add("taxable_consistent", False, "no two sources agree on the taxable amount")

    if tax is not None and vat is not None:
        add("vat_is_13_percent", abs(vat - round(tax * VAT_RATE, 2)) <= VOTE_TOL,
            f"13% of {tax} = {round(tax * VAT_RATE, 2)}, bill says {vat}")
    if None not in (tax, vat, net):
        add("taxable_plus_vat_equals_net", abs(tax + vat - net) <= VOTE_TOL,
            f"{tax} + {vat} = {round(tax + vat, 2)}, net {net}")

    # 3. dates and ids
    d, miti = f.get("date_ad"), f.get("miti_bs")
    add("date_present", d is not None, d or "no valid AD date found")
    if d:
        dd = date.fromisoformat(d)
        add("date_not_in_future", dd <= date.today() + timedelta(days=1), d)
        if miti:
            diff = int(miti[:4]) - dd.year
            add("ad_bs_years_consistent", diff in (56, 57), f"AD {dd.year} vs BS {miti[:4]}")
    add("bill_no_present", bool(f.get("bill_no")), f.get("bill_no") or "missing")
    add("supplier_pan_9_digits", bool(re.fullmatch(r"[0-9]{9}", f.get("supplier_pan") or "")),
        f.get("supplier_pan") or "missing")

    # 4. items
    items = [dict(i) for i in items]
    for it in items:
        it["description"] = clean_desc(it.get("description", ""))
        it["net_recovered"] = False
    add("items_found", len(items) > 0, f"{len(items)} rows")

    missing = [i for i in items if i["net_amount"] is None]
    recovered = False
    if tax is not None and items:
        if len(missing) == 1:
            rest = round(tax - sum(i["net_amount"] for i in items if i["net_amount"] is not None), 2)
            if rest > 0:
                m = missing[0]
                m["net_amount"], m["net_recovered"], m["needs_review"] = rest, True, True
                corrections.append(
                    f"row {items.index(m) + 1} net = {rest} (taxable minus other rows, unverified)")
                recovered, missing = True, []
            else:
                add("item_recovery", False, "other rows already exceed the taxable amount")
        elif len(missing) >= 2:
            add("items_sum_matches_taxable", False,
                f"{len(missing)} rows have no amount, cannot verify the sum")
        if not missing and not recovered:
            s = round(sum(i["net_amount"] for i in items), 2)
            add("items_sum_matches_taxable", abs(s - tax) <= 0.5, f"rows sum {s}, taxable {tax}")

    # 4b. gross check: row amounts (before discount) vs the printed Total Amount
    gross = f.get("total_amount")
    if gross is not None and items and all(i.get("amount") is not None for i in items):
        g = round(sum(i["amount"] for i in items), 2)
        add("items_sum_matches_total", abs(g - gross) <= 0.5, f"rows sum {g}, total {gross}")

    # 5. per-row review reasons
    review = list(issues)
    for n, it in enumerate(items, 1):
        why = []
        if it["net_recovered"]:
            why.append("net recovered by subtraction, check against the paper bill")
        elif it["net_amount"] is None:
            why.append("net not read")
        elif it.get("net_derived"):
            why.append("net derived from amount minus discount")
        if it.get("repair_note"):
            why.append(it["repair_note"])
        if it["qty"] is None:
            why.append("qty/rate not read")
        if why:
            review.append(f"row {n} ({it['part_no']}): " + "; ".join(why))

    return {
        "status": "ok" if not review else "review",
        "checks": checks,
        "corrections": corrections,
        "needs_review": review,
        "fields": f,
        "items": items,
    }
