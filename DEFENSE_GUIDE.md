# RetailSense: Defense Study Guide (v2, updated for the new OCR)

Everything here was read from your code and result files, not from the README (which is partly out of date).
File references look like `ml/train_cells.py:456`. In every glossary, **"In ours"** says exactly where the term appears in RetailSense.

> **What changed since v1:** a new column-by-column table reader (`services/ocr/table.py`), an OCR scoring script (`ocr_eval/score.py`), a "local names" product feature, a header patch v13 in the parser, friendlier review notes in the adapter, faster/reused Supabase clients, and sync auth dependencies.

---

## PART 0. The 12 things you MUST know before walking in

1. **The API does not run any ML model.** Models train offline (`ml/retrain.sh`) and write JSON into `ml/results/<store_id>/`. The API only reads those files (`services/ai_results.py`).
2. **The live ML code is `ml/train_cells.py`** (generated from a notebook by `ml/build_train_cells.py`). `ml/training/*.py`, `scripts/train_all.py` and `ml/models_saved/*` are the OLD version. Don't quote their numbers (e.g. credit AUC 0.984).
3. **Credit scoring serves Logistic Regression, not LightGBM** (`ml/train_cells.py:456-457`): LightGBM must beat it by > 0.01 AUC and didn't (0.9466 vs 0.965).
4. **Honest ML performance:** forecasts ~1% better than a naive baseline; churn LightGBM (AUC 0.642) is worse than logistic regression (0.683); restock barely beats baselines. Say it yourself.
5. **Data is tiny and from ONE shop** (Bijeta Auto Parts): 1100 invoices, 52 weeks, 51 buying customers of 62.
6. **OCR now has TWO readers.** The old line-by-line text parser always runs first. The new column reader (`table.py`) always runs too. The column rows replace the line rows only when the line rows don't add up to the printed total and the column rows do.
7. **OCR core idea:** bills carry redundant arithmetic (qty x rate = amount, rows sum to total, taxable + 13% VAT = net, amount in words). The system uses these as independent witnesses to detect and repair misreads, and refuses to guess when ambiguous.
8. **OCR accuracy (measured with `ocr_eval/score.py`, 20 bills):** field accuracy **74.3%**, bill total right **70%**, rows with every field right **18.8%**. Strong: header fields and qty/rate (83-90%). Weak: part number and unit (~52%). Details and caveats in Part 2.11.
9. **Human-in-the-loop:** nothing is saved to purchases or stock until a person approves the scanned bill.
10. **No ORM and no custom JWT code.** Database = Supabase (Postgres) via its Python client. Login/token checking = Supabase Auth.
11. **Nepali text:** the old text path OCRs `eng+nep`; the new table reader is **English-only**. "Local names" (e.g. patta) is a manual product-alias feature, not Nepali OCR.
12. **Known weak points** (Part 5): permissions not enforced server-side, no DB transactions, some unauthenticated admin endpoints, minimal tests, parser tuned to specific suppliers.

---

## PART 1. The system

**RetailSense Nepal**: back-office + business-intelligence backend for small Nepali retail shops.
**Features:** products/stock, customers and suppliers with running balances (udharo), sales invoices, quotations, returns, purchases, payments in/out, expenses, khata ledger, reminders, reports (P&L, daybook, party statement), dashboard, activity feed, **Scan Bill (OCR)**, **6 AI helpers**.
**Roles:** owner (all), accountant (all by default), auditor (read-only), staff (nothing until granted); separate platform admin (SaaS operator).

| Tech | Why |
|---|---|
| FastAPI + uvicorn | Fast Python web framework, automatic validation, free docs at `/docs` |
| Pydantic (`schemas/`) | Type-checks requests/responses (bad input gets 422) |
| Supabase | Hosted Postgres + login + file storage + RLS. Saves building auth |
| Tesseract + OpenCV | Free, private OCR |
| Gemini (optional) | Cloud OCR fallback in hybrid/gemini mode |
| pandas, scikit-learn, LightGBM, Prophet, SHAP, Optuna | Offline ML |
| nepali-datetime | BS to AD dates (checked against 19 real bills; `samaya` was a day off for BS 2083) |
| Docker + Render + GitHub Actions | Deploy (Docker because Tesseract is a system program) |

### Request flow (create invoice, `routers/invoices.py`)
CORS + ActivityLog middleware, then Pydantic validation, then `get_current_user` (verifies token with Supabase, rejects sessions older than 24h), then `get_active_store_id` (reads `X-Store-Id`, checks membership, 403 if not a member), then router logic (number via RPC `next_doc_number`, totals, insert invoice, add unpaid part to `customers.balance`, insert items with `cost_price_at_sale`, subtract stock), then the activity feed logs a sentence.
**Why `cost_price_at_sale`?** Freezes cost at sale time so profit history stays right when costs change.

### What changed in the backend core (new)
- **`database.py`:** Supabase clients are now **reused** (`lru_cache`). Opening a client costs ~2 s before its first query; a connected one answers in ~0.1 s. With a token the client is cached **per token** (max 256), so one user's client never carries another's token. Without a token a fresh client is made, because sign-in/sign-up/sign-out store a session on the client. `get_token_verifier()` is one shared client used only for `auth.get_user(token)` (keeps no state). `get_supabase_admin()` is cached (service-role key, bypasses RLS).
- **`middleware/auth_middleware.py`:** `get_current_user`, `get_active_store_id`, `get_current_user_with_role` changed from `async def` to plain `def`. FastAPI runs plain-`def` dependencies in a thread pool, so the blocking network call to Supabase no longer freezes the async event loop. They use the shared token verifier.
- **Products:** new `local_names` field (schemas/product.py). Product search now also matches `sku` and `local_names` (`routers/products.py`, characters `, ( ) % *` stripped before building the filter). Product names are composed as `Name / Localname` (`utils/product_names.py`).
- **`.gitignore` / `.dockerignore`:** `*.bak*`, `venv/`, `ocr_eval/bills|labels|results` ignored (bills contain supplier bank details and PANs); `ocr_eval/` excluded from the Docker image.

### Database (inferred from code; no SQL in repo)
`stores`, `users`, `store_members` (user to stores many-to-many), `platform_admins`, `categories`, `products` (now with `local_names`), `customers` (`balance`, `credit_limit`, `pricing_tier`), `suppliers` (`balance`), `invoices` + `invoice_items`, `purchases` + `purchase_items`, `quotations` + items, `payments`, `payments_out`, `payment_allocations`, `sales_returns`, `purchase_returns`, `expenses`, `khata_entries`, `reminders`, `pending_documents` (OCR review queue), `activity_feed`.
**Credit mechanics:** `customers.balance` += unpaid part of an invoice; -= payments and return credits; edits/deletes reverse old effects first.

### Other facts
- Product classification (`routers/admin.py`): fast = 8+ selling days or 30+ units in 90 days; moderate = 3+ days or 10+ units; slow = otherwise; dead stock = no sales but stock > 0.
- Customer tiers (`scripts/classify_customers.py`): **RFM, not ML**; top 35% become "regular" with a 5-15% suggested discount; manually set tiers never overwritten.
- `ActivityLogMiddleware` (`services/activity.py`): logs successful write requests as readable sentences; best-effort.
- Deployment: Dockerfile (python 3.12-slim + tesseract eng + nep), `render.yaml` (free, Singapore, `OCR_ENGINE=selfhosted`, no Gemini key), GitHub Actions runs pytest (`|| true`, so failures don't block deploy) then calls the Render deploy hook.

### Backend glossary (with "In ours")
- **API / REST / endpoint:** URLs and verbs the frontend calls. *In ours:* about 27 routers under `/api/...` registered in `main.py`.
- **FastAPI / uvicorn / ASGI:** framework / server / async interface. *In ours:* `main.py` creates the app; Render runs `uvicorn main:app`; the ActivityLog middleware is written directly against ASGI.
- **Pydantic:** data-type checking classes. *In ours:* `schemas/*.py`, e.g. `InvoiceCreate`, `ProductBase`; `config.py` uses pydantic-settings to read `.env`.
- **ORM (SQLAlchemy):** maps classes to tables. *In ours:* **not used**; we call `supabase.table("x").select().eq()`.
- **Supabase / PostgREST:** hosted backend / table-to-REST layer. *In ours:* all tables, login, and the `purchase-bills` storage bucket.
- **JWT / Bearer token:** signed identity token in `Authorization: Bearer ...`. *In ours:* issued by Supabase on login; verified per request with `supabase.auth.get_user(token)`; sessions older than 24h rejected (`_enforce_session_age`).
- **Authentication vs authorization:** who you are vs what you may do. *In ours:* login = Supabase; roles/permissions in `users` table.
- **RBAC:** role-based access. *In ours:* `require_role(...)` guards the scan-bill endpoints (owner/accountant) and staff management.
- **RLS:** Postgres rules limiting rows per user. *In ours:* active when `get_supabase(access_token)` is used (policies live in Supabase, not in the repo).
- **Service-role key:** master key that bypasses RLS. *In ours:* `get_supabase_admin()` for reports, pending documents, activity.
- **Multi-tenancy:** many shops, one database. *In ours:* `store_id` on every row; `X-Store-Id` header checked against `store_members`.
- **Dependency injection (`Depends`):** helper functions run before a route. *In ours:* `get_current_user`, `get_active_store_id`.
- **Thread pool vs event loop:** async code shares one loop; blocking code freezes it. *In ours:* auth dependencies are plain `def` (thread pool); OCR runs in `asyncio.to_thread`.
- **Caching (`lru_cache`):** remember results of a function. *In ours:* Supabase clients in `database.py`; AI result files cached by modification time in `ai_results.py`; Gemini results cached by image hash.
- **Middleware:** wraps every request. *In ours:* CORS and ActivityLog.
- **CORS:** browser rule blocking other websites calling the API. *In ours:* allowed origins from `ALLOWED_ORIGINS`.
- **Soft delete:** mark inactive. *In ours:* `products.is_active=False`.
- **Transaction / atomic:** all steps succeed or none. *In ours:* NOT used for invoices or bill approval (weak point); only `os.replace` for result files.
- **RPC:** call a Postgres function. *In ours:* `next_doc_number` for atomic document numbers.
- **Signed URL:** temporary link to a private file. *In ours:* bill images, 10 minutes.
- **Udharo / Khata:** credit sales / credit ledger. *In ours:* `customers.balance`, `khata_entries`, Udharo Advisor model.

---

## PART 2. Scan Bill (OCR) in full detail

### 2.1 Big picture
A shop owner photographs a supplier's purchase bill. The system reads it, builds a draft (supplier, bill number, date, items with part no/unit/qty/rate/discount), the owner reviews/edits, then approves, which creates the purchase, new products/supplier, updates stock and cost prices.

### 2.2 The complete flow, file by file (`POST /api/pending-documents/purchase-bill`, `routers/pending_documents.py`)
1. **Check type** (JPEG/PNG/WebP only, no PDF). Roles: owner or accountant.
2. **Upload original** to Supabase bucket `purchase-bills` at `{store_id}/{uuid}.ext`.
3. **Insert `pending_documents` row**, status `processing`.
4. **Run extraction** in a worker thread (`asyncio.to_thread`) via `pipeline.extract_with_engine` (mode from env `OCR_ENGINE`).
5. **Self-hosted path** = `pipeline.extract_selfhosted` (now a 5-part process):
   1. `load_image` (fix phone rotation via EXIF) then `preprocess` (2.3) then `run_ocr` Tesseract `eng+nep`, psm 4 (2.4).
   2. **Line parser** `parse_bill` (2.5) then `validate_bill` (2.6). **Always runs first.**
   3. **Targets** (acceptable printed totals) = `totals_of(fields)` [total amount, taxable, net - VAT or net/1.13] plus, for each amount-in-words value, `words / 1.13`. Reason (code comment): the amount in words survives OCR better than digits.
   4. **Column reader** `read_table(original_image, targets)` (2.7). **Always runs**; wrapped in try/except (on failure `table = None` and a warning is logged).
   5. **Combine** (2.8): improve header fields from the table's top text; choose the best supplier name; decide whether line rows or column rows win; merge names/part numbers/units; re-validate; adapt to the Gemini shape.
6. **Cloud step** only in hybrid/gemini mode (2.9).
7. **`build_review_draft`:** fuzzy-match items to products (now also against local names), match supplier (PAN first), convert BS date if needed, VAT default 13%, each item carries `local_names`.
8. **Duplicate check:** normalized bill number (lowercase letters/digits only: `SI0265` = `SI/0265`), within the supplier if known.
9. Row becomes `ready_for_review` (or `failed` with error message).
10. **Review:** `GET /{id}` (10-min signed image URL), `PATCH /{id}` edit.
11. **Approve** (`POST /{id}/approve`): re-check duplicate; create new products (name = `compose_name(name, local_names)`, `sku` = part number, `local_names`, cost = net price after discount, list = gross, selling 0, stock 0, reorder level 5); create supplier if new; compute `subtotal = sum(qty*net)`, `tax = subtotal*vat%`, `total`; insert purchase + items; add unpaid part to `suppliers.balance`; for each product `stock += qty`, `previous_cost_price = old`, `cost_price = net`, `list_price = gross`; **for existing products merge local names (no duplicates) and recompose the name**; mark approved. Reject and delete endpoints exist.

### 2.3 Preprocessing (`services/ocr/preprocess.py`, unchanged)
Grayscale; upscale if width < 1800 px (cubic); deskew (Canny + Hough, +/-15 degrees, rotate if >= 0.3 degrees); denoise (Non-Local Means h=10); adaptive threshold (Gaussian, block 35, C=15); remove table lines (morphological opening with long horizontal/vertical kernels, dilate 3x3, subtract); 20 px white border.

### 2.4 Tesseract (`services/ocr/engine.py`, unchanged)
`--oem 1 --psm 4`, `eng+nep` in one pass (falls back to English if a pack is missing). Output per word grouped into lines; confidence = mean word confidence. Nepali matters mainly for the label "Miti" and Devanagari digits; the parser mostly works with ASCII digits.

### 2.5 Line parser (`services/ocr/parser.py`)
Built from layered patches: functions like `parse_bill` are defined several times, each wrapping the previous, the last is live (v1 to **v13**). Say this honestly: it is a known code smell that grew by fixing real bills.
- **Header fields (regex):** bill number (several formats + supplier-specific `SB-`, `SI12345-12/34`), AD date (`20xx-MM-DD`, `DD/MM/20xx`, `a/b/yy`), Miti (BS date, for cross-check), supplier name (first of first 15 lines with PVT/LTD/TRADERS/...), supplier PAN (9 digits near VAT/PAN), customer PAN, totals (Total, Taxable, VAT 13%, Net).
- **Header patch v13 (new):**
  - `_bill_no_v13`: finds the number after "Invoice No/Bill No"; the separator regex tolerates `:` misread as `2`, `3`, `$`; maps look-alikes (`$1-0853`, `S1-0865`, `5/0332` all become `SI-0853`/`SI/...`); `S8` to `SB`; repairs a number split by a space (`00071 2/8384`).
  - `_ladali_suffix_v13`: if a Ladali-style `000600/8334` differs from the **Challan No** suffix on the same bill by exactly one digit, trust the Challan.
  - `_supplier_pan_v13`: the supplier's PAN is printed **above** the buyer block (lines with customer/buyer/party name/bill to/consignee); any PAN after that belongs to the buyer and is excluded.
  - Keeps the older reading unless the new one is longer or the old lacks `/` or `-`.
- **Rows:** header line matched by `escription|Part[il1]culars`; table ends at a stop line (`in words`, `total amount`, ...). **Amount = biggest money value in the row.** **Qty/rate solved arithmetically** (`q = round(amount/v)`, q 1..1000). Discount from `45 N2`-style codes or trailing percent; ROUND_HALF_UP. `_repair`: digit-swap fixes using a confusion map (`4/3` for `1`, `8` for `6/5`) accepted **only if exactly one fix explains the row**; v10 re-solves garbled rows with prefix-guessing and a "4x closer than runner-up" rule. HSN (4-8 digit classification code) kept separate from part number. Junk-row filters.
- Units were previously not stored; now the **unit** comes from the column reader and the adapter passes it through.

### 2.6 Validator (`services/ocr/validator.py`)
`TOL=0.06`, `VOTE_TOL=0.1`, `VAT=13%`. Checks: amount-in-words equals net (words to number, understands lakh/crore, fuzzy-fixes typos; **new OCR-typo map** `tivo/twa/tow` to two, `ane` to one, `flve` to five, `nlne` to nine, `elght` to eight, `slx` to six, `thlrty` to thirty); **taxable-amount voting** across up to 4 independent sources (`net - vat`, `words - vat`, printed taxable, `vat/0.13`), needing >= 2 independent groups; `vat_is_13_percent`; `taxable_plus_vat_equals_net`; date present / not in future; **AD-BS year gap must be 56 or 57**; bill number present; PAN 9 digits; items found; item recovery (one missing row net = taxable - others); rows sum matches taxable (within 0.5) and total; discount-misread repair (v10). Output: `status` ok/review, `checks`, `corrections`, per-row `needs_review` + `review_reason`.

### 2.7 NEW: Column reader (`services/ocr/table.py`, 967 lines)
**Why it was added** (docstring): the line parser reads Tesseract's text line by line, so on a tilted or low-resolution photo, numbers from neighbouring rows get mixed up. The new reader works on **pixel positions** instead of reading order. (It does **not** OCR individual cells in a grid; it OCRs whole **columns**, then lines them up into rows by height. It is English-only, `lang="eng"`.)

Public entry: `read_table(image_bgr, targets)` returns `{items, gross, supplier_names, top_text}`, or `None` if no table is found. Constants: working width `WIDTH=2400`; digit mode `--oem 1 --psm 6 -c tessedit_char_whitelist=0123456789.,`; text mode `--oem 1 --psm 6`.

**Step A: flatten the page** (`flatten`): shrink to 1000 px, blur + Otsu threshold, morphological CLOSE 15x15 so the paper is one blob, take the largest contour (if < 25% of image, fall back), convex hull then `approxPolyDP` (epsilon 0.02/0.03/0.05/0.08 of the perimeter) until exactly 4 corners, order corners TL/TR/BR/BL, accept only if a plausible rectangle (angles 75-105 degrees, opposite sides within 15%), then **perspective warp** to a flat rectangle. If anything fails: simple deskew.
**Step B: binarise:** resize to width 2400; adaptive threshold (block 51, C=15); `remove_table_lines` (old code) so ruling lines don't touch text.
**Step C: find the header row:** `image_to_data` with **psm 11** (sparse text) gives every word with its box; look only above 55% of page height; group words at similar height (tolerance `max(h,20) + 0.06*|dx|`, so tilted headers work); match against tolerant keyword regexes (qty/qnty/quant/qtv..., rate/price/mrp, amount/amt/total, disc/dis/less, description/particular/goods, `part no`, HS/HSN, unit/uom, net). Needs `amount`, or both `qty` and `rate`. No header found: return None. Table ends at the first "Sub/Total/Gross/Basic/Taxable/Words/Remarks" word, else at 55% of page height.
**Step D: find columns:** ruling lines via vertical morphological opening (kernel `(1, max(30, H//4))`) and a **column projection profile** (keep x positions where line pixels exceed 25% of height); ink segments via a second projection profile (blank gaps of >= 10 px separate columns); leftmost "amount" title = gross, rightmost = net (after discount); if the Amount title wasn't read use the rightmost ink run; if two titles share one ink run split it (gaps 6, 4, 3, 2 px, else cut at left title's right edge + 12). If qty/rate titles are missing, `_guess_number_columns` walks ink runs right to left from Amount and keeps a run when >= 60% of its lines are numeric.
**Step E: read each numeric column** (qty, rate, amount, disc, net) separately: crop, Otsu, 25 px border, Tesseract **with the digit whitelist** (stops 5 being read as S and 0 as O).
**Step F: build rows:** the **Amount column is the anchor** (every row has one). Row height = median spacing. Header slope fitted by least squares. `_align` pairs each other column's lines to the anchor rows by y-height (two nearest-row passes, least-squares shift, greedy assignment, limit 0.5 row height), which tolerates curled paper or feed offsets. Trailing Sub-Total-like rows are dropped.
**Step G: validate and repair numbers** (`_row_options`, `_pick`): row is "sure" if `qty*rate = amount` (tolerance `max(0.6, 0.0015*amount)`) or the amount is already after discount. Otherwise candidate repairs: `rate = amount/qty`; `qty = amount/rate` (integer within 0.02); `amount = qty*rate`; one-digit differences are preferred. When several rows are ambiguous (max 12) `_pick` brute-forces combinations and keeps the one whose sum matches a printed total (tolerance `1.0 + 0.02*rows`). **The printed total is used as an oracle** (a checking answer).
**Step H: text columns:** part number, description (multi-line names: lines below the anchor row within 1.8 row-heights continue the name), and unit (from a Unit column, or right of Qty; matched to a unit list by `difflib` cutoff 0.6). Cleaning: strip symbols and OCR crumbs; remove part code / HS code from the front of the name; fix digit look-alikes (`O`->0, `S`->5, `I`/`L`->1, `Z`->2, `B`->8) only inside digit runs; **drop HS codes** (8-digit codes starting with chapters 27, 38, 39, 40, 68, 69, 70, 73, 74, 76, 83, 84, 85, 87, 94); reject parts shorter than 4 characters; if >= 3 long part numbers and >= 60% share the same first 3 letters, mend one that differs by one prefix character. All text work is in try/except: **names are a bonus, numbers are never lost if text reading fails.**
**Step I: final repairs:** `_digit_fix`: if every row has qty/rate/amount but the gross doesn't match any target, find exactly one row where a one-digit change in both rate and amount fixes the sum (the same digit misread in rate and amount keeps `qty x rate = amount`, so only the total reveals it). `_infer_discount`: if the net is above the printed taxable, infer one uniform percentage (< 60) or the single row that becomes a whole-number percent. **Supplier candidates:** re-OCR the top 35% of the page with psm 3, rank lines by letter height (x1.3 for Pvt/Ltd/Traders), filter phone/email/VAT/address words, tidy ("TAX INVOICE" removed), score by `name_score`.
Rows that were repaired get `needs_review=True` and a `repair_note`.

### 2.8 How the two readers are combined (`pipeline.py: extract_selfhosted`)
```
image -> preprocess -> Tesseract (eng+nep, psm 4) -> parse_bill -> validate_bill     [line reader, always]
                    \-> targets = printed totals (+ words/1.13)
original image -> read_table(targets)                                                  [column reader, always]
header fields: if the line parser missed bill no / PAN, retry on the word layout (top_text) with v13 helpers
supplier name: best by name_score among line-parser name and table candidates
if table and line rows do NOT add up to targets:
     if column rows add up      -> use column rows (note: "read column by column...")
     elif column rows are clearly better (every row self-consistent, line reading has gaps, >= as many rows)
                                -> use column rows (note: "check the rows against the paper")
else keep line rows
_merge_text: pair rows by equal amount (or by position if counts match); take the better description (_name_quality: good tokens +len, bad tokens -4 each); drop HS code from part_no; prefer column-read part no; copy unit
validate_bill again -> to_gemini_shape
```
"Adds up" = `rows_add_up`: every row has qty and rate and gross (or net) sum is within `1.0 + 0.03*rows` of a target.
**Key viva line:** *"Two independent readers; the printed totals decide who wins; when they disagree and neither adds up, the row is flagged for the human."*

### 2.9 Modes (`OCR_ENGINE`)
| Mode | Behaviour |
|---|---|
| `selfhosted` (default, deployed) | Tesseract + column reader, no API key |
| `hybrid` | self-hosted first; Gemini only if needed (no items, no net, confidence < 35, an item lacks qty/rate, or `sum(qty*rate)` differs from the printed total by more than `2.0 + 0.006*sum(qty)`) |
| `gemini` | original paid flow |
Hybrid: Gemini result cached by SHA-256 of the image (`.ocr_cache/`); cloud rows verified against the self-hosted printed total; if Gemini is down the self-hosted result is returned with a warning. Gemini model `gemini-3.6-flash`, strict JSON output, 4 tries (waits 2/5/15 s), 60 s timeout. **Hybrid logic was not changed** in the new work; it now runs on the improved result. `render.yaml` deploys `selfhosted` with no Gemini key.

### 2.10 Adapter and review notes (`adapter.py`)
Same output shape as Gemini. **New:** `unit` now passed through; validator check names are turned into plain sentences for the shop owner (`_friendly`: "Bill number was not read. Enter it from the paper.", "Item amounts add up to Rs X but the bill says Rs Y (difference Rs Z). An item may be missing or misread.", etc.); internal checks (`taxable_consistent`, `ad_bs_years_consistent`, `item_recovery`) are hidden; `_friendly_correction` rewrites "net_amount missing -> X (from amount in words)" as "Bill total taken from the amount in words: Rs X."; notes are de-duplicated. Always-present notes: OCR confidence, printed bill total, "The bill number cannot be verified automatically".

### 2.11 NEW: Measuring accuracy (`ocr_eval/score.py`)
It exists, so you can now say OCR was evaluated. How it works:
- Run `python ocr_eval/score.py [-v] [--tag v12]`. Bills (`ocr_eval/bills/*.jpg`) and hand-checked labels (`ocr_eval/labels/*.json`) are **git-ignored** (they contain supplier bank details and PANs).
- It calls `extract_selfhosted` on every bill (so it tests the **self-hosted pipeline only**, not Gemini/hybrid) and scores **the dict the review screen receives**: a field counts as right only if the user would not need to edit it.
- **One point per field printed on the bill:** header = supplier name, supplier PAN, bill number, date; per row = name, part no, unit, qty, rate, discount %. A row the OCR missed loses all its points; a junk row the OCR added costs one point each. **Field accuracy = right / (fields on bills + junk rows).**
- Row pairing: numbers count most (a row with right qty x rate is the right row even if the name is garbled), then name and part no, then position.
- Comparison rules: text ignores case/space/punctuation (similarity >= 0.9); supplier name ignores Pvt/Ltd/Co; PAN and bill number compare letters+digits only; date must match exactly; numbers within 0.011; units normalised (`pieces` = `pcs`); fields the label leaves empty are not scored.
- Extra outputs: rows with **every** field right; **bill total right** (computed `sum(qty*rate*(1-disc))*1.13` vs the label's net amount within `1.0 + 0.03*rows`); per-field accuracy; `--tag` saves `results/<tag>.json`.
- **YOUR MEASURED RESULT** (`python ocr_eval/score.py -v --tag final`, 20 bills, 112 item rows, 740 scored fields, self-hosted pipeline):
  - **Field accuracy 74.3%** (550/740); **bill total right 70%** (14/20); **rows with every field right 18.8%** (21/112); junk rows 0.
  - Per field: supplier name 90.0%, bill number 90.0%, date 90.0%, supplier PAN 75.0%, qty 88.4%, rate 84.8%, discount 83.0%, item name 74.1%, **part number 52.3%, unit 52.5%**.
  - **Reading it:** header fields and the numbers are the strong part (83-90%); part numbers, units and long item names are the weak part. "Every field right" is a strict test (all 6 row fields must match), so 18.8% is low mostly because part number and unit fail often.
  - **Worst bills:** bill01, 03, 04, 14, 17, 19 had wrong totals. Typical causes seen in the `-v` output: part numbers missing a leading letter (`J2443` read as `12443`, `TVS29...` read as `S29...`); the Unit column printed as "Unit" not read at all (bills 17-20); in bill19 the quantity and rate columns were misassigned (rate read as 24.34, which is the discount).
  - **Why this supports the design:** only about 1 row in 5 is perfect, so the human review step is necessary, not optional.
  - **Caveats:** 20 bills from a handful of suppliers, so the percentages are rough (one bill moves the score several points); labels were corrected by hand from OCR drafts, which can hide some OCR errors; self-hosted pipeline only.
- **Honest caveats to say yourself:** (1) the parser patches are named after specific bills/suppliers (Sipradi, Ladali, GBAS), so numbers on the bills you tuned on are optimistic; report a **held-out** set if you have one; (2) the metric is field-level on the review-screen dict, not character-level; (3) the Windows Tesseract path in the script shows it was run on a Windows machine, so the Linux/Docker result may differ slightly.

### 2.12 Local names and product matching
- A product can have `local_names` (comma-separated nicknames, e.g. "patta"). `compose_name("Spring Leaves","patta")` gives `Spring Leaves / Patta` (safe to re-run: old suffixes are removed first). Used when creating/updating products and on bill approval.
- `fuzzy_match` now compares a bill item against the product name, each part of `Name / Local`, and every local name (`_aliases`). Threshold stays **0.90** (raised from 0.72 because "Oil Filter" matched "Air Filter"). On approval, new local names are merged into an existing product without duplicates.
- Product search now also searches `sku` and `local_names`.
- **This is not Nepali OCR.** It is manual aliasing so a Nepali/colloquial name on a bill links to the same product.

### 2.13 BS to AD and other details
- `utils/nepali_date.py` wraps `nepali-datetime` (BS 1975-2100). In the self-hosted path the adapter reports the **printed AD date** and uses Miti only for the AD-BS year-gap check; BS conversion mainly applies to Gemini output. If a bill has only BS, the date is empty and defaults to today at approval.
- Supplier match: exact 9-digit PAN, or PAN differing by one digit (asks to confirm), else fuzzy name.
- `multipage.py` still exists but is never imported: single image only.

### 2.14 OCR limitations (say them first)
- Parser and column reader depend on **English keyword lists** (header titles, units, end-of-table words, HS chapters, supplier-name words), the **hard-coded 13% VAT**, and vendor-specific bill-number patterns.
- Column reader needs a found header (Amount or Qty+Rate) in the top 55% of the page, and a roughly rectangular visible sheet for the perspective warp; tables are cut at 55% page height if no end-of-table word is found; brute-force repair covers at most 12 ambiguous rows.
- **Heavy runtime:** the old path made one Tesseract call; now there are many (header words, five numeric columns, part, description, unit, supplier block) on a small free server.
- Repairs are heuristic and can "fix" a row to match a total; those rows are flagged `needs_review` with a note, and a human approves.
- Many `try/except` blocks, so failures are silent apart from log warnings.
- Weak on blur, glare, folds, thermal fading, handwriting. Nepali script not read by the column reader. No PDF, no multi-page.
- Approve is not one DB transaction; reject doesn't check status.
- Tests: only `tests/test_health.py` (the evaluation script is not part of CI).

### OCR glossary (each with "In ours")
- **OCR:** turning an image of text into text. *In ours:* the Scan Bill feature; supplier bills become draft purchases.
- **Tesseract:** open-source OCR engine (v4+ uses an LSTM neural net). *In ours:* called through `pytesseract` in `engine.py` (whole page, `eng+nep`, psm 4) and in `table.py` (columns, header words, supplier block), always `--oem 1`.
- **pytesseract:** Python wrapper that calls the `tesseract` program. *In ours:* `image_to_data` in both modules.
- **Language pack (`eng`, `nep`):** trained data per language. *In ours:* Dockerfile installs `tesseract-ocr-eng` and `tesseract-ocr-nep`; the page path uses both together, the column reader only English.
- **OEM / PSM:** which engine / how Tesseract assumes the page is laid out. *In ours:* OEM 1 everywhere; PSM 4 (single column of variable text) for the full page, PSM 6 (uniform block) for numeric/text columns, PSM 11 (sparse text) to find header words, PSM 3 (automatic) for the supplier block.
- **`image_to_data` / TSV / bounding box:** per-word records with position (left, top, width, height) and confidence. *In ours:* `table.py` uses the boxes to find headers, columns and rows; `engine.py` regroups words into lines.
- **Confidence:** Tesseract's own 0-100 certainty per word. *In ours:* mean confidence shown in the review note; < 35 = unreadable in hybrid mode; words <= 20 are dropped when reading the supplier block.
- **Whitelist (`tessedit_char_whitelist`):** restrict allowed characters. *In ours:* digits `0123456789.,` for qty/rate/amount/discount columns so `5` is never read as `S`.
- **Preprocessing:** cleaning the image first. *In ours:* `preprocess.py` (2.3).
- **Grayscale, upscaling (cubic), EXIF orientation:** drop colour / enlarge so characters are ~30 px tall / metadata says how the phone was held. *In ours:* all three in `preprocess.py` / `engine.load_image`.
- **Deskew; Canny edge detector; Hough transform:** straighten tilt; find edges; find straight lines by voting. *In ours:* `estimate_skew` uses Canny (50/150) + HoughLinesP to measure the tilt from table borders; `table.py` calls it as the fallback when perspective warping fails.
- **Perspective warp (homography), contour, convex hull, `approxPolyDP`:** map a 4-corner shape to a rectangle; outline of a blob; tightest outline; simplify to few corners. *In ours:* `flatten()` in `table.py` finds the paper's four corners and flattens the photo.
- **Denoising (Non-Local Means):** average similar patches to remove grain. *In ours:* `fastNlMeansDenoising(h=10)` in `preprocess.py`.
- **Otsu / adaptive threshold (binarization):** automatic global / local black-white cut-off. *In ours:* adaptive (Gaussian, block 35/C=15 in `preprocess.py`; block 51/C=15 in `table.py`) for uneven lighting; Otsu for paper detection, column crops, and the supplier block.
- **Morphological operations (open, close, dilate), kernel:** shape-based pixel operations with a small structuring shape. *In ours:* opening with long thin kernels isolates ruling lines (then erased, or used as column edges); closing merges the paper into one blob.
- **Projection profile:** count ink pixels along one axis. *In ours:* `_vlines` and `_ink_segments` find column boundaries from vertical profiles.
- **Least-squares fit:** best straight line through points. *In ours:* header slope and the row-alignment shift (`np.polyfit`, `lstsq`).
- **Regex:** pattern language for text (`[0-9]{9}` = nine digits). *In ours:* nearly everything in `parser.py`: bill numbers, dates, PAN, header titles, HS codes. `re.ASCII` makes `\d` match only 0-9.
- **Heuristic:** rule of thumb that usually works. *In ours:* thresholds like "biggest number in the row is the amount", the 0.90 match, tolerances `1.0 + 0.03*rows`.
- **Parsing:** raw text to structured fields. *In ours:* `parse_bill`, `_build_item`.
- **Anchor column:** the reference used to define rows. *In ours:* the Amount column in `table.py`.
- **Oracle / checksum:** a known-good value used to choose among candidates. *In ours:* the printed total (and amount in words / 1.13) selects which digit repair is right.
- **PAN:** 9-digit Nepali tax ID. *In ours:* `supplier_pan` for supplier matching (`match_supplier_by_pan`), validator `supplier_pan_9_digits`; buyer PAN is excluded.
- **VAT / taxable / net:** 13% tax; amount before VAT after discount; taxable + VAT. *In ours:* validator checks `taxable + vat = net` and `vat = 13% of taxable`; approval recomputes `tax = subtotal * vat%`.
- **HSN / HS code vs part number (SKU):** 4-8 digit customs classification vs supplier's item code. *In ours:* HS codes are recognised (`HS_CODE`) and dropped so they never become the part number; the part number is saved as `products.sku`.
- **Lakh / crore:** 100,000 / 10,000,000. *In ours:* the amount-in-words parser understands them.
- **BS / AD / Miti:** Bikram Sambat calendar (~56-57 years ahead) / Gregorian / the "date" label. *In ours:* year-gap check (56 or 57), `nepali-datetime` conversion for Gemini output.
- **Fuzzy matching:** approximate string similarity (0-1). *In ours:* `difflib.SequenceMatcher`; product match >= 0.90 (including local names), units cutoff 0.6, amount-in-words typo repair cutoff 0.67.
- **Deduplication:** don't record the same bill twice. *In ours:* normalized bill number per supplier, at upload and at approval.
- **Reconciliation / voting / tolerance:** make independent numbers agree; majority of independent sources wins; allowed rounding difference. *In ours:* taxable-amount voting; tolerances 0.06, 0.1, 0.5, `1.0 + 0.03*rows`.
- **Round-half-up:** 0.5 rounds up (accounting) unlike Python's banker's rounding. *In ours:* `_half_up` with `Decimal` for discounts.
- **Human-in-the-loop:** a person verifies output before it becomes real data. *In ours:* `pending_documents` review then approve.
- **Cache (SHA-256 key):** store result under the image's fingerprint. *In ours:* `.ocr_cache/<sha256>.json` for Gemini results.
- **Monkey-patching / layered patches:** redefining a function to change behaviour. *In ours:* `parse_bill` is wrapped by v1...v13; `validate_bill`, `clean_desc` too.
- **Ground truth / label / field accuracy:** hand-checked correct answers / one point per field / right over total. *In ours:* `ocr_eval/labels/*.json` and `score.py`.
- **Held-out set:** data never used for tuning. *In ours:* what you should report separately to avoid optimism.

### Likely OCR questions
- *Why Tesseract and not only Gemini?* Cost (no per-scan fee), privacy (supplier names/PANs/prices stay on your server), reliability (Gemini returned 503/429), cheap hosting. Gemini is an optional fallback.
- *Why a second reader?* The line parser mixes up numbers on tilted/low-res photos; the column reader uses positions. They are independent, so the printed totals can arbitrate.
- *What if both are wrong?* Rows are flagged `needs_review` with plain-language notes; a human approves; nothing is saved automatically.
- *How accurate is it?* Give your measured numbers from `ocr_eval` and the caveats (2.11). Don't guess.
- *Why not train your own OCR model?* Needs thousands of labelled bills and GPUs; Tesseract plus validation is practical.
- *Why English-only in the table reader?* Bill headers and numbers are Latin/ASCII; Nepali text is used only for labels like Miti; supporting it would need Nepali keyword lists and more tuning.
- *Isn't tuning on your sample bills overfitting?* Partly; that's why held-out evaluation matters. The patches are explicit about specific suppliers.

---

## PART 3. The 6 AI models

### 3.1 The pipeline
1. `ml/retrain.sh` (cron line `0 2 * * 0` documented in the script header; **no scheduler installed in the repo**) reads shop ids from `ml/shops.txt`.
2. `ml/export_shop.py` exports tables **pseudonymised** (names become "Customer 001"; phone/email/address/PAN dropped; store id becomes "S1"); stops if exported row counts don't match the DB.
3. `ml/train_shop.py` runs `ml/train_cells.py` in blocks: "core" blocks must succeed; each "helper" (sales_trend, cash_flow, comparison, churn, credit, restock, anomaly, report) is isolated. Minimum-history guards: 40 weeks (forecast), 36 (restock), 270 days (churn), 330 (credit), 30 customers, 200 invoices.
4. **Safe publish** (`train_shop.py:88-103`): validate each `*_results.json` parses as a JSON dict; copy to `<file>.tmp` next to the target; `os.replace` (atomic rename). A failed helper leaves old files. `retrain_status.json` records each helper (last run 2026-10-04: 8 ok, 0 failed). Atomic **per file**, not across files.
5. The temp folder with exported data is deleted after each run.
6. Serving (`services/ai_results.py`): store id checked by regex (blocks path traversal), cached by file modification time, **404 "No AI results for this shop yet"** if none, real names/phones/balances added from Supabase at request time.
7. `POST .../train` endpoints do **not** train.

**Dataset:** 2025-10-04 to 2026-10-03, 1100 invoices, 52 complete weeks (Sunday-Saturday), 51 of 62 customers have invoices, 1300 active products. `rev_w` = billed invoice totals (includes unpaid credit sales).

### 3.2 Summary (numbers from `ml/results/<store>/*.json`)
| Helper | Algorithm | Question | Key numbers |
|---|---|---|---|
| Business Direction (sales_trend) | Prophet + Optuna (30 trials) | Growing or shrinking? Next 8 weeks? | holdout MAE 30,723 vs naive 30,681; trend -1.8% = stable |
| Cash Flow Forecast | Prophet (default) + weekday split + flat expenses | Daily money in/out, 30-90 days | MAE 30,629 vs naive 30,681 |
| Restock Advisor | One pooled LightGBM (Poisson), 1300 products | Units sold in next 4 weeks | MAE 1.236 vs 1.254/1.264; wMAPE 1.82 |
| Customers Leaving (churn) | LightGBM + SHAP | Who stops buying within 60 days? | AUC 0.642 vs LR 0.683, recency-only 0.652 |
| Udharo Advisor (credit) | **Logistic Regression** served | Safe to give more credit? | AUC 0.965, P 0.75, R 0.79, F1 0.73 (45 customers) |
| Unusual Transactions | Isolation Forest | Which invoices look wrong? | recall 42% on injected errors vs 3% chance |

### 3.3 Sales trend (`train_cells.py:118-161`)
Prophet: linear trend, weekly/daily seasonality off, **yearly seasonality OFF** (one year of data), no holidays (the Nepali holiday helper exists but is unused), 80% interval, forecast clipped at 0. Optuna tunes only `changepoint_prior_scale` (0.001-0.2, log scale) using 6-fold expanding-window CV with 4-week horizon; best 0.00136 (nearly a straight line). Baseline: mean of last 8 weeks. Holdout = last 8 weeks: Prophet MAE 30,722.85, RMSE 39,509, naive 30,680.91; average week Rs 398,909, so MAE ~7.7%. Insights: direction (growing > +3%, declining < -3%), best/worst week, best month Jun / worst Sep. Weaknesses: 52 weeks; Optuna's CV overlaps the holdout (code admits it); no better than naive.

### 3.4 Cash flow (`:163-204`)
Default Prophet forecasts 14 weeks; split across weekdays by historical share; expenses = average of last 26 weeks of the **expenses table** (Rs 2,557/day flat); net = revenue - expenses. `INCLUDE_PURCHASES = False`, so **stock purchases aren't cash-out** and net looks ~95% of revenue (unrealistic). Revenue is billed, not collected.

### 3.5 Model comparison (`model_comparison_weekly_revenue.json`)
6-fold CV, 4-week horizon: Prophet-tuned 30,412; blend 30,454; Holt damped 30,785; naive 30,794; Prophet-default 31,172. ~1.2% better than naive. Code prints: "no model clearly beats the naive baseline (differences are within noise)". 80% interval coverage 79% (well calibrated).

### 3.6 Churn (`:332-408`)
- **Label:** at cut-off T, `y=1` if no purchase in the next **60 days**; snapshots every 14 days (16 cut-offs).
- **Features (8):** `recency_days`, `frequency_30d`, `frequency_90d`, `monetary_90d`, `avg_gap_days`, `recency_vs_gap` (how overdue vs their rhythm), `avg_invoice`, `unpaid_ratio`. All from invoices before T.
- **Split:** temporal with purge gap; train 244 rows, test 350 (churn rate 0.16 / 0.134, ~47 positives). Rows repeat customers, so effective sample is smaller.
- **LightGBM (untuned):** 150 trees, lr 0.05, 6 leaves, min_child_samples 15, subsample/colsample 0.8, `reg_lambda` 5.
- **Metrics:** AUC 0.642; avg precision 0.21 (chance 0.134); precision@0.5 0.278; recall@0.5 0.106; baselines recency-only 0.652, logistic regression 0.683. **Served model is below both.**
- No imbalance handling in live code. Final refit on all 594 rows. Risk: HIGH >= 0.5, MED >= 0.25: 3 high, 7 medium, 41 low, 6 already lapsed.
- `is_churned` = `recency_days > 60` (a fact). The router relabels: lapsed -> `stopped_coming`; active with model risk high/medium -> `slowing_down`; others -> `buying_regularly`. The page's "high risk" count is not the model's raw count.
- **SHAP** top-3 reasons per customer ("Share of recent purchases left unpaid: 70% (raises the risk)"). Poster figure 4: Customer 024, 63%.
- Weaknesses: 51 customers; weak AUC; "unpaid ratio" is the top driver, so it partly means "owes money".

### 3.7 Credit scoring (`:410-496`)
- **Label:** bad if >= 30% (`BAD_UNPAID=0.30`) of value bought after 2026-04-06 is still unpaid.
- **Features (9):** `n_purchases`, `total_value`, `avg_invoice`, `max_invoice`, `unpaid_ratio`, `share_unpaid_invoices`, `purchases_90d`, `avg_gap_days`, `recency_days` (180-day window).
- **Training:** customers with >= 3 earlier purchases: **45 customers, 8 bad (17.8%)**. Evaluation: repeated stratified k-fold (10 repeats), **not temporal**.
- **LR vs LightGBM:** LightGBM AUC 0.9466 (std 0.0885), accuracy 0.822, precision/recall/F1 **0** (flags nobody at 0.5; 82% = predict-everyone-fine). LR AUC **0.965** (std 0.0756), accuracy 0.918, precision 0.75, recall 0.79, F1 0.73. Rule: LightGBM served only if AUC > LR + 0.01 (simpler wins ties), so **LR is served**.
- **LR setup:** median imputation, StandardScaler, L2, C=0.5; weights in `credit_logistic_regression.json`; reasons = standardised value x coefficient, top 3.
- **Scoring:** `credit_score = round(100*(1-p_bad))`; grades A >= 80 (2.0x limit), B >= 65 (1.0x), C >= 50 (0.5x, review), D >= 35 (0.25x), F < 35 (0). Max credit = (limit or 10,000) x multiplier rounded to 1000. 11 customers with no purchases in 180 days get neutral p=0.5 (score 50, C) - a default, not a prediction. Result: A 39, B 1, C 13, D 0, F 9.
- Caveats: n=45, 8 positives; random CV; high AUC reflects persistence of unpaid habits; the 30% cut-off and multipliers are business choices.

### 3.8 Restock Advisor (`:498-571`)
Pooled LightGBM regressor, **Poisson** objective (counts with many zeros), 250 trees, lr 0.05, 15 leaves, min_child_samples 40. **Features (9):** `sold_last_week`, `sold_4w`, `sold_8w`, `sold_13w`, `weeks_with_sales_13w`, `weeks_since_last_sale`, `category_sold_4w`, `price` (lifetime median: mild look-ahead), `cost` (current). Target: units in next 4 weeks. 36 weekly cut-offs, test = last 12, 4-week purge gap. **Metrics:** LightGBM MAE 1.2357, RMSE 2.886; baseline 13-week avg x4 MAE 1.2544 / RMSE 3.30; repeat-last-4-weeks MAE 1.2644 / RMSE 4.08; **wMAPE 1.82** (> 1 = worse than predicting zero; gain mostly fewer big misses). Output: weekly = demand/4 (flat); confidence low 1179 / medium 121 / high 0. **The API overrides the file's 8/12-week rule with a 2/4-week shop policy** (overridable by `?cover_weeks=&target_weeks=`). No SHAP here.

### 3.9 Anomaly detection (`:573-650`)
Isolation Forest (300 trees). **Features (10):** `log_total`, `n_items`, `max_line_share`, `discount_ratio`, `unpaid_ratio`, `tax_ratio`, `price_dev`, `max_qty`, `cust_dev`, `weekday`. **Flags exactly the lowest 3%** of scores (33 of 1100), so the count is a review budget, not a finding. Validated by injecting 33 fake errors: recall spike 0.27, heavy discount 0.64, wrong price 0.36, overall **0.42 vs 0.03 chance**. Reasons are rule-based text ("Bill total is zero", "Discount is X% of the bill", "VAT is X% ... normally 13%"), severity 1-100. Caveat: injected errors reflect the author's assumptions; many top flags are zero-total invoices.

### 3.10 Legacy (don't quote)
`ml/training/*.py`, `scripts/train_all.py`, `ml/models_saved/*`: first iteration with random splits, class weights, a hard-coded store id, noisy numbers (churn AUC 0.75 with 2 test positives; credit AUC 0.984 on ~16 test customers). Say: "I replaced them with temporal snapshots and baselines because the original numbers were noise."

### ML glossary (each with "In ours")
- **Model:** formula learned from past data. *In ours:* six, listed in 3.2.
- **Training / test data:** learn on one part, score on unseen part. *In ours:* e.g. churn train 244 rows / test 350; restock last 12 weeks.
- **Temporal split vs random split; purge gap:** train on earlier dates, test on later; random shuffling can leak the future; a gap so label windows don't overlap. *In ours:* churn and restock are temporal with purge gaps; credit uses random repeated k-fold (only 45 customers).
- **Cross-validation / stratified k-fold / expanding-window CV:** repeat splits and average / keep class share equal / always train on the past. *In ours:* credit = repeated stratified k-fold (10 repeats); Prophet comparison = 6 expanding-window folds.
- **Leakage:** answer/future info in the features. *In ours:* features use only invoices before the cut-off T. Mild leaks: restock `price` is a lifetime median; Optuna CV overlaps the holdout.
- **Overfitting / regularisation:** memorising small data / penalties against it. *In ours:* tiny trees (6-15 leaves), `reg_lambda` 5.
- **Baseline:** simple method a model must beat. *In ours:* naive 8-week mean, recency-only churn, logistic regression, 13-week average x4.
- **Logistic regression:** weighted sum of features turned into a probability. *In ours:* the served credit model and a churn baseline.
- **Gradient boosting / LightGBM:** many small trees, each fixing the last. *In ours:* churn, restock (and credit comparison); parameters in 3.6/3.8.
- **Poisson objective:** loss for non-negative counts. *In ours:* restock.
- **Prophet / changepoint prior / Holt damped trend:** forecasting with trend; trend flexibility; trend that flattens. *In ours:* sales trend and cash flow; Holt is a comparison.
- **Optuna / TPE / hyperparameter:** automatic search / its smart sampler / setting chosen before training. *In ours:* only the Prophet changepoint scale, 30 trials.
- **Isolation Forest / contamination:** unsupervised outlier detector / expected anomaly share. *In ours:* 300 trees; contamination implemented by hand as the 3% score quantile.
- **SHAP:** per-prediction feature contributions. *In ours:* churn top-3 reasons and poster figure 4.
- **AUC:** chance a random positive ranks above a random negative (0.5 coin, 1.0 perfect; threshold-free). *In ours:* churn 0.642, credit LR 0.965.
- **Precision / recall / F1 / accuracy / threshold:** of flagged how many right / of real positives how many found / their harmonic mean / share correct / probability cut-off. *In ours:* credit LR 0.75/0.79/0.73, threshold 0.5; accuracy is misleading (LightGBM 82% with recall 0).
- **Average precision:** area under the precision-recall curve; chance = positive rate. *In ours:* churn 0.21 vs 0.134.
- **Class imbalance / `class_weight`:** rare class counted more. *In ours:* not used in the live code (legacy scripts used it).
- **MAE / RMSE / wMAPE / MAPE:** average absolute miss / squares big misses / total abs error over total actual / mean % error. *In ours:* MAE and RMSE for forecasts, wMAPE for restock (MAPE not used).
- **Naive forecast:** "next weeks look like the recent average". *In ours:* last-8-week mean.
- **Calibration / prediction interval:** probabilities or bands that match reality. *In ours:* 80% band covers 79% of actual weeks.
- **RFM / percentile rank:** Recency, Frequency, Monetary scoring. *In ours:* `scripts/classify_customers.py` (not ML).
- **Pseudonymisation:** replace names with labels. *In ours:* `export_shop.py`.
- **Atomic replace (`os.replace`):** swap a file in one step. *In ours:* `train_shop.py` publish.

### Likely ML questions
- *Why is churn LightGBM worse than logistic regression?* Only 51 customers and ~600 correlated rows; simple models do as well. Reported openly; the value is explainable ranking.
- *Why serve LR for credit?* Interpretable, higher AUC (0.965 vs 0.947), LightGBM flagged nobody at 0.5.
- *Is 0.965 trustworthy?* 45 customers, 8 bad, std 0.076, random CV: indicative, not conclusive.
- *Why not accuracy?* 82% accuracy equals "everyone is fine"; precision/recall/AUC show what it hides.
- *Why no seasonality/holidays?* One year of data.
- *Why does net cash flow look high?* Purchases excluded; billed not collected.
- *Why not live training?* Free server is small; weekly retrain is enough; precomputed = fast. Cost: staleness up to a week.
- *Why JSON files?* Batch-written, read-only, isolated from the live DB, atomic publish. At scale: a table or object storage with versioning.
- *Does one shop affect another?* No: per-shop export, per-shop results folder, UUID-checked path, store id from the session.

---

## PART 4. Poster figures
- `poster_figures/figure3_forecast_vs_actual.png`: weekly revenue vs 8-week holdout forecast with 80% band ("MAE Rs 30.7k"); the forecast is nearly flat: "the model predicts the average because the series is stable".
- `poster_figures/figure4_shap_explanation.png`: SHAP bars for Customer 024 (risk 63%); red raises risk; top driver unpaid share 70%, then average bill ~Rs 59,670, usual gap 22 days, days since last buy 49.

---

## PART 5. Weak points (own them)
**Security/access:** permission system (~40 keys) stored but `require_permission` is **not used by any router**; unauthenticated `/api/admin/model-status`, `/training-progress`, `/classify-products/preview`; hard-coded store id in admin/classification/train_all; `/platform-admin/bootstrap` open until the first admin exists; several routers use the service-role client (bypasses RLS, relies on `.eq("store_id")`); login returns `str(e)` on failure; no rate limiting; cached per-token Supabase clients live in memory (bounded at 256).
**Data integrity:** no DB transactions (invoice and bill-approval are multi-step); read-modify-write balances/stock can lose updates; stock can go negative; P&L counts only paid invoices and uses purchases as cost; no pagination (1000-row limit).
**Code quality:** layered patches in OCR, `.bak` files (now git-ignored), dead code (`ai_models.py`, `multipage.py`, `invoice_service.py`), duplicate `/item-details` route, logic inside routers; README out of date (mentions XGBoost, wrong AI paths).
**Tests:** only 2 health checks; CI ignores failures; OCR scoring is a manual script.
**ML:** tiny single-shop data; weak vs baselines; no scheduler installed.
**Improvements to propose:** enforce permissions server-side; atomic Postgres functions; pagination; real tests (parser unit tests + OCR evaluation in CI with a held-out set); more shops and 2+ years for seasonality/holidays; include purchases and collections in cash flow; results in a table/object storage with versioning; cloud scheduler.

---

## PART 6. One-line answers to memorise
1. **What is it?** A back-office and AI backend for Nepali retail shops: billing, stock, udharo, purchases, reports, bill scanning, six AI helpers.
2. **Why JSON for ML results?** Weekly batch writes, read-only afterwards, isolated from the live DB, atomic publish.
3. **Is publish atomic?** Per file: write `.tmp` in the same folder, then `os.replace`.
4. **How does OCR work?** Clean the image, Tesseract, a line parser and a column reader, then validate with the bill's own arithmetic; the printed total arbitrates; a human approves.
5. **Why two readers?** Different failure modes; totals decide which to trust.
6. **How accurate is OCR?** On 20 labelled bills (112 rows) field accuracy is 74.3% and the bill total is right on 70% of bills; header fields and qty/rate are 83-90%, part numbers and units about 52%. Only 19% of rows are fully right, which is why every scan goes through human review.
7. **Why logistic regression for credit?** Interpretable and best AUC (0.965 vs 0.947) on 45 customers.
8. **Why not accuracy?** Imbalance: "everyone is fine" scores ~82%.
9. **Do forecasts beat naive?** About 1%, within noise; they add calibrated intervals.
10. **How is leakage avoided?** Features only from before the cut-off; temporal splits with purge gaps.
11. **Why Tesseract?** Free, private, no API dependency; Gemini optional.
12. **How do you stop a wrong OCR read being saved?** Validator flags, `needs_review` notes, duplicate check, human approval.
13. **How are shops separated?** `store_id`, membership check on `X-Store-Id`, RLS, per-shop result folders.
14. **What would you improve first?** Server-side permissions and atomic DB functions, then tests and more data.

---

## PART 7. All metrics: what, how calculated, our values (ML run of 2026-10-04, OCR run on 20 bills)

TP/FP/FN/TN = true/false positives/negatives. "Positive" = churned customer (churn) or bad-credit customer (credit).

### 7.1 Classification metrics (churn, credit)
| Metric | Formula | Meaning | In ours |
|---|---|---|---|
| Accuracy | (TP+TN) / all | Share of predictions that are right. Misleading with imbalance | Credit LR 0.918; credit LightGBM 0.822 (= "predict nobody is bad") |
| Precision | TP / (TP+FP) | When it raises an alarm, how often it is right | Churn@0.5 0.278; credit LR 0.75 |
| Recall | TP / (TP+FN) | Of real positives, how many it catches | Churn@0.5 0.106; credit LR 0.79 |
| F1 | 2PR / (P+R) | Balance of precision and recall | Credit LR 0.731 |
| ROC-AUC | Probability a random positive scores higher than a random negative (area under TPR-vs-FPR curve; sklearn `roc_auc_score`). 0.5 = coin flip, 1 = perfect. Needs no threshold | Ranking quality | Churn 0.642; credit LR 0.965, LightGBM 0.9466 |
| AUC std | Standard deviation of AUC over CV folds | Stability of the estimate | Credit LR 0.0756, LightGBM 0.0885 |
| Average precision (AP) | Area under precision-recall curve (sklearn). Chance level = share of positives | Quality when positives are rare | Churn 0.21 (chance 0.134) |
| Baseline AUC | AUC of a simple method on the same test rows | Is the model worth it | Churn: recency-only 0.652, logistic regression 0.683 |
| Threshold | Probability cut-off for a "positive" label (0.5) | Turns probabilities into yes/no | Used for precision/recall/F1 only |

### 7.2 Forecast metrics (sales trend, cash flow, restock)
| Metric | Formula | Meaning | In ours |
|---|---|---|---|
| MAE | mean(abs(prediction - actual)) | Average miss in the same unit (Rs) | Sales trend holdout 30,722.85; cash flow 30,628.84; naive 30,680.91 |
| RMSE | sqrt(mean((prediction - actual)^2)) | Like MAE but big misses count more | Sales trend 39,509.06; cash flow 39,253.55 |
| MAE as % of average week | MAE / average weekly revenue | Error size relative to business size | 30,412 / 398,909 = 7.6% |
| Improvement over naive | (naive MAE - model MAE) / naive MAE | Does the model beat "repeat the recent average" | ~1.2% (code verdict: within noise) |
| Naive baseline | Mean of the last 8 training weeks, repeated | Reference for forecasts | MAE 30,794 (6-fold CV) |
| Holdout MAE | Train on all but last 8 weeks, test on those 8 (`holdout_eval`) | Quick single test | 30,722.85 |
| Rolling-origin CV MAE | Average MAE over folds; each fold trains on the past and predicts the next 4 weeks (`rolling_mae`, 6 folds) | More robust than one split | Prophet tuned 30,412; blend 30,454; Holt 30,785; naive 30,794; Prophet default 31,172 |
| Fold std | Std of MAE across folds | Stability | Prophet tuned 10,250; naive 7,395 |
| 80% interval coverage | Share of actual weeks that fall inside the 80% prediction band | Calibration: should be ~80% | 79% |
| Optuna objective | `rolling_mae` (lower is better) with 30 trials on `changepoint_prior_scale` | How tuning chooses a value | Best 0.00136 |
| Trend % | (mean of last 8 weeks - mean of previous 8) / mean of previous 8 x 100 | Business direction. Growing > +3%, declining < -3% | -1.8% = "stable" |
| wMAPE (restock) | sum(abs(prediction - actual)) / sum(actual units) | Error relative to units sold, safe with many zeros. 1.0 = same as predicting zero | LightGBM 1.823; baselines 1.850, 1.865 |
| MAE / RMSE (restock) | as above, units per product over 4 weeks | Per-product miss | LightGBM 1.2357 / 2.8856; baseline 13-week avg x4 1.2544 / 3.3003; repeat-last-4 1.2644 / 4.0761 |

### 7.3 Anomaly metrics
| Metric | Formula | Meaning | In ours |
|---|---|---|---|
| Contamination | Fixed 3%; threshold = 3% quantile of scores | Review budget, always flags exactly 3% (33 of 1100) | 0.03 |
| Injected-error recall | Share of 33 injected fake errors scoring at or below the threshold; per type and overall | Can it find known mistakes | Spike 0.27, heavy discount 0.64, wrong price 0.36, overall 0.42 |
| Random-guess recall | = contamination | Chance level | 0.03 |
| Precision (injected) | Equals recall here, because flagged count = injected count | | 0.42 |
| Severity | clip(round(100 x (threshold - score) / (threshold - min score)), 1, 100) | How unusual a flagged invoice is | Zero-total invoices = 100 |

### 7.4 Scores and thresholds the app shows (outputs, not evaluation)
| Quantity | How calculated | Values |
|---|---|---|
| Churn probability | LightGBM probability of no purchase in next 60 days | Risk: high >= 0.5, medium >= 0.25, else low |
| `recency_vs_gap` | recency_days / avg_gap_days | 1.0 = on schedule |
| Credit score | round(100 x (1 - probability of bad credit)) | A >= 80, B >= 65, C >= 50, D >= 35, F < 35 |
| Max recommended credit | (credit limit, or 10,000 if zero) x grade multiplier, rounded to 1000 | A 2.0x, B 1.0x, C 0.5x, D 0.25x, F 0 |
| Bad-credit label | At least 30% of value bought after 2026-04-06 still unpaid | 8 of 45 customers |
| Weeks of stock | stock / (predicted 4-week demand / 4) | API rule: restock when under 2 weeks, order up to 4 |
| Restock confidence | Based on selling weeks in last 13 | Low 1179, medium 121, high 0 |
| Product class | 90-day selling days and units | Fast 8+ days or 30+ units |
| RFM tier | Average of percentile ranks of recency, frequency, monetary | Top 35% = regular |

### 7.5 Evaluation design per model
| Model | Split / validation | Sizes | Baselines compared |
|---|---|---|---|
| Sales trend | Holdout last 8 weeks + 6-fold rolling CV (4-week horizon) | 52 weeks | Naive 8-week mean, Holt, blend, default Prophet |
| Cash flow | Holdout last 8 weeks | 52 weeks | Naive |
| Churn | Temporal split with purge gap (train label windows end before test) | Train 244 / test 350 rows, ~47 test positives | Recency-only, logistic regression |
| Credit | Repeated stratified k-fold (10 repeats), not temporal | 45 customers, 8 bad | Logistic regression vs LightGBM (LightGBM must win by > 0.01 AUC) |
| Restock | Last 12 weekly cut-offs as test, 4-week purge gap | ~27k train / ~15.6k test rows | 13-week average x4, repeat last 4 weeks |
| Anomaly | Injection of 33 fake errors | 1100 invoices | Random guess (3%) |

### 7.6 OCR metrics (`ocr_eval/score.py`)
| Metric | How calculated | Our result |
|---|---|---|
| Field accuracy | right / (fields on bills + junk rows). One point per printed field: header (supplier name, PAN, bill no, date) and per row (name, part no, unit, qty, rate, discount %). A missed row loses all its points; a junk row costs one | 74.3% (550/740) |
| Rows with every field right | Rows where all scored fields match / labelled rows | 18.8% (21/112) |
| Bill total right | `sum(qty x rate x (1 - disc)) x 1.13` vs labelled net amount, within 1.0 + 0.03 x rows | 70% (14/20) |
| Junk rows | Predicted rows not matched to a label row | 0 |
| Per-field accuracy | right / scored for each field | Supplier name 90.0%, bill no 90.0%, date 90.0%, PAN 75.0%, qty 88.4%, rate 84.8%, disc 83.0%, name 74.1%, part no 52.3%, unit 52.5% |
| Row matching | Score = 2 if qty x rate matches amount (within 1.0) + 1 if rate matches + name similarity + 1 if part no matches + 0.5 x position closeness; pairs need score >= 1.0 | Used to pair predicted rows with label rows |
| Comparison rules | Text: ignore case/space/punctuation, similarity >= 0.9 (supplier 0.85, ignoring Pvt/Ltd/Co); PAN and bill no: letters and digits equal; date exact; numbers within 0.011; units normalised | |
| OCR confidence | Mean Tesseract word confidence (0-100, ignoring -1) | Shown in the review note; < 35 = unreadable in hybrid mode |

### 7.7 Business and bill-check formulas (not model metrics)
| Quantity | Formula | Where |
|---|---|---|
| Invoice subtotal / total | subtotal = sum(qty x price - item discount); total = subtotal - discount + tax + delivery | `routers/invoices.py:52-53` |
| Purchase net price / total | net = price x (1 - disc%/100); total = sum(qty x net) - discount + tax + charges + round-off | `routers/purchases.py:11, 64-65` |
| Scanned-bill VAT | tax = subtotal(net) x vat%/100 (default 13); total = subtotal + tax | `routers/pending_documents.py:542-543` |
| Payment status | unpaid if paid <= 0; partial if paid < total; else paid | invoices/purchases/approval |
| Customer/supplier balance | += unpaid on sale/purchase; -= payments and return credits | invoices, purchases, payments, returns |
| Sales-return credit | credit = min(unpaid on invoice, refund); cash refund = refund - credit | `routers/sales_returns.py:103-109` |
| Profit and loss | revenue = sum of paid invoices; gross = revenue - purchases; net = gross - expenses; margin = gross / revenue x 100 | `routers/reports.py:12-38` |
| OCR validator tolerances | TOL 0.06, VOTE_TOL 0.1, VAT 13%, rows sum within 0.5, BS-AD year gap 56 or 57 | `services/ocr/validator.py` |
