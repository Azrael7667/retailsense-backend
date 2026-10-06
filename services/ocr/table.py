"""
Column-based reader for the item table.

The line parser reads Tesseract's text line by line, so on a tilted or
low-resolution photo numbers from neighbouring rows get mixed up. This reader
works on positions instead:

1. flatten the sheet of paper (perspective warp) so table rows are horizontal
2. find the header row (Qty / Rate / Amount / Dis % ...) and the column edges
3. read each number column on its own with a digits-only whitelist
4. line the columns up by height into rows
5. fix each row with qty x rate = amount, and accept the table only if the
   rows add up to the printed total
"""
import difflib
import logging
import re
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

from services.ocr.preprocess import estimate_skew, remove_table_lines

WIDTH = 2400  # working width of the flattened page, in pixels
DIGITS = "--oem 1 --psm 6 -c tessedit_char_whitelist=0123456789.,"

HEADER_KEYS = {
    "qty": re.compile(r"^(qty|qnty|quant|qtv|oty|aty)", re.I),
    "rate": re.compile(r"^(rate|price|mrp)", re.I),
    "amount": re.compile(r"^(amount|amt|total)", re.I),
    "disc": re.compile(r"^(dis|disc|discount|less)%?$", re.I),
    "desc": re.compile(r"(particular|description|goods)", re.I),
    "net": re.compile(r"^net$", re.I),
    "part": re.compile(r"^part(no\.?)?$", re.I),  # not "Particulars"
    "hs": re.compile(r"^\W*[lI]?(hs|hsn|hsc)", re.I),
    "unit": re.compile(r"^(unit|uom)$", re.I),
}
TEXT = "--oem 1 --psm 6"
UNITS = ["Pcs", "Pieces", "Piece", "Sets", "Set", "Kits", "Kit", "Nos", "Ltr", "Kg", "Box", "Pair",
         "Pale", "Unit", "Mtr", "Btl", "Can", "Roll", "Jar", "Drum", "Pkt"]
END_ROW = re.compile(r"^(sub|total|gross|basic|taxable|words|no\.?of|remarks)", re.I)


# ---------------------------------------------------------------- page

def _order(p: np.ndarray) -> np.ndarray:
    s, d = p.sum(1), np.diff(p, axis=1).ravel()
    return np.array([p[s.argmin()], p[d.argmin()], p[s.argmax()], p[d.argmax()]], np.float32)


def _plausible(q: np.ndarray) -> bool:
    """A sheet photographed at an angle: corners near 90 degrees, opposite sides of similar length."""
    for i in range(4):
        a, b, c = q[i - 1], q[i], q[(i + 1) % 4]
        v1, v2 = a - b, c - b
        cos = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-6))
        if abs(cos) > 0.26:  # outside 75..105 degrees
            return False
    top, bot = np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3])
    lef, rig = np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1])
    return min(top, bot) / max(top, bot) > 0.85 and min(lef, rig) / max(lef, rig) > 0.85


def _deskew(bgr: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    a = estimate_skew(g)
    if abs(a) < 0.3:
        return bgr
    h, w = g.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), a, 1.0)
    return cv2.warpAffine(bgr, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


def flatten(bgr: np.ndarray) -> np.ndarray:
    """Warp the sheet of paper to a flat rectangle; returns the input if no sheet is found."""
    h, w = bgr.shape[:2]
    k = 1000 / max(h, w)
    small = cv2.resize(bgr, None, fx=k, fy=k)
    g = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    cs, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cs:
        return bgr
    c = max(cs, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.25 * small.shape[0] * small.shape[1]:
        return _deskew(bgr)
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    quad = None
    for eps in (0.02, 0.03, 0.05, 0.08):
        ap = cv2.approxPolyDP(hull, eps * peri, True)
        if len(ap) == 4:
            quad = ap
            break
    if quad is None:
        return _deskew(bgr)
    q = _order(quad.reshape(4, 2).astype(np.float32) / k)
    if not _plausible(q):
        return _deskew(bgr)
    W = int(max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3])))
    H = int(max(np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1])))
    if W < 0.4 * w or H < 0.4 * h:
        return bgr
    M = cv2.getPerspectiveTransform(q, np.float32([[0, 0], [W, 0], [W, H], [0, H]]))
    return cv2.warpPerspective(bgr, M, (W, H), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


def _binary(gray: np.ndarray) -> np.ndarray:
    """Black text on white; adaptive so shadows across the sheet don't swallow the text."""
    g = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, 15)


# ---------------------------------------------------------------- layout

def _words(img: np.ndarray, config: str) -> List[Dict]:
    d = pytesseract.image_to_data(img, lang="eng", config=config, output_type=Output.DICT)
    out = []
    for i, t in enumerate(d["text"]):
        t = t.strip()
        if t:
            out.append({"t": t, "x": d["left"][i], "y": d["top"][i], "w": d["width"][i],
                        "h": d["height"][i], "conf": float(d["conf"][i])})
    return out


def _find_header(words: List[Dict], page_h: int) -> Optional[Tuple[int, Dict[str, Dict]]]:
    """The text line holding the most column titles (needs Amount plus Qty or Rate)."""
    best = None
    for w in words:
        if w["y"] > 0.55 * page_h:  # the footer ("Total No of Qty", "Discount") is not the header
            continue
        cy = w["y"] + w["h"] / 2
        row = [v for v in words if abs((v["y"] + v["h"] / 2) - cy) < max(w["h"], 20) + 0.06 * abs(v["x"] - w["x"])]
        cols: Dict[str, Dict] = {}
        for v in sorted(row, key=lambda v: v["x"]):
            t = v["t"].strip(".:;|!'\"{}[]()")
            for k, rx in HEADER_KEYS.items():
                if rx.search(t) and k not in cols:
                    cols[k] = v
        if "amount" not in cols and not {"qty", "rate"} <= cols.keys():
            continue  # without an Amount title, Qty and Rate must both be there
        if "amount" in cols and cols["amount"]["t"].lower().startswith("total") and "rate" not in cols:
            continue
        if not ({"qty", "rate", "desc"} & cols.keys()):
            continue
        score = len(cols)
        if best is None or score > best[0]:
            best = (score, max(v["y"] + v["h"] for v in cols.values()), cols)
    return (best[1], best[2]) if best else None


def _amount_cols(words: List[Dict], hdr_y: int) -> List[Dict]:
    """All header words that read Amount/Amt/Total, left to right (gross first, net last)."""
    return sorted([w for w in words if abs(w["y"] + w["h"] - hdr_y) < 60 and
                   HEADER_KEYS["amount"].search(w["t"].strip(".:|"))], key=lambda w: w["x"])


def _vlines(binv: np.ndarray, y0: int, y1: int) -> List[int]:
    reg = binv[y0:y1]
    H = reg.shape[0]
    if H < 50:
        return []
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, H // 4)))
    v = cv2.morphologyEx(reg, cv2.MORPH_OPEN, k)
    prof = (v > 0).sum(0)
    xs = np.where(prof > H * 0.25)[0]
    groups: List[List[int]] = []
    for x in xs:
        if groups and x - groups[-1][-1] <= 10:
            groups[-1].append(int(x))
        else:
            groups.append([int(x)])
    return [int(np.mean(g)) for g in groups]


def _bounds(word: Dict, lines: List[int], width: int, neighbours: List[int]) -> Tuple[int, int]:
    """Column edges around a header word: ruling lines if there are any, else halfway to the next title."""
    cx = word["x"] + word["w"] / 2
    left = [x for x in lines if x < word["x"] + 5]
    right = [x for x in lines if x > word["x"] + word["w"] - 5]
    if left and right and right[0] - left[-1] < width * 0.3:
        return left[-1] + 4, right[0] - 4
    lo = max([n for n in neighbours if n < cx], default=None)
    hi = min([n for n in neighbours if n > cx], default=None)
    a = int((lo + cx) / 2) if lo is not None else int(word["x"] - word["w"])
    b = int((hi + cx) / 2) if hi is not None else width - 4
    return max(0, a), min(width, b)


def _table_bottom(words: List[Dict], hdr_y: int, page_h: int) -> int:
    ys = [w["y"] for w in words if w["y"] > hdr_y + 30 and END_ROW.search(w["t"])]
    return min(ys) - 6 if ys else int(min(page_h, hdr_y + 0.55 * page_h))


# ---------------------------------------------------------------- reading

def _num(s: str, decimals: bool) -> Optional[float]:
    s = s.strip(" .,")
    if not s or not re.search(r"\d", s):
        return None
    s = re.sub(r"[^0-9.,]", "", s)
    parts = re.split(r"[.,]", s)
    if decimals:
        if len(parts) > 1 and len(parts[-1]) == 2:
            return float("".join(parts[:-1]) + "." + parts[-1])
        digits = "".join(parts)
        if len(digits) >= 3:  # "242743" for 2,427.43: the decimal point was lost
            return float(digits[:-2] + "." + digits[-2:])
        return float(digits)
    # quantity: "10.00", "10,00", "3", "124.0"
    if len(parts) > 1 and len(parts[-1]) in (1, 2, 3) and set(parts[-1]) <= {"0"}:
        return float("".join(parts[:-1]) or 0)
    if len(parts) > 1 and len(parts[-1]) in (1, 2):
        return float("".join(parts[:-1]) + "." + parts[-1])
    return float("".join(parts))


def _read_column(gray: np.ndarray, a: int, b: int, y0: int, y1: int,
                 config: str = DIGITS, joiner: str = "") -> List[Tuple[float, str]]:
    """(y centre, text) for each text line in one column."""
    crop = gray[y0:y1, a:b]
    if crop.size == 0:
        return []
    crop = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    crop = cv2.copyMakeBorder(crop, 25, 25, 25, 25, cv2.BORDER_CONSTANT, value=255)
    d = pytesseract.image_to_data(crop, lang="eng", config=config, output_type=Output.DICT)
    lines: Dict[Tuple, List] = {}
    for i, t in enumerate(d["text"]):
        if t.strip():
            key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
            lines.setdefault(key, []).append((d["left"][i], d["top"][i] + d["height"][i] / 2 - 25 + y0, t))
    out = []
    for ws in lines.values():
        ws.sort()
        out.append((float(np.mean([w[1] for w in ws])), joiner.join(w[2] for w in ws)))
    return sorted(out)


def _align(col: List[Tuple[float, str]], ys: List[float], row_h: float,
           shift0: float = 0.0) -> Tuple[Dict[int, str], List[Tuple[float, str]]]:
    """
    Pair a column's lines with the anchor rows, one to one. Columns can sit lower
    or higher than the anchor (curled paper, dot-matrix feed), so the offset is
    measured first and fitted as a straight line down the page.
    """
    if not col or not ys:
        return {}, list(col)
    shift = lambda y: shift0  # first guess from the tilt of the header line
    for _ in range(2):
        pairs = []
        for cy, _t in col:
            k = min(range(len(ys)), key=lambda i: abs(cy - (ys[i] + shift(ys[i]))))
            if abs(cy - (ys[k] + shift(ys[k]))) < row_h * 0.6:
                pairs.append((ys[k], cy - ys[k]))
        if len(pairs) >= 3:
            A = np.array([[p[0], 1.0] for p in pairs])
            b, c = np.linalg.lstsq(A, np.array([p[1] for p in pairs]), rcond=None)[0]
            if abs(b) * (ys[-1] - ys[0]) > row_h:  # a steep fit is noise, keep a flat offset
                b, c = 0.0, float(np.median([p[1] for p in pairs]))
            shift = (lambda b, c: lambda y: b * y + c)(float(b), float(c))
        elif pairs:
            m = float(np.median([p[1] for p in pairs]))
            shift = lambda y, m=m: m
    cand = sorted((abs(cy - (y + shift(y))), i, j) for j, (cy, _t) in enumerate(col) for i, y in enumerate(ys))
    got, used = {}, set()
    for dist, i, j in cand:
        if dist > row_h * 0.5 or i in got or j in used:
            continue
        got[i] = col[j][1]
        used.add(j)
    return got, [c for j, c in enumerate(col) if j not in used]


# ---------------------------------------------------------------- arithmetic

def _close(a: float, b: float, rel: float = 0.0015, abs_: float = 0.6) -> bool:
    return abs(a - b) <= max(abs_, rel * abs(b))


def _same_digits(a: Optional[float], b: float) -> bool:
    if a is None:
        return False
    da, db = re.sub(r"\D", "", f"{a:.2f}"), re.sub(r"\D", "", f"{b:.2f}")
    if da == db:
        return True
    if len(da) == len(db):
        return sum(x != y for x, y in zip(da, db)) <= 1
    if abs(len(da) - len(db)) == 1:  # one stray or lost digit: 4108 for 108
        long, short = (da, db) if len(da) > len(db) else (db, da)
        return any(long[:i] + long[i + 1:] == short for i in range(len(long)))
    return False


def _row_options(q: Optional[float], r: Optional[float], a: Optional[float], d: Optional[float] = None) -> List[Tuple]:
    """
    Ways to make qty x rate = amount from what was read, best first, as
    (qty, rate, amount, sure). When the three disagree one of them is misread,
    and only the bill total can say which, so every repair is kept.
    """
    if q and r and a and _close(q * r, a):
        return [(q, r, a, True)]
    if q and r and a and d and _close(q * r * (1 - d / 100), a):  # this bill prints Amount after discount
        return [(q, r, round(q * r, 2), True)]
    opts = []
    if q and a and q == int(q):
        r2 = round(a / q, 2)
        if _close(q * r2, a):
            opts.append((q, r2, a, _same_digits(r, r2)))
    if r and a:
        q2 = a / r
        if abs(q2 - round(q2)) < 0.02 and round(q2) > 0:
            opts.append((float(round(q2)), r, a, _same_digits(q, round(q2))))
    if q and r:
        opts.append((q, r, round(q * r, 2), _same_digits(a, round(q * r, 2))))
    opts.sort(key=lambda o: not o[3])  # a repair that differs from the reading by one digit first
    return opts or [(q, r, a, False)]


def _pick(options: List[List[Tuple]], discs: List[float], targets: List[float]) -> List[Tuple]:
    """Choose one option per row so the rows add up to a printed total; else the first option of each."""
    first = [o[0] for o in options]
    open_rows = [i for i, o in enumerate(options) if len(o) > 1]
    if not targets or len(open_rows) > 12:
        return first

    def fits(choice):
        gross = sum(c[2] or 0 for c in choice)
        net = sum((c[2] or 0) * (1 - (d or 0) / 100) for c, d in zip(choice, discs))
        tol = 1.0 + 0.02 * len(choice)  # printed rates are rounded, so qty x rate drifts by paisa
        return any(abs(gross - t) <= tol or abs(net - t) <= tol for t in targets)

    if fits(first):
        return first
    from itertools import product
    for combo in product(*[range(len(options[i])) for i in open_rows]):
        choice = list(first)
        for i, k in zip(open_rows, combo):
            choice[i] = options[i][k]
        if fits(choice):
            return [(c[0], c[1], c[2], True) if i in open_rows else c for i, c in enumerate(choice)]
    return first


def _ink_segments(clean: np.ndarray, y0: int, y1: int, min_gap: int = 10) -> List[Tuple[int, int]]:
    """Runs of x that hold ink inside the table band, split by blank vertical gaps."""
    band = (clean[y0:y1] < 128).sum(0)
    ink = band > max(2, 0.004 * (y1 - y0))
    segs, start, gap = [], None, 0
    for x, v in enumerate(ink):
        if v:
            if start is None:
                start = x
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                segs.append((start, x - gap + 1))
                start, gap = None, 0
    if start is not None:
        segs.append((start, len(ink)))
    return segs


def _owner(word: Dict, segs: List[Tuple[int, int]]) -> Optional[int]:
    wa, wb = word["x"] - 10, word["x"] + word["w"] + 10
    ovs = [min(b, wb) - max(a, wa) for a, b in segs]
    k = int(np.argmax(ovs)) if ovs else -1
    return k if k >= 0 and ovs[k] > 0 else None


def _split_shared(segs, titles, clean, y0, y1):
    """Two titles over one ink run means two columns printed too close together: split on a smaller gap."""
    for _ in range(4):
        owners = [_owner(t, segs) for t in titles]
        shared = {k for k in owners if k is not None and owners.count(k) > 1}
        if not shared:
            break
        k = min(shared)
        a, b = segs[k]
        inner = None
        for gap in (6, 4, 3, 2):
            sub = [(a + p, a + q) for p, q in _ink_segments(clean[:, a:b], y0, y1, gap)]
            if len(sub) > 1:
                inner = sub
                break
        if inner is None:  # no gap at all: cut after the left title, numbers end near their title's right edge
            mine = sorted([t for t, o in zip(titles, owners) if o == k], key=lambda t: t["x"])
            cut = mine[0]["x"] + mine[0]["w"] + 12
            inner = [(a, cut), (cut + 1, b)]
        segs = segs[:k] + inner + segs[k + 1:]
    return segs


def _guess_number_columns(gray, segs, spans, read, cols, y0, y1) -> None:
    """
    Qty or Rate title not read: walk the ink runs left of Amount. Runs that read
    as numbers on most rows are taken as Rate (nearest Amount), then Qty; a text
    column such as Unit ("Pcs") reads as nothing under the digit whitelist and is skipped.
    """
    amount = spans.get("amount")
    if not amount:
        return
    n_rows = max(1, len([t for _y, t in read.get("amount", []) if _num(t, True)]))
    left_edge = cols["desc"]["x"] + cols["desc"]["w"] if "desc" in cols else int(0.35 * gray.shape[1])
    taken = [v for k, v in spans.items()]
    for a, b in sorted(segs, key=lambda s: -s[0]):
        if b > amount[0] or a < left_edge or any(min(b, t[1]) - max(a, t[0]) > 0 for t in taken):
            continue
        if "rate" in read and "qty" in read:
            break
        lines = _read_column(gray, max(0, a - 6), b + 6, y0, y1)
        nums = [t for _y, t in lines if re.fullmatch(r"[0-9][0-9.,]*", t.strip(" .,")) and _num(t, False)]
        if len(nums) < max(1, 0.6 * n_rows):
            continue
        key = "rate" if "rate" not in read else "qty"
        read[key], spans[key] = lines, (a, b)
        taken.append((a, b))


def _column_of(word: Dict, segs: List[Tuple[int, int]], fallback: Tuple[int, int]) -> Tuple[int, int]:
    """The ink run under a header title; numbers are right-aligned so it may start left of the title."""
    wa, wb = word["x"] - 10, word["x"] + word["w"] + 10
    best, best_ov = None, 0
    for a, b in segs:
        ov = min(b, wb) - max(a, wa)
        if ov > best_ov:
            best, best_ov = (a, b), ov
    if best is None or best[1] - best[0] > 3 * max(word["w"], 60) + 200:
        return fallback
    return max(0, best[0] - 6), best[1] + 6


# ---------------------------------------------------------------- main

def read_table(image_bgr: np.ndarray, targets: Optional[List[float]] = None) -> Optional[Dict]:
    """
    Returns {"items": [...], "gross": sum of row amounts} in the validator's item
    shape, or None if no item table could be found. targets are printed totals
    (gross or after discount) the rows should add up to; they pick between repairs.
    """
    flat = flatten(image_bgr)
    gray = cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY)
    f = WIDTH / gray.shape[1]
    gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    H = gray.shape[0]
    binary = _binary(gray)
    clean = 255 - remove_table_lines(255 - binary)  # titles touching the ruling lines get skipped otherwise

    words = _words(clean, "--oem 1 --psm 11")
    hdr = _find_header(words, H)
    if not hdr:
        return None
    hdr_y, cols = hdr
    y0 = hdr_y + 6
    y1 = _table_bottom(words, hdr_y, H)
    if y1 - y0 < 40:
        return None

    lines = _vlines(255 - binary, y0, y1)
    centres = sorted(w["x"] + w["w"] / 2 for w in cols.values())

    amts = _amount_cols(words, hdr_y)
    gross_w = amts[0] if amts else cols.get("amount")
    net_w = amts[-1] if len(amts) > 1 else None  # "Amount ... Net Amount": the last one is after discount

    segs = _ink_segments(clean, y0, y1)
    if gross_w is None:  # Amount title not read: the rightmost run of ink right of the other titles
        last = max(w["x"] + w["w"] for w in cols.values())
        right = [sg for sg in segs if sg[0] > last and sg[1] - sg[0] > 40]
        if not right:
            return None
        a, b = max(right, key=lambda sg: sg[1])
        gross_w = {"t": "Amount", "x": a, "y": hdr_y - 30, "w": b - a, "h": 30, "conf": 0.0}
        cols["amount"] = gross_w
    titles = [w for w in (cols.get("qty"), cols.get("rate"), gross_w, cols.get("disc"), net_w) if w is not None]
    segs = _split_shared(segs, titles, clean, y0, y1)

    def col(word):
        fb = _bounds(word, lines, WIDTH, [c for c in centres if abs(c - (word["x"] + word["w"] / 2)) > 5])
        return _column_of(word, segs, fb)

    read, spans = {}, {}
    for key, word in (("qty", cols.get("qty")), ("rate", cols.get("rate")), ("amount", gross_w),
                      ("disc", cols.get("disc")), ("net", net_w)):
        if word is not None:
            spans[key] = col(word)
            # each column starts under its own title: on a tilted photo the header is not level
            read[key] = _read_column(gray, *spans[key], word["y"] + word["h"] + 6, y1)
    if "qty" not in read or "rate" not in read:
        top = gross_w["y"] + gross_w["h"] + 6
        _guess_number_columns(gray, segs, spans, read, cols, top, y1)

    anchor = read.get("amount") or read.get("net") or []
    anchor = [(y, t) for y, t in anchor if _num(t, True)]
    if not anchor:
        return None
    ys = [y for y, _ in anchor]
    row_h = float(np.median(np.diff(ys))) if len(ys) > 1 else 40.0

    ys = [y for y, _ in anchor]
    # tilt of the header line: on a photo taken at an angle a column further left sits lower
    hw = list(cols.values())
    slope = 0.0
    if len(hw) >= 2:
        xs = np.array([w["x"] + w["w"] / 2 for w in hw])
        yb = np.array([w["y"] + w["h"] for w in hw])
        if np.ptp(xs) > 200:
            slope = float(np.polyfit(xs, yb, 1)[0])
    ax = sum(spans["amount"]) / 2 if "amount" in spans else 0
    aligned = {k: _align(read.get(k, []), ys, row_h,
                         slope * (sum(spans[k]) / 2 - ax) if k in spans and ax else 0.0)
               for k in ("qty", "rate", "disc")}
    rows = [(y, aligned["qty"][0].get(i), aligned["rate"][0].get(i), aligned["disc"][0].get(i), t)
            for i, (y, t) in enumerate(anchor)]
    # a row whose amount was not read: qty and rate left over at the same height
    for qy, qt in aligned["qty"][1]:
        mate = [r for r in aligned["rate"][1] if abs(r[0] - qy) < row_h * 0.4]
        if mate and _num(qt, False) and _num(mate[0][1], True):
            rows.append((qy, qt, mate[0][1], None, None))
    rows.sort(key=lambda r: r[0])
    # Sub Total / Taxable figures under the last item have an amount but no qty or rate
    def summary_line(r):
        if r[1] and _num(r[1], False):
            return False
        rate = _num(r[2], True) if r[2] else None
        amt = _num(r[4], True) if r[4] else None
        if rate and amt:  # qty lost but amount / rate is a whole number: a real item
            return abs(amt / rate - round(amt / rate)) > 0.02
        return True

    while len(rows) > 1 and summary_line(rows[-1]):
        rows.pop()

    options, discs = [], []
    for y, q_t, r_t, d_t, t in rows:
        d = _num(d_t, True) if d_t else None
        if d is not None and not (0 <= d < 100):
            d = None
        options.append(_row_options(_num(q_t, False) if q_t else None,
                                    _num(r_t, True) if r_t else None, _num(t, True) if t else None, d))
        discs.append(d)
    rows = [r[0] for r in rows]

    try:
        texts = _text_columns(gray, cols, spans, lines, rows, row_h, slope, ax, y1, segs)
    except Exception as e:  # names are a bonus; never lose the numbers over them
        logging.getLogger("uvicorn.error").warning("column reader text failed: %r", e)
        texts = [(None, "", None)] * len(rows)

    items = []
    for k, (y, (q, r, a, sure), d) in enumerate(zip(rows, _pick(options, discs, targets or []), discs)):
        part, desc, unit = texts[k]
        net = round(a * (1 - (d or 0) / 100), 2) if a else None
        items.append({
            "part_no": part, "description": desc, "unit": unit, "qty": q, "rate": r, "amount": a,
            "disc_pct": d or None, "discount": round(a - net, 2) if a and net is not None else None,
            "net_amount": net, "net_derived": bool(d), "needs_review": not sure,
            "repair_note": None if sure else "read by the column reader, check qty and rate",
            "_from_columns": True,
        })

    _digit_fix(items, targets or [])
    _infer_discount(items, targets or [], has_disc=any(i["disc_pct"] for i in items))
    return {"items": items, "gross": round(sum(i["amount"] or 0 for i in items), 2),
            "supplier_names": supplier_candidates(gray, words, H, hdr_y),
            "top_text": _lines_text([w for w in words if w["y"] < hdr_y])}


# ---------------------------------------------------------------- text columns

def _text_span(word: Dict, lines: List[int], left_limit: int, right_limit: int) -> Tuple[int, int]:
    """A text column runs from the ruling line left of its title to the next column."""
    left = [x for x in lines if left_limit - 5 <= x < word["x"] + 5]
    a = left[-1] + 4 if left else max(left_limit, word["x"] - 40)
    return a, max(a + 20, right_limit - 4)


def _rows_of(col_lines: List[Tuple[float, str]], ys: List[float], row_h: float, shift: float) -> List[str]:
    """Text per row: a line beside the row starts it, lines below it before the next row continue it."""
    got, left = _align(col_lines, ys, row_h, shift)
    parts: Dict[int, List[Tuple[float, str]]] = {i: [(ys[i], t)] for i, t in got.items()}
    for ly, t in left:
        above = [i for i, y in enumerate(ys) if y + shift <= ly + row_h * 0.3]
        if above and ly - (ys[above[-1]] + shift) < row_h * 1.8:
            parts.setdefault(above[-1], []).append((ly, t))
    return [" ".join(t for _y, t in sorted(parts.get(i, []))) for i in range(len(ys))]


_QTY_UNIT = re.compile(r"\s+\d[\d.,]*\s+(?:" + "|".join(["Pcs", "Pieces", "Sets?", "Kits?", "Nos", "Ltr", "Pale", "Unit"]) +
                       r")\b.*$", re.I)


def _clean_name(desc: str, part: Optional[str]) -> str:
    """The name without the part code printed in front of it, and without qty/unit spilling in at the end."""
    desc = _QTY_UNIT.sub("", desc or "")
    toks = desc.split()
    while len(toks) > 1:
        t = re.sub(r"\W", "", toks[0]).upper()
        p = re.sub(r"\W", "", part or "").upper()
        codeish = len(t) >= 4 and re.search(r"\d", t) and re.search(r"[A-Z]|\d{4,}", t)
        if HS_CODE.match(t) or (p and t and difflib.SequenceMatcher(None, t, p).ratio() >= 0.6) or \
                (codeish and len(toks) > 2 and re.fullmatch(r"[A-Z][A-Z]+", re.sub(r"\W", "", toks[1]) or "-")):
            toks.pop(0)
        else:
            break
    return " ".join(toks)


def _clean_text(t: str) -> str:
    t = re.sub(r"[|~_\[\]{}“”‘’\"`»«©®]", " ", t or "")
    t = re.sub(r"\s+", " ", t).strip(" -—.,:;'")
    toks = t.split()
    junk = lambda x: (x.islower() and len(x) <= 3) or not re.search(r"[A-Za-z0-9]", x)
    while toks and junk(toks[-1]):  # OCR crumbs at the ends of a name
        toks.pop()
    while toks and junk(toks[0]):
        toks.pop(0)
    return " ".join(toks)


HS_CODE = re.compile(r"^(27|38|39|40|68|69|70|73|74|76|83|84|85|87|94)\d{6}$")


def _clean_part(t: str) -> Optional[str]:
    """One part code: short prefixes like "TVS" join the code after them, anything after the code is dropped."""
    out = []
    for x in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-/.]*", t or ""):
        out.append(x)
        if len(x) >= 5 or re.search(r"\d", x):
            break
    s = _digit_lookalikes(" ".join(out).upper().strip(" -./"))
    s = re.sub(r"^1(?=[A-Z]{2}\d)", "T", s)  # TVS29068131 read as 1VS29068131
    if HS_CODE.match(s) or len(re.sub(r"\W", "", s)) < 4:
        return None
    return s if re.search(r"[\d-]", s) else None


_LOOK = {"O": "0", "S": "5", "I": "1", "L": "1", "Z": "2", "B": "8"}


def _digit_lookalikes(s: str) -> str:
    """Inside a run of digits a letter is a misread digit: FA00S8400 -> FA0058400, O119AAL -> 0119AAL."""
    c = list(s)
    for i, ch in enumerate(c):
        if ch not in _LOOK:
            continue
        left = c[i - 1].isdigit() if i > 0 else False
        right = c[i + 1].isdigit() if i + 1 < len(c) else False
        two_right = i + 2 < len(c) and c[i + 1].isdigit() and c[i + 2].isdigit()
        if (left and right) or (i == 0 and two_right):
            c[i] = _LOOK[ch]
    return "".join(c)


def _common_prefix_fix(parts: List[Optional[str]]) -> List[Optional[str]]:
    """Most codes on a bill share a prefix (TVS..., 0310EAU...): mend one misread character in it."""
    heads = [p[:3] for p in parts if p and len(p) > 5]
    if len(heads) < 3:
        return parts
    head, n = max(((h, heads.count(h)) for h in set(heads)), key=lambda x: x[1])
    if n < 0.6 * len(heads) or not re.search(r"[A-Z]", head):
        return parts
    out = []
    for p in parts:
        if p and len(p) > 5 and p[:3] != head and sum(a != b for a, b in zip(p[:3], head)) == 1:
            p = head + p[3:]
        out.append(p)
    return out


def _unit_in(t: str) -> Optional[str]:
    """A unit word anywhere in the cell text ("10 Pcs", "Sets")."""
    for tok in re.findall(r"[A-Za-z]{2,}", t or ""):
        u = _clean_unit(tok)
        if u:
            return u
    return None


def _clean_unit(t: str) -> Optional[str]:
    w = re.sub(r"[^A-Za-z]", "", t or "")
    if len(w) < 2:
        return None
    hit = difflib.get_close_matches(w.lower(), [u.lower() for u in UNITS], n=1, cutoff=0.6)
    if not hit:
        return None
    u = next(u for u in UNITS if u.lower() == hit[0])
    return u.upper() if w.isupper() and len(w) > 2 else u


_CODE_DASH = re.compile(r"^([A-Z0-9][A-Z0-9/.]*[0-9][A-Z0-9/.]*)\s*[-–—]\s*(.+)$")


def _part_left(pw: Dict, hs: Optional[Dict], lines: List[int], segs: List[Tuple[int, int]]) -> int:
    """Part numbers are wider than their title and start left of it: use the ruling line, else the ink."""
    hs_end = hs["x"] + hs["w"] if hs and hs["x"] < pw["x"] else 0
    ruled = [x for x in lines if hs_end - 5 <= x < pw["x"] + 5]
    if ruled:
        return ruled[-1] + 4
    under = [a for a, b in segs if a < pw["x"] + pw["w"] and b > pw["x"] and a > hs_end + 10]
    if under:
        return max(hs_end + 6, min(under) - 18)
    return max(hs_end + 10, pw["x"] - 60)


def _text_columns(gray, cols, spans, lines, rows, row_h, slope, ax, y1, segs=()) -> List[Tuple]:
    """(part no, name, unit) for every row, read column by column."""
    num_lefts = sorted(v[0] for k, v in spans.items() if k in ("qty", "rate", "amount"))
    if not rows or not num_lefts:
        return [(None, "", None)] * len(rows)

    def read(span, top, config=TEXT):
        shift = slope * ((span[0] + span[1]) / 2 - ax) if ax else 0.0
        col = _read_column(gray, int(span[0]), int(span[1]), int(top), y1, config, " ")
        return _rows_of(col, rows, row_h, shift)

    unit_txt = None
    if "unit" in cols:
        w = cols["unit"]
        right = min([x for x in num_lefts if x > w["x"] + w["w"]], default=w["x"] + w["w"] + 150)
        left = max([v[1] for v in spans.values() if v[1] <= w["x"] + 5], default=w["x"] - 60)
        unit_txt = read((left + 4, right - 4), w["y"] + w["h"] + 6)

    if unit_txt is None and "qty" in spans:
        qa = spans["qty"][0]
        nxt = min([v[0] for k, v in spans.items() if k in ("rate", "amount") and v[0] > spans["qty"][1]],
                  default=spans["qty"][1] + 200)
        head_bottom = min(w["y"] + w["h"] for w in cols.values()) + 6
        unit_txt = read((qa, nxt - 4), cols["qty"]["y"] + cols["qty"]["h"] + 6 if "qty" in cols else head_bottom)

    part_txt, desc_txt = None, None
    if "desc" in cols:
        dw = cols["desc"]
        right = min([x for x in num_lefts if x > dw["x"] + dw["w"]], default=num_lefts[0])
        if "unit" in cols and cols["unit"]["x"] > dw["x"]:
            right = min(right, cols["unit"]["x"] - 10)
        pw = cols.get("part")
        combined = pw is not None and 0 <= dw["x"] - (pw["x"] + pw["w"]) < 250 and \
            not any(pw["x"] + pw["w"] < x < dw["x"] for x in lines)
        if pw is not None and not combined and pw["x"] < dw["x"]:
            # printed part numbers can run into the HSN column; the cleaner drops the HS digits after them
            between = [x for x in lines if pw["x"] + pw["w"] - 5 < x < dw["x"] + 5]
            pb = between[0] - 4 if between else dw["x"] - 20
            pa = _part_left(pw, cols.get("hs"), lines, segs)
            part_txt = read((max(0, pa - 12), pb), pw["y"] + pw["h"] + 6)
            da = pb + 8
        else:
            da, _ = _text_span(pw if combined else dw, lines, 0, right)
        desc_txt = read((max(0, da - 14), right - 4), dw["y"] + dw["h"] + 6)

    res = []
    for i in range(len(rows)):
        desc = _clean_text(desc_txt[i]) if desc_txt else ""
        part = _clean_part(part_txt[i]) if part_txt else None
        if not part:
            m = _CODE_DASH.match(desc)
            if m:  # "122080082 - MASTER TIE ROD ASSEMBLY"
                part, desc = m.group(1), m.group(2).strip()
        if part_txt:
            desc = _clean_name(desc, part)
        else:
            desc = _QTY_UNIT.sub("", desc)
        unit = _unit_in(unit_txt[i]) if unit_txt else None
        res.append((part, desc, unit))
    fixed = _common_prefix_fix([r[0] for r in res])
    return [(f, d, u) for f, (_p, d, u) in zip(fixed, res)]


_BUYER = re.compile(r"customer|party|bill\s*to|buyer|consignee|s\.?\s*no\b|sn\b|hs\s*code|hscode|challan", re.I)
_NOT_NAME = re.compile(r"phone|mobile|email|e-mail|@|vat|pan\b|fax|www|date|bill|address|kathmandu|nepal|"
                       r"chowk|road|marg|tel\b|original|copy|ward|city|invoice\s*no", re.I)
_TITLE = re.compile(r"\b(tax\s*invoice|invoice|tax|abbreviated)\b", re.I)


def _top_lines(gray: np.ndarray, limit: int) -> List[Tuple[str, float]]:
    """(text, letter height) for each line in the top part of the page, read for large print."""
    crop = gray[:max(50, int(limit))]
    # one global threshold: the adaptive one used for the table hollows out big bold letters
    crop = cv2.threshold(cv2.GaussianBlur(crop, (3, 3), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    d = pytesseract.image_to_data(crop, lang="eng", config="--oem 1 --psm 3", output_type=Output.DICT)
    lines: Dict[Tuple, List] = {}
    for i, t in enumerate(d["text"]):
        if t.strip() and float(d["conf"][i]) > 20:
            key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
            lines.setdefault(key, []).append((d["left"][i], t.strip(), d["height"][i]))
    out = []
    for ws in lines.values():
        ws.sort()
        out.append((ws, float(np.median([w[2] for w in ws]))))
    return out


_SUFFIX = re.compile(r"pvt|ltd|limited|traders|spares|parts|automobiles?|automotive|international|concern|"
                     r"autotech|enterprises?|suppliers|trading|autoparts", re.I)


def tidy_supplier(text: Optional[str]) -> Optional[str]:
    """Drop TAX INVOICE, logo lettering the name repeats ("SIPRADI Sipradi Autoparts"), and crumbs."""
    if not text:
        return None
    toks = _TITLE.sub(" ", text).split()
    low = [re.sub(r"\W", "", t).lower() for t in toks]
    for i in range(len(toks) - 1, 0, -1):  # a later word repeating an earlier one: the name starts there
        if len(low[i]) >= 4 and any(difflib.SequenceMatcher(None, low[i], low[j]).ratio() >= 0.75
                                    for j in range(i)):
            toks = toks[i:]
            break
    out = _clean_text(" ".join(toks))
    return out or None


def name_score(text: Optional[str]) -> float:
    """How much a line looks like a company name."""
    if not text:
        return -99.0
    toks = text.split()
    good = sum(1 for t in toks if re.fullmatch(r"[A-Z][A-Za-z.&'-]{1,}|[A-Z]{2,}[.,]?|\d{2}-\d{2}", t))
    bad = len(toks) - good
    return good - 2 * bad + (3 if _SUFFIX.search(text) else 0)


def supplier_candidates(gray: np.ndarray, words: List[Dict], page_h: int,
                        stop_y: Optional[float] = None) -> List[str]:
    """Candidate supplier names from the top of the page, tallest line first."""
    limit = 0.35 * page_h if stop_y is None else min(stop_y, 0.35 * page_h)
    buyer = [w["y"] for w in words if w["y"] < limit and _BUYER.search(w["t"])]
    if buyer:
        limit = min(limit, min(buyer) - 5)
    found = []
    for ws, h in _top_lines(gray, limit):
        kept = [w[1] for w in ws if not _TITLE.fullmatch(w[1].strip(".:"))]
        text = " ".join(kept)
        if not kept or _NOT_NAME.search(text) or sum(c.isalpha() for c in text) < 6:
            continue
        found.append((h * (1.3 if _SUFFIX.search(text) else 1.0), text))
    return [tidy_supplier(t) for _h, t in sorted(found, reverse=True)[:3] if tidy_supplier(t)]


def _lines_text(words: List[Dict]) -> str:
    """Words regrouped into text lines (left to right), for the header rules in the parser."""
    lines: List[List[Dict]] = []
    for w in sorted(words, key=lambda w: (w["y"], w["x"])):
        for ln in lines:
            if abs((w["y"] + w["h"] / 2) - (ln[0]["y"] + ln[0]["h"] / 2)) < 0.6 * max(w["h"], ln[0]["h"]):
                ln.append(w)
                break
        else:
            lines.append([w])
    return "\n".join(" ".join(w["t"] for w in sorted(ln, key=lambda w: w["x"])) for ln in lines)


def _set_disc(it: Dict, d: float, note: str) -> None:
    it["disc_pct"] = d
    it["net_amount"] = round(it["amount"] * (1 - d / 100), 2)
    it["discount"] = round(it["amount"] - it["net_amount"], 2)
    it["net_derived"] = True
    it["needs_review"] = True
    it["repair_note"] = note


def _digit_fix(items: List[Dict], targets: List[float]) -> None:
    """
    The same digit misread in a row's rate and amount (5 read as 8 in both)
    keeps qty x rate = amount, so only the bill total shows it. If exactly one
    row can absorb the gap with a one-digit change to both, change it.
    """
    if not items or any(not (i.get("qty") and i.get("rate") and i.get("amount")) for i in items):
        return
    gross = sum(i["amount"] for i in items)
    tol = 1.0 + 0.03 * len(items)
    if any(abs(gross - t) <= tol for t in targets):
        return
    hits = []
    for t in targets:
        gap = t - gross
        if not _one_digit(gap):
            continue
        for it in items:
            a2 = round(it["amount"] + gap, 2)
            r2 = round(a2 / it["qty"], 2)
            if a2 > 0 and _same_digits(it["amount"], a2) and _same_digits(it["rate"], r2)                     and _close(it["qty"] * r2, a2):
                hits.append((it, r2, a2))
    if len(hits) > 1:  # prefer the row where the same character was misread in both columns
        hits = [h for h in hits if _swap(h[0]["amount"], h[2]) == _swap(h[0]["rate"], h[1])]
    if len(hits) == 1:
        it, r2, a2 = hits[0]
        note = f"rate {it['rate']:g} -> {r2:g} and amount {it['amount']:g} -> {a2:g} so the rows add up to the printed total"
        it.update(rate=r2, amount=a2, net_amount=round(a2 * (1 - (it["disc_pct"] or 0) / 100), 2),
                  needs_review=True, repair_note=note)
        it["discount"] = round(a2 - it["net_amount"], 2)


def _swap(a: float, b: float) -> Optional[Tuple[str, str]]:
    da, db = re.sub(r"\D", "", f"{a:.2f}"), re.sub(r"\D", "", f"{b:.2f}")
    diff = [(x, y) for x, y in zip(da, db) if x != y]
    return diff[0] if len(da) == len(db) and len(diff) == 1 else None


def _one_digit(x: float) -> bool:
    d = re.sub(r"[^1-9]", "", f"{abs(x):.2f}")
    return len(d) == 1


def _infer_discount(items: List[Dict], targets: List[float], has_disc: bool) -> None:
    """
    The rows make the gross total but a lower printed figure (taxable) is left
    unexplained: the line discounts were not read. Either every row carries the
    same discount (no discount column found), or one row has a whole-number one.
    """
    if not items or any(not i.get("amount") for i in items):
        return
    gross = sum(i["amount"] for i in items)
    net = sum(i["net_amount"] or i["amount"] for i in items)
    tol = 1.0 + 0.03 * len(items)
    if not targets or abs(net - min(targets)) <= tol:  # the lowest printed figure is the taxable amount
        return
    gross_ok = any(abs(gross - t) <= tol for t in targets)
    for t in sorted(t for t in targets if net * 0.7 < t < net - tol):
        gap = net - t
        if _one_digit(gap):  # 1,000.00 or 300.00: a misread digit in the printed figure, not a discount
            continue
        if not has_disc and gross_ok:
            d = round(gap / gross * 100, 2)
            if 0 < d < 60:
                for it in items:
                    _set_disc(it, d, f"discount {d}% worked out from the printed taxable amount")
                return
        hits = [it for it in items if not it.get("disc_pct")
                and abs(gap / it["amount"] * 100 - round(gap / it["amount"] * 100)) < 0.02
                and 0 < round(gap / it["amount"] * 100) < 60]
        if len(hits) == 1:
            d = float(round(gap / hits[0]["amount"] * 100))
            _set_disc(hits[0], d, f"discount {d:g}% worked out from the printed taxable amount")
            return


# ---------------------------------------------------------------- totals

def totals_of(fields: Dict) -> List[float]:
    """Printed figures the item rows may add up to: gross total, taxable, or net minus VAT."""
    out = []
    for k in ("total_amount", "taxable_amount"):
        if fields.get(k):
            out.append(float(fields[k]))
    net, vat = fields.get("net_amount"), fields.get("vat_amount")
    if net and vat:
        out.append(round(float(net) - float(vat), 2))
    elif net:  # only the grand total was read: take 13% VAT back off
        out.append(round(float(net) / 1.13, 2))
    return sorted(set(out))


def rows_add_up(items: List[Dict], targets: List[float]) -> bool:
    """Every row has qty and rate, and the rows (before or after line discounts) make a printed total."""
    if not items or not targets:
        return False
    if any(not i.get("qty") or not i.get("rate") for i in items):
        return False
    gross = sum(float(i["qty"]) * float(i["rate"]) for i in items)
    net = sum(float(i["qty"]) * float(i["rate"]) * (1 - float(i.get("disc_pct") or 0) / 100) for i in items)
    tol = 1.0 + 0.03 * len(items)
    return any(abs(gross - t) <= tol or abs(net - t) <= tol for t in targets)
