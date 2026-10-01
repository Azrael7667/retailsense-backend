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
