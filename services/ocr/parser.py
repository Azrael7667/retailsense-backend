import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional

A = re.ASCII  # stop \d from matching Devanagari digits

MONEY = re.compile(r"(?<![0-9.,])[0-9][0-9.,]*[.,][0-9]{2}(?![0-9])", A)
CODE = re.compile(r"(?=[A-Z0-9\-]*[0-9])[A-Z0-9][A-Z0-9\-]{3,}", A)  # part/HSN-like token
HEAD = re.compile(r"escription", re.I | A)
STOP = re.compile(
    r"in\s*words|total\s*(?:amount|quantity|qty)|gross\s*amount|bill\s*print|customer\s*signature|taxable",
    re.I | A)
DISC_N = re.compile(r"\b([0-9]{2})\s*N[0-9]\b", A)   # Sipradi style: "45 N2"
HAS_N = re.compile(r"\bN[0-9]\b", A)


def to_amount(tok: str) -> float:
    digits = re.sub(r"[.,]", "", tok)
    return float(digits[:-2] + "." + digits[-2:])


def _half_up(x: float) -> float:
    return float(Decimal(str(round(x, 6))).quantize(Decimal("0.01"), ROUND_HALF_UP))


def _first(pattern: str, text: str, flags: int = 0) -> Optional[str]:
    m = re.search(pattern, text, flags | A)
    return re.sub(r"\s+", "", m.group(1)) if m else None


def _last_amount(pattern: str, text: str) -> Optional[float]:
    toks = [m.group(1) for m in re.finditer(pattern, text, re.I | A)]
    for tok in reversed(toks):
        if MONEY.fullmatch(tok):
            return to_amount(tok)
    return None


def _bill_no(text: str) -> Optional[str]:
    v = (_first(r"Bill\s*No[^0-9\n]{0,4}([0-9]{3,8}\s*/\s*[0-9]{2,6})", text, re.I)
         or _first(r"\b([0-9]{6}/[0-9]{4})\b", text))
    if v:
        return v
    m = re.search(
        r"(?:Bill|Inv\w*|nv\w*|ice)\s*(?:No\.?|Number)\s*[:;.]?\s*"
        r"((?=[A-Z0-9/\-]*[0-9])[A-Z0-9][A-Z0-9/\-]{3,24})", text, re.I | A)
    if m:
        return m.group(1).upper()
    m = re.search(r"(?<![0-9])([0-9]{10,14}-[0-9]{4,6})(?![0-9])", text, A)
    return m.group(1) if m else None


def _date(text: str) -> Optional[str]:
    m = re.search(r"(20[0-3][0-9])\s*[/-]\s*([0-9]{2})\s*[/-]\s*([0-9]{2})", text, A)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.search(r"([0-9]{2})/([0-9]{2})/(20[0-3][0-9])", text, A)
        if m:
            d, mo, y = map(int, m.groups())
        else:
            m = re.search(r"(?<![0-9/])([0-9]{2})/([0-9]{2})/([0-9]{2})(?![0-9/])", text, A)
            if not m:
                return None
            a, b, yy = map(int, m.groups())
            mo, d = (a, b) if a <= 12 else (b, a)   # Sipradi prints MM/DD/YY
            y = 2000 + yy
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def _customer(text: str) -> Optional[str]:
    m = re.search(r"Bill\s*TO\s*[:;.]?\s*[A-Z0-9]{4,10}\W+([^\n]+)", text, re.I | A)
    if not m:
        m = re.search(r"\w*tomer\s*(?:Name|Details)\s*[:;.]?\s*([^\n]+)", text, re.I | A)
    if not m:
        return None
    name = m.group(1)
    if ")" in name:
        name = name[: name.index(")") + 1]
    name = re.sub(r"[^A-Za-z0-9 ()&.\-]", "", name)
    name = re.sub(r"(\s+[0-9])+\s*$", "", name)
    return re.sub(r"\s+", " ", name).strip() or None


def _supplier_name(lines: List[str]) -> Optional[str]:
    for l in lines[:15]:
        if re.search(r"\b(PVT\.?|LTD\.?|TRADERS|ENTERPRISES?|TRADING)\b", l, re.I):
            return re.sub(r"[^A-Za-z0-9 ().&\-]", "", l).strip() or None
    return None


# ---------------------------------------------------------------- items

def _wordish(t: str) -> bool:
    s = t.strip("~=_|.,:;()\"'\u201c\u201d!-")
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z.\-]{2,}", s))


def _is_start(line: str) -> bool:
    m = MONEY.search(line)
    pre = line[: m.start()] if m else line
    if m:
        return bool(CODE.search(pre)) and bool(re.search(r"[A-Za-z]{3,}", pre))
    return bool(re.search(r"[0-9]{7,}", pre)) and bool(re.search(r"[A-Za-z]{4,}", pre))


def _build_item(lines: List[str]) -> Dict:
    first = lines[0]
    m0 = MONEY.search(first)
    pre = first[: m0.start()] if m0 else first

    codes = [c.group() for c in CODE.finditer(pre)]
    part = max(codes, key=len) if codes else None

    toks = [t.lstrip("~=_|") for t in pre.split()]
    i = 0
    while i < len(toks) and not _wordish(toks[i]):
        i += 1
    words: List[str] = []
    clean_end = True
    for t in toks[i:]:
        if re.fullmatch(r"[A-Z0-9()\-/.,&]+", t):
            words.append(t)
        else:
            clean_end = False
            break

    item = {
        "part_no": part, "description": "", "qty": None, "rate": None,
        "amount": None, "discount": None, "disc_pct": None, "net_amount": None,
        "net_derived": False, "needs_review": True,
    }

    vals = [to_amount(x.group()) for l in lines for x in MONEY.finditer(l)]
    vals = [v for v in vals if v > 0]
    qty = rate = None
    net = disc = None
    derived = False

    if vals:
        top = max(vals)                                   # amount = biggest number in the row
        idx = len(vals) - 1 - vals[::-1].index(top)
        before, after = vals[:idx], vals[idx + 1:]

        best = None                                       # qty * rate = amount
        for v in before:
            q = round(top / v)
            if 1 <= q <= 1000 and abs(q * v - top) <= 0.05 + 0.005 * q:
                key = (-q, v)
                if best is None or key > best[0]:
                    best = (key, q, v)
        if best:
            qty, rate = best[1], best[2]

        if HAS_N.search(first):                           # layout with "Disc % + code" column
            mp = DISC_N.search(first)
            if mp:
                disc = _half_up(top * float(mp.group(1)) / 100)
                net = round(top - disc, 2)
        else:
            pct = next((v for v in after if v <= 100), None)
            if not after or after[-1] == top:
                net = top
            else:
                last = after[-1]
                if 0.7 * top <= last < top:
                    net, disc = last, round(top - last, 2)
                elif last < 0.3 * top and last != pct:
                    disc, net, derived = last, round(top - last, 2), True
            if pct is not None and 0 < pct <= 60:          # exact net from the discount %
                d = _half_up(top * pct / 100)
                cn = round(top - d, 2)
                if net is None:
                    net, disc, derived = cn, d, True
                elif net != top and abs(cn - net) <= 1.0:
                    net, disc, derived = cn, d, False

        item.update(amount=top, discount=disc, net_amount=net, net_derived=derived,
                    disc_pct=round(disc / top * 100, 2) if disc else None)

    # qty digits glued to the end of the description
    if qty is not None and words and re.fullmatch(r"[0-9]{1,5}", words[-1]):
        q, w = str(qty), words[-1]
        if w == q or (len(w) < len(q) and q.startswith(w)):
            words.pop()
    # wrapped description on the next line
    if clean_end:
        for extra in lines[1:]:
            if MONEY.search(extra):
                continue
            for t in extra.split():
                if re.fullmatch(r"[A-Z0-9()\-/.,&]*[A-Z]{2,}[A-Z0-9()\-/.,&]*", t):
                    words.append(t)
                else:
                    break
            break

    desc = re.sub(r"\(\s*\)", "", " ".join(words))
    desc = re.sub(r"\s+", " ", desc).strip(" -.,")
    item.update(description=desc, qty=qty, rate=rate,
                needs_review=(qty is None or net is None or derived))
    return item


def _parse_items_generic(lines: List[str]) -> List[Dict]:
    start = next((i + 1 for i, l in enumerate(lines) if HEAD.search(l)), 0)
    rows: List[List[str]] = []
    for l in lines[start:]:
        if STOP.search(l):
            break
        if _is_start(l):
            rows.append([l])
        elif rows and len(rows[-1]) <= 3:
            rows[-1].append(l)
    return [_build_item(r) for r in rows]


def parse_bill(text: str) -> Dict:
    lines = [l for l in text.splitlines() if l.strip()]
    amt = r"([0-9][0-9.,]*[0-9])"
    fields = {
        "bill_no": _bill_no(text),
        "date_ad": _date(text),
        "miti_bs": _first(r"Miti[^0-9\n]{0,4}(20[7-9][0-9]\s*[/-]\s*[0-9]{1,2}\s*[/-]\s*[0-9]{1,2})", text, re.I)
                   or _first(r"\((20[7-9][0-9]/[0-9]{1,2}/[0-9]{1,2})\)", text),
        "supplier_name": _supplier_name(lines),
        "supplier_pan": _first(r"\bVAT\s*(?:No\.?|Number)?[^0-9\n]{0,6}([0-9]{9})(?![0-9])", text, re.I),
        "customer_name": _customer(text),
        "customer_pan": _first(r"PAN\s*No[^0-9\n]{0,4}([0-9]{9})", text, re.I),
        "total_amount": _last_amount(r"Total\s*Amount[^0-9\n]{0,6}" + amt, text)
                        or _last_amount(r"Gross\s*Amount[^0-9\n]{0,6}" + amt, text)
                        or _last_amount(r"Basic\s*Total[^0-9\n]{0,6}" + amt, text),
        "taxable_amount": _last_amount(r"Taxable[^0-9\n]{0,10}" + amt, text),
        "vat_amount": _last_amount(r"Vat[^0-9\n]{0,6}13(?:\.[0-9]*)?\s*%[^0-9\n]{0,4}" + amt, text)
                      or _last_amount(r"[0-9]{2}\s*%\s*VAT[^0-9\n]{0,4}" + amt, text),
        "net_amount": _last_amount(r"Net\s*Amount[^0-9\n]{0,6}" + amt, text)
                      or _last_amount(r"Total\s*Amount\s*Inc\w*\.?\s*VAT[^0-9\n]{0,6}" + amt, text)
                      or _last_amount(r"Net\s*Tot\w*[^0-9\n]{0,14}" + amt, text),
    }
    return {"fields": fields, "items": parse_items(lines)}


# ------------------------------------------------ Sipradi-style rows
# part | HS | description | qty | Unit | rate | disc% | code | amount   (amount = qty x rate, before discount)
UNIT_TOK = re.compile(r"^Un[a-z]{1,2}$", re.I | A)
CODE_LINE = re.compile(r"\bN[0-9]\b", A)
DEV = {c: str(i) for i, c in enumerate("०१२३४५६७८९")}
CONF = {"4": "1", "3": "1", "8": "65", "6": "5"}   # digit read -> digits it may really be
JUNK = "$«»~=_|—–-()'\"“”!:;/\\"


def _clean(t: str) -> str:
    return t.strip(JUNK).rstrip(".,")


def _num(tok: str) -> Optional[float]:
    t = _clean(tok)
    if re.fullmatch(r"[0-9][0-9.,]*[.,][0-9]{2}", t):
        return to_amount(t)
    if re.fullmatch(r"[0-9]{5,7}", t):          # decimal point lost by OCR
        return int(t) / 100
    return None


def _qty(tok: str) -> Optional[int]:
    t = _clean(tok)
    if re.fullmatch(r"[0-9]{1,3}", t):
        return int(t)
    if len(t) == 1 and t in DEV:
        return int(DEV[t])
    return None


def _is_num_line(l: str) -> bool:
    toks = l.split()
    marker = any(UNIT_TOK.match(_clean(t)) for t in toks) or bool(CODE_LINE.search(l))
    return marker and any(_num(t) is not None for t in toks)


def _fit(r, a, q=None):
    if r is None or a is None or r <= 0 or a <= 0:
        return None
    q = q or round(a / r)
    if 1 <= q <= 9999 and abs(q * r - a) <= 0.005 * q + 0.015:
        return q
    return None


def _variants(v: float, k: int):
    ds = str(int(round(v * 100)))
    out = {(round(v, 2), 0)}
    pos = [(i, c) for i, d in enumerate(ds) for c in CONF.get(d, "")]
    if k >= 1:
        for i, c in pos:
            out.add((int(ds[:i] + c + ds[i + 1:]) / 100, 1))
    if k >= 2:
        for x in range(len(pos)):
            for y in range(x + 1, len(pos)):
                (i, c), (j, e) = pos[x], pos[y]
                if i != j:
                    s = list(ds)
                    s[i], s[j] = c, e
                    out.add((int("".join(s)) / 100, 2))
    return out


def _search(rate, amount, hint):
    """Return (qty, rate, amount) if exactly one small digit fix explains the row."""
    for k in (1, 2):
        rv, av = _variants(rate, k), _variants(amount, k)
        for use_hint in ((True, False) if hint else (False,)):
            sols = set()
            for r, sr in rv:
                for a, sa in av:
                    if sr + sa == k:
                        q = _fit(r, a, hint if use_hint else None)
                        if q:
                            sols.add((q, r, a))
            if len(sols) == 1:
                return next(iter(sols))
            if len(sols) > 1:
                return None            # ambiguous: do not guess
    return None


def _repair(rate, amount, hint, rate_digits):
    q = _fit(rate, amount)
    if q:
        return q, rate, amount, None
    if rate is not None and amount is not None:
        sol = _search(rate, amount, hint)
        if sol:
            return sol[0], sol[1], sol[2], "digits corrected so that qty x rate = amount"
    if hint and amount is not None:
        r2 = round(amount / hint, 2)
        ds = str(int(round(r2 * 100)))
        if rate_digits and len(rate_digits) >= 4 and ds.endswith(rate_digits) and _fit(r2, amount, hint):
            return hint, r2, amount, "rate rebuilt from amount / qty"
    if hint and rate is not None:
        return hint, rate, round(hint * rate, 2), "amount rebuilt from qty x rate"
    return None, rate, amount, None


def _caps(t: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9()\-/.,&]+", t))


def _sip_desc(toks: List[str], extras: List[str]) -> str:
    start = None
    for i, t in enumerate(toks):
        if re.fullmatch(r"[0-9]{4}", _clean(t)) and re.search(r"[0-9]{9,}", " ".join(toks[:i]), A):
            start = i + 1
            break
    if start is None:
        start = next((i for i, t in enumerate(toks)
                      if re.fullmatch(r"[A-Z][A-Z0-9()\-/.,&]{2,}", t)), len(toks))
    words: List[str] = []
    for t in toks[start:]:
        if UNIT_TOK.match(_clean(t)) or _num(t) is not None:
            break
        if re.fullmatch(r"[^A-Za-z0-9]+", t):
            continue
        if _caps(t):
            words.append(t)
        else:
            break
    while words and re.fullmatch(r"[0-9]{1,3}", words[-1]):
        words.pop()
    for l in extras[:2]:
        add = [t for t in l.split() if re.fullmatch(r"[A-Z0-9()\-/.,&]*[A-Z][A-Z0-9()\-/.,&]*", t)]
        if add:
            words += add
            break
    d = re.sub(r"\(\s*\)", "", " ".join(words))
    return re.sub(r"\s+", " ", d).strip(" -.,")


def _build_sip_item(lines: List[str]) -> Dict:
    toks = lines[0].split()
    nums = [(i, _num(t)) for i, t in enumerate(toks) if _num(t) is not None]
    amount = nums[-1][1] if nums else None
    rate = rate_idx = rate_digits = None
    if len(nums) >= 2:
        rate_idx, rate = nums[-2]
        rate_digits = re.sub(r"[^0-9]", "", _clean(toks[rate_idx]))
    else:                                   # rate sits on the wrapped line
        for l in lines[1:3]:
            for t in l.split():
                v = _num(t)
                if v is not None:
                    rate, rate_digits = v, re.sub(r"[^0-9]", "", _clean(t))
                    break
            if rate is not None:
                break

    ui = next((i for i, t in enumerate(toks) if UNIT_TOK.match(_clean(t))), None)
    hint = _qty(toks[ui - 1]) if ui else None

    pct = None
    ci = next((i for i, t in enumerate(toks) if re.fullmatch(r"N[0-9A-Z]", _clean(t), re.I)), None)
    cands = [toks[ci - 1]] if ci else []
    if rate_idx is not None:
        cands += toks[rate_idx + 1: rate_idx + 4]
    for t in cands:
        c = _clean(t)
        if re.fullmatch(r"[0-9]{2}", c) and 0 < int(c) <= 60:
            pct = int(c)
            break

    qty, rate, amount, note = _repair(rate, amount, hint, rate_digits)
    disc = _half_up(amount * pct / 100) if (amount and pct) else None
    net = round(amount - disc, 2) if disc is not None else None
    pm = re.search(r"[0-9]{9,}", lines[0], A)
    return {
        "part_no": pm.group() if pm else None,
        "description": _sip_desc(toks, lines[1:]),
        "qty": qty, "rate": rate, "amount": amount,
        "discount": disc, "disc_pct": float(pct) if pct else None, "net_amount": net,
        "net_derived": False, "repair_note": note,
        "needs_review": qty is None or rate is None or net is None or note is not None,
    }


def parse_items(lines: List[str]) -> List[Dict]:
    start = next((i + 1 for i, l in enumerate(lines) if HEAD.search(l)), 0)
    body: List[str] = []
    for l in lines[start:]:
        if STOP.search(l):
            break
        body.append(l)
    if sum(1 for l in body if _is_num_line(l)) >= 3:
        rows: List[List[str]] = []
        for l in body:
            if _is_num_line(l):
                rows.append([l])
            elif rows and len(rows[-1]) <= 2:
                rows[-1].append(l)
        return [_build_sip_item(r) for r in rows]
    return _parse_items_generic(lines)


# ------------------------------------------------ rows with "qty Pcs rate amount" (BNH style)
UNIT_QTY = re.compile(
    r"(?<![0-9.,])([0-9]{1,4}(?:\.[0-9]{1,2})?)\s*"
    r"(?:Pcs|Pes|Pc|Nos|No|Sets|Set|Box|Ltr|Kg|Mtr|Pair)\b\s*"
    r"([0-9][0-9.,]*[.,][0-9]{2})(?![0-9])", re.I | A)

_orig_build_item = _build_item


def _build_item(lines):
    item = _orig_build_item(lines)
    text = " ".join(lines)
    m = UNIT_QTY.search(text)
    if not m:
        return item
    q, rate = float(m.group(1)), to_amount(m.group(2))
    if q <= 0 or rate <= 0:
        return item
    if (item["qty"] is not None and item["rate"] is not None and item["amount"] is not None
            and abs(item["qty"] * item["rate"] - item["amount"]) <= 0.06):
        return item                      # already consistent, leave it alone
    expected = round(q * rate, 2)
    tail = [_num(t) for t in text[m.end():].split()]
    tail = [v for v in tail if v is not None]
    read = max(tail) if tail else None
    note = None if (read is not None and abs(read - expected) <= 0.005) else "amount rebuilt from qty x rate"
    item.update(qty=int(q) if q == int(q) else q, rate=rate, amount=expected,
                discount=None, disc_pct=None, net_amount=expected, net_derived=False,
                repair_note=note, needs_review=note is not None)
    return item


# --- bill_no/pan patch v1 ---
import re as _re

_SB_RE = _re.compile(r"\b([A-Z]{2,5}(?:-[A-Z]{2})?[- ]SB-\d{2,3}/\d{2,3})[-.\s]?(\d{2,5})\b")
_MV_RE = _re.compile(r"(?<![A-Za-z0-9])[S$5][I1l|](\d{5}-\d{2}/\d{2})\b")
_SI_RE = _re.compile(r"Invoice\s*No\.?[\s:;.|]*[S$][It1l|]?/?\s*(\d{4})\b", _re.I)
_PAN9 = _re.compile(r"(?<![\d.,])(\d{9})(?![\d.,]\d)")


def _bill_no_v1(text):
    m = _SB_RE.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = _MV_RE.search(text)
    if m:
        return "SI" + m.group(1)
    m = _SI_RE.search(text)
    if m:
        return "SI/" + m.group(1)
    return None


def _pan_v1(text, exclude):
    for line in text.splitlines():
        if _re.search(r"\b(?:PAN|VAT)\b", line, _re.I):
            for m in _PAN9.finditer(line):
                if m.group(1) not in exclude:
                    return m.group(1)
    return None


_parse_bill_orig = parse_bill


def parse_bill(text):
    out = _parse_bill_orig(text)
    try:
        f = out["fields"] if isinstance(out, dict) and "fields" in out else out
        new_no = _bill_no_v1(text)
        if new_no:
            f["bill_no"] = new_no
        if not f.get("supplier_pan"):
            p = _pan_v1(text, {str(f.get("customer_pan") or "")})
            if p:
                f["supplier_pan"] = p
    except Exception:
        pass
    return out
# --- end bill_no/pan patch v1 ---


# --- items filter patch v2 ---
_HDR_WORDS_V2 = _re.compile(r"\b(?:PAN|VAT|E-?mail|Phone|Fax|Tel|Invoice|Challan|Customer|Address|Date|Miti)\b", _re.I)


def _junk_item_v2(it):
    # a row with no quantity, rate or amount that looks like a header line
    if any(it.get(k) not in (None, "", 0, 0.0) for k in ("qty", "rate", "amount")):
        return False
    pn = str(it.get("part_no") or "")
    desc = str(it.get("description") or "").strip()
    if not desc:
        return True
    if _HDR_WORDS_V2.search(desc):
        return True
    if _re.fullmatch(r"\d{9,}", pn):
        return True
    return False


_parse_bill_v1 = parse_bill


def parse_bill(text):
    out = _parse_bill_v1(text)
    try:
        if isinstance(out, dict) and isinstance(out.get("items"), list):
            out["items"] = [it for it in out["items"] if not _junk_item_v2(it)]
    except Exception:
        pass
    return out
# --- end items filter patch v2 ---


# --- head patch v4: table header can also say "Particulars" ---
HEAD = _re.compile(r"escription|Part[il1]culars", _re.I | A)
# --- end head patch v4 ---


# --- row repair patch v5: item name from the row text, qty/rate recovered from amount ---
import re as _re

_UNIT_V5 = r"(?:Pcs|Pes|Pc|Nos|No|Sets|Set|Box|Ltr|Kg|Mtr|Pair|Doz|[^\x00-\x7f]{1,3})"
_NUM_V5 = r"([0-9][0-9.,]*[.,][0-9]{2})(?![0-9])"
_MONEY_TOK_V5 = _re.compile(r"^[\$S|]?\d[\d,]*[.,]\d{1,2}$", _re.I)
_ROW_V5 = _re.compile(
    r"(?<![0-9.,])([0-9]{1,5}(?:\.[0-9]{1,2})?)\s*" + _UNIT_V5
    + r"(?![A-Za-z])\s*[\$S|]?\s*" + _NUM_V5 + r"(?:\s+" + _NUM_V5 + r")?",
    _re.I,
)


def _f_v5(s):
    if _re.fullmatch(r"\d+,\d{1,2}", s):
        return float(s.replace(",", "."))
    return float(s.replace(",", ""))


def _tail_junk_v5(t):
    return (
        _MONEY_TOK_V5.match(t)
        or _re.fullmatch(_UNIT_V5, t, _re.I)
        or t.lower() in ("amount", "rate", "qty", "unit", "|", "=", "-", "~")
    )


def _desc_from_row_v5(line):
    toks = line.split()
    while toks and _tail_junk_v5(toks[-1]):
        toks.pop()
    for i in range(min(5, len(toks) - 1)):
        if _re.fullmatch(r"\d{4,8}", toks[i]) and all(len(t) <= 3 for t in toks[:i]):
            toks = toks[i + 1:]
            break
    d = " ".join(toks).strip(" |-=~.")
    if len(d) >= 4 and len(_re.findall(r"[A-Za-z]", d)) >= 3:
        return d
    return None


_build_item_v5_orig = _build_item


def _build_item(lines):
    item = _build_item_v5_orig(lines)
    try:
        first = lines[0] if lines else ""
        d = (item.get("description") or "").strip()
        if not d or d == str(item.get("part_no") or ""):
            nd = _desc_from_row_v5(first)
            if nd:
                item["description"] = nd
        if not item.get("qty") or not item.get("rate"):
            m = _ROW_V5.search(first)
            if m:
                q, r = _f_v5(m.group(1)), _f_v5(m.group(2))
                a = _f_v5(m.group(3)) if m.group(3) else None
                new_rate = None
                if a is None:
                    new_rate, a = r, round(q * r, 2)
                elif q and a and abs(q * r - a) <= max(1.0, 0.005 * a):
                    new_rate = r
                elif q and a:
                    cand = round(a / q, 2)
                    if f"{cand:.2f}".endswith(f"{r:.2f}"):
                        new_rate = cand
                if new_rate and q and a:
                    item["qty"] = int(q) if q == int(q) else q
                    item["rate"] = new_rate
                    item["amount"] = a
                    item["net_amount"] = a
                    item["needs_review"] = True
                    for k, v in list(item.items()):
                        if isinstance(v, str) and "not read" in v.lower():
                            item[k] = ""
    except Exception as e:
        import logging
        logging.getLogger("uvicorn.error").warning("row repair patch v5 failed: %r", e)
    return item
# --- end row repair patch v5 ---


# --- hsn/part patch v7: HSN is not a part number; Unit-Qty-Rate layout; header-line rows ---
import contextvars as _cv
import logging

_NOPART_V7 = _cv.ContextVar("nopart_v7", default=False)
_UNITW_V7 = r"(?:Pcs|Pes|Pc|Nos|No|Sets|Set|Box|Ltr|Kg|Mtr|Pair|Doz)"
_UNIT_START_V7 = _re.compile(r"^(?:ML|LTR|LITRE|L|KG|GM|MM|CM|INCH|IN|PCS|PC|NO|NOS|SET|SETS|PAIR)\b", _re.I)
_JUNK_ROW_V7 = _re.compile(r"print|date|time|page|words|remark|total|signature", _re.I)


def _header_no_part_col_v7(text):
    for line in text.splitlines():
        if _re.search(r"Particulars|escription", line, _re.I) and _re.search(r"Qty|Quant|Rate|Amount|Unit", line, _re.I):
            return not _re.search(r"Part[\s.]*(?:No|Num|#)", line, _re.I)
    return False


def _qty_tok_v7(t):
    if _MONEY_TOK_V5.match(t):
        return _f_v5(t.lstrip("$S|"))
    if _re.fullmatch(r"\d{1,6}", t):
        v = int(t)
        return v / 100 if len(t) >= 3 and t.endswith("00") else float(v)
    return None


def _particulars_v7(toks):
    toks = list(toks)
    for i in range(min(5, len(toks) - 1)):
        if _re.fullmatch(r"\d{4,8}", toks[i]) and all(len(t) <= 3 for t in toks[:i]):
            toks = toks[i + 1:]
            break
    while toks:
        t = toks[-1]
        if _tail_junk_v5(t):
            toks.pop()
        elif _re.fullmatch(r"\d{1,5}", t) and len(toks) >= 2 and _re.fullmatch(_UNITW_V7, toks[-2], _re.I):
            toks.pop()
        else:
            break
    return " ".join(toks).strip(" |-=~.:;_")


def _split_particulars_v7(s):
    m = _re.match(r"^([A-Z]{2,5})[ \-]?(\d[0-9A-Z\-/]+)\s+(.+)$", s)
    if m and not _UNIT_START_V7.match(m.group(3)):
        if not _re.search(r"[AEIOU]", m.group(1)) or len(_re.sub(r"\D", "", m.group(2))) >= 4:
            return s[:m.end(2)].strip(), m.group(3).strip()
    words = s.split()
    if len(words) >= 2:
        t = words[0]
        if len(t) >= 5 and sum(c.isdigit() for c in t) >= 3 and _re.fullmatch(r"[A-Za-z0-9\-/.]+", t):
            return t, " ".join(words[1:])
    return "", s


_build_item_v7_orig = _build_item


def _build_item(lines):
    item = _build_item_v7_orig(lines)
    try:
        toks = (lines[0] if lines else "").split()
        if not item.get("qty") or not item.get("rate"):
            u = next((i for i, t in enumerate(toks) if i >= 2 and _re.fullmatch(_UNITW_V7, t, _re.I)), None)
            if u is not None and not _MONEY_TOK_V5.match(toks[u - 1]):
                nums = []
                for k, t in enumerate(x for x in toks[u + 1:] if x != "|"):
                    v = _qty_tok_v7(t) if k == 0 else (_f_v5(t.lstrip("$S|")) if _MONEY_TOK_V5.match(t) else None)
                    if v is None:
                        break
                    nums.append(v)
                if len(nums) >= 2 and nums[0] > 0 and nums[1] > 0:
                    q, r, rest = nums[0], nums[1], nums[2:]
                    amt = next((x for x in rest if abs(x - q * r) <= max(1.0, 0.005 * q * r)), None)
                    pct = 0.0
                    if amt is None:
                        pct = rest[0] if rest and 0 < rest[0] <= 100 else 0.0
                        amt = round(q * r * (1 - pct / 100), 2)
                    item["qty"] = int(q) if q == int(q) else q
                    item["rate"] = r
                    item["amount"] = amt
                    item["net_amount"] = amt
                    if pct:
                        item["disc_pct"] = pct
                    item["needs_review"] = True
                    for k, v in list(item.items()):
                        if isinstance(v, str) and "not read" in v.lower():
                            item[k] = ""
        if _NOPART_V7.get() and _re.fullmatch(r"\d{4,8}", str(item.get("part_no") or "")):
            pn, name = _split_particulars_v7(_particulars_v7(toks))
            if name:
                item["part_no"], item["description"] = pn, name
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("hsn/part patch v7 failed: %r", e)
    return item


_parse_bill_v7_orig = parse_bill


def parse_bill(text):
    tok = _NOPART_V7.set(_header_no_part_col_v7(text))
    try:
        out = _parse_bill_v7_orig(text)
    finally:
        _NOPART_V7.reset(tok)
    try:
        if isinstance(out, dict):
            if isinstance(out.get("items"), list):
                out["items"] = [
                    it for it in out["items"]
                    if not (not it.get("qty") and not it.get("rate") and not it.get("amount")
                            and _JUNK_ROW_V7.search(str(it.get("description") or "") + " " + str(it.get("part_no") or "")))
                ]
            f = out.get("fields")
            if isinstance(f, dict) and f.get("supplier_name"):
                f["supplier_name"] = _re.sub(r"^[A-Za-z][,.]?\s+(?=[A-Z][A-Za-z]{2,})", "", f["supplier_name"])
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("hsn/part patch v7 (post) failed: %r", e)
    return out
# --- end hsn/part patch v7 ---


# --- cleanup patch v8: trailing symbols in rows, junk rows with an amount, supplier name noise ---
def _particulars_v7(toks):
    toks = list(toks)
    for i in range(min(5, len(toks) - 1)):
        if _re.fullmatch(r"\d{4,8}", toks[i]) and all(len(t) <= 3 for t in toks[:i]):
            toks = toks[i + 1:]
            break
    while toks:
        t = toks[-1]
        if _tail_junk_v5(t) or not _re.search(r"[A-Za-z0-9]", t):
            toks.pop()
        elif _re.fullmatch(r"\d{1,5}", t) and len(toks) >= 2 and _re.fullmatch(_UNITW_V7, toks[-2], _re.I):
            toks.pop()
        else:
            break
    s = " ".join(toks).strip(" |-=~.:;_")
    s = _re.sub(r"\s+" + _UNITW_V7 + r"\b(?:\s+[\d.,|]+)+\s*$", "", s, flags=_re.I)
    return s.strip(" |-=~.:;_")


_parse_bill_v8_orig = parse_bill


def parse_bill(text):
    out = _parse_bill_v8_orig(text)
    try:
        if isinstance(out, dict):
            if isinstance(out.get("items"), list):
                out["items"] = [
                    it for it in out["items"]
                    if not (not it.get("qty") and not it.get("rate")
                            and _JUNK_ROW_V7.search(str(it.get("description") or "") + " " + str(it.get("part_no") or "")))
                ]
            f = out.get("fields")
            if isinstance(f, dict) and f.get("supplier_name"):
                n = _re.sub(r"(?:\s+[a-z]{1,2}\.?)+$", "", f["supplier_name"])
                f["supplier_name"] = _re.sub(r"\bPyt\b", "Pvt", n)
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("cleanup patch v8 failed: %r", e)
    return out
# --- end cleanup patch v8 ---


# --- cleanup patch v9: cut the name at the unit word, ignore trailing noise ---
def _particulars_v7(toks):
    toks = list(toks)
    for i in range(min(5, len(toks) - 1)):
        if _re.fullmatch(r"\d{4,8}", toks[i]) and all(len(t) <= 3 for t in toks[:i]):
            toks = toks[i + 1:]
            break
    s = " ".join(toks)
    m = _re.search(r"\s+" + _UNITW_V7 + r"(?![A-Za-z])\s*[\d|]", s, _re.I)
    if m:
        s = s[:m.start()]
    toks = s.split()
    while toks:
        t = toks[-1]
        if _tail_junk_v5(t) or not _re.search(r"[A-Za-z0-9]", t) or _re.fullmatch(r"[a-z]{1,2}", t):
            toks.pop()
        elif _re.fullmatch(r"\d{1,5}", t) and len(toks) >= 2 and _re.fullmatch(_UNITW_V7, toks[-2], _re.I):
            toks.pop()
        else:
            break
    return " ".join(toks).strip(" |-=~.:;_")
# --- end cleanup patch v9 ---


# --- sipradi patch v10: solve qty / price / amount together from the row ---
def _sip_fix_v10(item, row):
    oq, orate, oam = item.get("qty"), item.get("rate"), item.get("amount")
    if oq and orate and oam and abs(oq * orate - oam) <= max(1.0, 0.006 * oq):
        return
    toks = row.split()
    ci = next((i for i, t in enumerate(toks) if _re.search(r"N[0-9]", t)), None)
    if ci is None:
        return
    A, full = None, False
    for t in toks[ci + 1:]:
        c = _re.sub(r"[^\d.,]", "", t)
        if _re.fullmatch(r"\d{1,3}(?:,\d{3})*(?:\.\d{0,2})?|\d+(?:\.\d{0,2})?", c):
            v = float(c.replace(",", "").rstrip(".") or 0)
            if v >= 100:
                A, full = v, bool(_re.search(r"\.\d{2}$", c))
                break
    if A is None:
        return
    disc, di = None, None
    for i in range(ci - 1, -1, -1):
        c = _re.sub(r"[^\d]", "", toks[i])
        if c and len(c) <= 3 and int(c) <= 100 and "." not in toks[i]:
            disc, di = int(c), i
            break
    if di is None:
        return
    raw, garbled, pi = None, False, None
    for i in range(di - 1, -1, -1):
        m = _re.search(r"(\d{1,3}(?:,\d{3})*\.\d{2})$", toks[i])
        if m:
            raw, garbled, pi = m.group(1).replace(",", ""), len(toks[i]) > len(m.group(1)), i
            break
    if raw is None:
        return
    plist = [float(raw)] + ([float(str(d) + raw) for d in range(1, 10)] if garbled else [])
    ui = next((i for i in range(pi - 1, -1, -1) if "unit" in toks[i].lower()), None)
    qtok = _re.sub(r"\D", "", toks[ui - 1]) if ui and ui >= 1 else ""
    cands = []
    for P in plist:
        q = round(A / P)
        tol = max(1.0, 0.006 * q) + (0.0 if full else 1.0)
        if q >= 1 and abs(q * P - A) <= tol:
            cands.append((abs(q * P - A), q, P))
    cands.sort()
    pick = None
    if len(cands) == 1:
        pick = cands[0]
    elif len(cands) > 1:
        pre = [c for c in cands if qtok and (str(c[1]) == qtok or qtok.startswith(str(c[1])))]
        if len(pre) == 1:
            pick = pre[0]
        elif cands[0][0] * 4 <= cands[1][0]:
            pick = cands[0]
    if not pick:
        return
    _, q, P = pick
    amt = A if full else round(q * P, 2)
    item["qty"], item["rate"], item["amount"] = int(q), P, amt
    item["disc_pct"] = float(disc)
    item["net_amount"] = round(amt * (1 - disc / 100), 2)
    if "discount" in item:
        item["discount"] = round(amt * disc / 100, 2)
    item["needs_review"] = True
    for k, v in list(item.items()):
        if isinstance(v, str) and "not read" in v.lower():
            item[k] = ""


_build_sip_item_v10_orig = _build_sip_item


def _build_sip_item(lines):
    item = _build_sip_item_v10_orig(lines)
    try:
        _sip_fix_v10(item, lines[0] if lines else "")
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("sipradi patch v10 failed: %r", e)
    return item


_parse_bill_v10_orig = parse_bill


def parse_bill(text):
    out = _parse_bill_v10_orig(text)
    try:
        f = out.get("fields") if isinstance(out, dict) else None
        if isinstance(f, dict) and f.get("supplier_name"):
            toks = [t for t in f["supplier_name"].split() if len(_re.findall(r"[a-z][A-Z]", t)) < 2]
            f["supplier_name"] = _re.sub(r"(?:\s+\d{1,2})+$", "", " ".join(toks))
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("sipradi patch v10 (supplier) failed: %r", e)
    return out
# --- end sipradi patch v10 ---


# --- sipradi names patch v11: rebuild an empty name from the row text ---
def _sip_name_v11(item, lines):
    if (item.get("description") or "").strip():
        return
    toks = (lines[0] if lines else "").split()
    pi = next((i for i, t in enumerate(toks) if _re.fullmatch(r"\d{9,14}", t)), None)
    if pi is None:
        return
    hi = pi + 1 if pi + 1 < len(toks) and _re.fullmatch(r"\d{4}", toks[pi + 1]) else pi
    ui = next((i for i, t in enumerate(toks) if i > hi and "unit" in t.lower()), None)
    end = ui if ui is not None else len(toks)
    if end - 1 > hi and _re.fullmatch(r"\W*\d{1,4}\W*", toks[end - 1]):
        end -= 1
    words = toks[hi + 1:end]
    for extra in lines[1:3]:
        if not _re.search(r"\d+[.,]\d{2}", extra):
            words += extra.split()
    keep = []
    for t in words:
        t2 = _re.sub(r"^[^A-Za-z0-9(]+|[^A-Za-z0-9)/]+$", "", t)
        if len(t2) >= 2 and _re.search(r"[A-Z0-9]", t2) and _re.fullmatch(r"[A-Za-z0-9()\-/.,&+]+", t2):
            keep.append(t2)
    name = " ".join(keep).strip(" -.,")
    if name:
        item["description"] = name
        item["needs_review"] = True


_build_sip_item_v11_orig = _build_sip_item


def _build_sip_item(lines):
    item = _build_sip_item_v11_orig(lines)
    try:
        _sip_name_v11(item, lines)
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("sipradi names patch v11 failed: %r", e)
    return item
# --- end sipradi names patch v11 ---
