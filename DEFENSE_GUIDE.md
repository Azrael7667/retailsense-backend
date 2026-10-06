# RetailSense: Defense Study Guide

Everything here was read from your actual code and result files (not from the README, which is partly out of date).
File references look like `ml/train_cells.py:456`.

---

## PART 0. The 10 things you MUST know before walking in

1. **The API does not run any ML model.** Models are trained offline by `ml/retrain.sh`. They write JSON files into `ml/results/<store_id>/`. The API only reads those files (`services/ai_results.py`).
2. **The live ML code is `ml/train_cells.py`** (auto-generated from a notebook by `ml/build_train_cells.py`). The files in `ml/training/*.py`, `scripts/train_all.py` and `ml/models_saved/*` are the OLD version. Do not quote numbers from them (e.g. credit AUC 0.984 is the old number).
3. **Credit scoring serves Logistic Regression, not LightGBM** (`ml/train_cells.py:456-457`). LightGBM must beat it by more than 0.01 AUC to be chosen. It did not (0.9466 vs 0.965).
4. **Honest performance:** forecasts are only ~1% better than a naive baseline; churn LightGBM (AUC 0.642) is worse than logistic regression (0.683); restock barely beats baselines. Say this yourself. It is much safer than being caught.
5. **Data is tiny and from ONE shop:** 1100 invoices, 52 weeks, 51 buying customers, 62 customers total (Bijeta Auto Parts). README says some values were adjusted for the evaluation data.
6. **OCR accuracy was never measured.** There is no ground-truth set, no CER/WER, no tests. It was tuned by fixing failures on sample bills.
7. **OCR is rule-based post-processing on top of Tesseract**, and its core idea is: *bills contain redundant arithmetic* (qty x rate = amount, rows sum to total, taxable + 13% VAT = net, amount in words), so the system uses the bill's own numbers to detect and repair misreads.
8. **Human-in-the-loop:** nothing is saved to purchases or stock until a person approves the scanned bill.
9. **There is no ORM and no custom JWT code.** Database = Supabase (Postgres) through its Python client. Login/token checking = Supabase Auth.
10. **Known weak points** (Part 5): permissions not enforced server-side, no DB transactions, a few unauthenticated admin endpoints, minimal tests.

---

## PART 1. The system

**RetailSense Nepal**: a back-office + business-intelligence backend for small Nepali retail shops.

**Features:** products/stock, customers and suppliers with running balances (udharo = credit sales), sales invoices, quotations, sales/purchase returns, purchases, payments in/out, expenses, khata (credit ledger), reminders, reports (P&L, daybook, party statement), dashboard, activity feed, **Scan Bill (OCR)**, and **6 AI helpers**.

**Roles:** owner (everything), accountant (everything by default), auditor (read-only), staff (nothing until granted). Plus a separate platform admin (SaaS operator).

**Tech stack and why**
| Tech | Why |
|---|---|
| FastAPI + uvicorn | Fast async Python framework, automatic validation, free Swagger docs at `/docs` |
| Pydantic (`schemas/`) | Checks request/response data types; bad input gets a 422 automatically |
| Supabase (Postgres + Auth + Storage) | Managed DB, ready-made login, file storage (bills), row-level security. Saves building auth |
| Tesseract + OpenCV | Free self-hosted OCR (no per-scan fee, bills stay private) |
| Gemini (optional) | Cloud OCR fallback in `hybrid`/`gemini` mode |
| pandas, scikit-learn, LightGBM, Prophet, SHAP, Optuna | Offline ML training |
| nepali-datetime | BS to AD date conversion |
| Docker + Render + GitHub Actions | Deploy (Docker is needed because Tesseract is a system program) |

### Definitions
- **API**: URLs the frontend calls to read/change data.
- **REST**: each thing has a URL; HTTP verbs mean actions (GET read, POST create, PATCH/PUT update, DELETE remove); data is JSON.
- **FastAPI**: Python web framework. **uvicorn**: the server that runs it. **ASGI**: async interface between server and app.
- **Pydantic**: type-checking classes (e.g. `InvoiceCreate`).
- **ORM** (SQLAlchemy is one): maps Python classes to tables. **You do not use one.**
- **Supabase**: hosted Postgres + Auth + Storage. **PostgREST**: turns tables into a REST API; the `supabase` Python client talks to it.
- **JWT**: signed token carrying the user's identity, sent as `Authorization: Bearer <token>`. Issued by Supabase, not by your code.
- **Authentication** = who you are. **Authorization** = what you may do.
- **RBAC**: access by role.
- **RLS (Row-Level Security)**: Postgres rules limiting which rows a user can see. Active when the user's token is used (`database.py` `get_supabase(access_token)`).
- **Service-role key**: master key that bypasses RLS; server-side only (`get_supabase_admin()`).
- **Multi-tenancy**: many shops in one database; every row has `store_id`.
- **Dependency injection (`Depends`)**: FastAPI runs helper functions (like `get_current_user`) before a route.
- **Middleware**: code wrapping every request (CORS, activity logging).
- **CORS**: browser rule that blocks calls from other websites unless the API allows that origin (`ALLOWED_ORIGINS`).
- **Soft delete**: mark inactive instead of deleting (`products.is_active=False`).
- **Transaction / atomic**: all steps succeed or none do.
- **RPC**: calling a Postgres function by name (`next_doc_number`).
- **Signed URL**: temporary link to a private file (10 min for bill images).
- **Udharo / Khata**: credit sales / credit ledger book.

### Request flow (example: create an invoice, `routers/invoices.py`)
1. CORS + ActivityLog middleware wrap the request.
2. Pydantic validates the body.
3. `get_current_user` (`middleware/auth_middleware.py:49`) verifies the token by asking Supabase (`auth.get_user`); sessions older than 24h are rejected.
4. `get_active_store_id` reads the `X-Store-Id` header and checks the user is a member of that store (403 otherwise).
5. Router: get number via RPC `next_doc_number`, compute totals, insert invoice, add unpaid amount to `customers.balance`, insert items (with `cost_price_at_sale`), subtract stock.
6. Middleware logs a sentence into `activity_feed`.

**Why `cost_price_at_sale`?** Freezes cost at sale time so profit history stays right when costs change later.

### Database (inferred from code; no SQL files in the repo)
`stores`, `users`, `store_members` (many-to-many user/store, so one person can work in several shops), `platform_admins`, `categories`, `products`, `customers` (has `balance`, `credit_limit`, `pricing_tier`), `suppliers` (`balance`), `invoices` + `invoice_items`, `purchases` + `purchase_items`, `quotations` + items, `payments`, `payments_out`, `payment_allocations`, `sales_returns`, `purchase_returns`, `expenses`, `khata_entries`, `reminders`, `pending_documents` (the OCR review queue), `activity_feed`.

**How credit works:** `customers.balance` += unpaid part of an invoice; -= payments and return credits. Edits/deletes reverse the old effect first.

### Other facts
- Product classification (`routers/admin.py`): fast = 8+ selling days or 30+ units in 90 days; moderate = 3+ days or 10+ units; slow = otherwise; dead stock = no sales but stock > 0.
- Customer tiers (`scripts/classify_customers.py`): **RFM, not ML**. Percentile rank of Recency, Frequency, Monetary, averaged; top 35% = "regular" with suggested 5-15% discount. Manually set tiers never overwritten.
- `ActivityLogMiddleware` (`services/activity.py`): logs successful POST/PUT/PATCH/DELETE as readable sentences; best-effort, never breaks the request.
- Deployment: `Dockerfile` (python 3.12-slim + tesseract eng + nep), `render.yaml` (free plan, Singapore, `OCR_ENGINE=selfhosted`), GitHub Actions runs tests then calls the Render deploy hook (pytest ends with `|| true`, so failures don't block deploy).

---

## PART 2. Scan Bill (OCR) in full detail

### 2.1 Big picture
A shop owner photographs a supplier's purchase bill. The system reads it, builds a draft (supplier, bill number, date, items with qty/rate/discount), the owner reviews/edits, then approves, which creates the purchase, any new products/supplier, and updates stock and cost prices.

### 2.2 Flow, file by file (`POST /api/pending-documents/purchase-bill`, `routers/pending_documents.py:335-384`)
1. **Check type**: only JPEG/PNG/WebP (`ALLOWED_CONTENT_TYPES`). No PDF. Roles: owner or accountant only.
2. **Upload original** to Supabase Storage bucket `purchase-bills` at `{store_id}/{uuid}.ext`.
3. **Insert `pending_documents` row**, status `processing`.
4. **Run extraction** in a worker thread (`asyncio.to_thread`) so the web server isn't frozen. Entry: `services/ocr/pipeline.py: extract_with_engine` (mode from env `OCR_ENGINE`).
5. **Self-hosted path** (`pipeline.py: extract_selfhosted`):
   1. `engine.load_image`: open, fix phone rotation (EXIF), convert RGB.
   2. `preprocess.preprocess`: clean the image (2.3).
   3. `engine.run_ocr`: Tesseract (2.4).
   4. `parser.parse_bill`: text to fields + item rows (2.5).
   5. `validator.validate_bill`: cross-check and repair numbers (2.6).
   6. `adapter.to_gemini_shape`: convert to the same dict shape Gemini returns, so the router doesn't care which engine ran.
6. **Build review draft** (`build_review_draft`, router `:202-290`): fuzzy-match items to existing products, match supplier, convert BS date, set VAT default 13%.
7. **Duplicate check** (`check_duplicate_bill_number`): normalized bill number (lowercase letters/digits only, so SI0265 = SI/0265), within the same supplier if known.
8. Save draft, status `ready_for_review` (or `failed` + error message).
9. **Review**: `GET /{id}` (with 10-min signed image URL), `PATCH /{id}` to edit.
10. **Approve** (`POST /{id}/approve`, `:460-614`): re-check duplicate; create new products (cost = net price after discount, list price = gross, selling price 0, stock 0, reorder level 5); create supplier if new; compute `subtotal = sum(qty * net)`, `tax = subtotal * vat%`, `total`; insert purchase + items; add unpaid amount to `suppliers.balance`; `stock += qty`; set `cost_price`, keep `previous_cost_price`; mark approved. **Reject** and **delete** endpoints also exist.

### 2.3 Image preprocessing (`services/ocr/preprocess.py`)
In order:
1. **Grayscale**: colour adds noise.
2. **Upscale** if width < 1800 px (cubic): Tesseract likes characters ~30 px tall.
3. **Deskew**: Canny edges, then Hough line transform finds long lines (table borders), median angle within +/-15 degrees, rotate if >= 0.3 degrees.
4. **Denoise**: Non-Local Means (h=10).
5. **Adaptive threshold** (Gaussian, block 35, C=15): converts to black/white using a local cut-off, so shadows/uneven light on phone photos are handled.
6. **Remove table lines**: morphological opening with long horizontal and vertical kernels finds the ruled lines; they are erased so borders aren't read as `|`, `l`, `1`.
7. **20 px white border**: Tesseract is bad when text touches the edge.

### 2.4 Tesseract (`services/ocr/engine.py`)
- Config: `--oem 1 --psm 4`, languages `eng+nep` in a single pass.
- **OEM 1** = LSTM neural-network engine. **PSM 4** = "single column of text of variable sizes" (suits a bill read row by row).
- Nepali and English are handled together; there is no separate Nepali pass and no language detection. Nepali matters mainly for the label "Miti" and Devanagari digits. The parser mostly works with ASCII digits and ignores Nepali text.
- If a language pack is missing it falls back to English instead of crashing.
- Output: per-word data grouped into lines; **confidence = mean word confidence** (0-100).

### 2.5 Parser (`services/ocr/parser.py`): text to structured data
**Honest note:** this file is built from layered "patches": functions like `parse_bill` are defined several times; each new definition wraps the previous one; the last one is live. It evolved by fixing real bills (`.bak_v8` to `.bak_v12` files). It is a known code smell; say so before they do.

Header fields (all regex-based):
- **Bill number**: patterns like `Bill No: 123456/2081`, `\d{6}/\d{4}`, generic `Bill/Invoice No` + code (4-25 chars with a digit), plus supplier-specific forms (`SB-`, `SI12345-12/34`, tolerant of OCR confusions like `$`/`5` for `S`).
- **Date (AD)**: `20xx-MM-DD`, `DD/MM/20xx`, or `a/b/yy` (if first number <= 12 read as month/day, since one supplier prints MM/DD/YY). Years 2000-2039 only.
- **Miti (BS date)**: captured only for a sanity check.
- **Supplier name**: first of the first 15 lines containing PVT/LTD/TRADERS/ENTERPRISES/TRADING; trailing OCR junk stripped.
- **Supplier PAN**: 9 digits near "VAT No"/"PAN", excluding the customer's.
- **Totals**: Total Amount, Taxable, VAT (13%), Net Amount.

Item rows:
- Table starts at a header line matching `escription|Part[il1]culars` (tolerates a missing first letter) and ends at a stop line (`in words`, `total amount`, `gross amount`, ...).
- **Amount = biggest money value in the row.**
- **Qty and rate are solved arithmetically:** for each number before the amount, try `q = round(amount / v)` with q in 1..1000 within tolerance; this is "arithmetic recovery".
- **Discount** detection: Sipradi layout has a code like `45 N2` meaning 45%; otherwise a trailing percent <= 100; net compared to amount (70-100% = net; <30% = discount). Rounding uses ROUND_HALF_UP like printed bills.
- **Sipradi-layout parser**: columns `part | HS | description | qty | Unit | rate | disc% | code | amount`.
  - `_repair`: enforces `qty x rate = amount`; if not, tries digit-swap fixes using a confusion map (`4/3` read for `1`, `8` for `6/5`, ...). **Accepts only if exactly one fix explains the row; refuses if ambiguous** ("don't guess").
  - v10 patch re-solves garbled rows (tries lost leading digits, uses Unit-column quantity as tie-breaker, accepts only if the best fit is 4x closer than the runner-up).
- **HSN vs part number:** HSN is a 4-8 digit product-classification code, not the supplier's part number; the parser keeps them separate.
- Junk-row filters drop rows that mention PAN/VAT/Phone/Invoice/Date/Total/Signature etc.
- Units (Pcs, Box, ...) are only used as markers to locate qty; the unit string is NOT stored (adapter sets `unit: None`, default `pcs`).

### 2.6 Validator (`services/ocr/validator.py`): the heart of the accuracy story
Constants: `TOL=0.06`, `VOTE_TOL=0.1`, `VAT=13%`.
Checks:
1. **amount_in_words_matches_net**: reads "Rupees Ten Thousand ... Only", converts words to a number (understands lakh/crore; fuzzy-fixes OCR typos using `difflib` cutoff 0.67) and compares with net amount.
2. **Taxable amount voting**: up to 4 independent sources (`net - vat`, `words - vat`, printed taxable, `vat / 0.13`); the value agreed by the most *independent* groups (>= 2) wins and corrects the printed one.
3. `vat_is_13_percent`, `taxable_plus_vat_equals_net`.
4. Dates/IDs: `date_present`, `date_not_in_future`, **AD/BS year gap must be 56 or 57** (catches a misread year), `bill_no_present`, `supplier_pan_9_digits`.
5. Items: `items_found`; if exactly one row has no net, recover it as `taxable - sum(others)` (flagged unverified); `items_sum_matches_taxable` (within 0.5); `items_sum_matches_total`.
6. **Discount repair (v10):** if rows sum to the printed total but implied discount differs, find the single row whose discount % fixes the gap; apply only if unique.
7. Output: `status` = `ok` or `review`, list of `checks`, `corrections`, per-row `needs_review` + `review_reason`.

**Central idea to defend:** *error detection through the bill's own accounting redundancy.* Several independent numbers must agree; when they disagree the system repairs only if one explanation fits, otherwise flags for human review.

### 2.7 Modes (`pipeline.py`, env `OCR_ENGINE`)
| Mode | Behaviour |
|---|---|
| `selfhosted` (default, deployed) | Tesseract only, no API key. If unreadable (no items / no net / confidence < 35) adds a "enter manually" note |
| `hybrid` | Tesseract first; Gemini only if needed: no items, no net, confidence < 35, an item lacks qty/rate, or `sum(qty*rate)` differs from printed total by more than `2.0 + 0.006*sum(qty)` |
| `gemini` | Original paid cloud flow |

Hybrid extras: Gemini result is cached by SHA-256 of the image (`.ocr_cache/`) so re-scans are free and stable; cloud rows are verified against the self-hosted printed total; if Gemini is down the self-hosted result is returned with a warning. Gemini call: model `gemini-3.6-flash`, strict JSON output, 4 tries with waits 2/5/15 s, 60 s timeout.
**Note:** `render.yaml` sets `selfhosted` and has no Gemini key, so the deployed system is pure self-hosted.

### 2.8 Other pieces
- **Adapter** (`adapter.py`): same output shape as Gemini; always reports date as AD; unit price is the gross rate with discount reported separately; always adds notes: OCR confidence, printed total, "bill number cannot be verified automatically".
- **BS to AD** (`utils/nepali_date.py`): wraps `nepali-datetime`. The file explains that a hand-made table drifted and `samaya` was a day off for BS 2083; `nepali-datetime` matched 19 real bills showing both dates. **Caveat:** in the self-hosted path the adapter reports the printed AD date, so BS conversion is mainly used for Gemini output; if a bill shows only BS, the date is empty and falls back to today at approval.
- **Fuzzy product matching** (`fuzzy_match`): `difflib.SequenceMatcher` ratio, threshold **0.90** (raised from 0.72 because "Oil Filter" matched "Air Filter"). Trade-off: more near-duplicates created as new products.
- **Supplier matching**: exact 9-digit PAN, or PAN differing by exactly one digit (asks to confirm), else fuzzy name.
- **`utils/product_names.py`**: `compose_name("Spring Leaves","patta")` gives `Spring Leaves / Patta`; keeps Nepali local names as part of the name and used for matching.
- **`multipage.py`** exists but is never imported (dead code); only single images work.

### 2.9 OCR limitations (say these first)
- Never measured: no labelled set, no accuracy number. (Honest line: "tuned on real sample bills until arithmetic reconciled; proper field-level evaluation is future work.")
- Parser is tuned to specific bill layouts (Sipradi, BNH); new suppliers likely need a new patch.
- Weak on blur, glare, folds, thermal-print fading, handwriting.
- Nepali text/items largely ignored; descriptions expect uppercase Latin tokens.
- Units not read; no PDF; no multi-page; VAT fixed at 13%.
- Approve is not one database transaction; reject doesn't check status.
- Many `try/except: pass` in patches: failures can be silent.
- Free Render tier is slow for OCR; the cache is on ephemeral disk.
- Only `tests/test_health.py` exists; no OCR tests.

### OCR definitions
- **OCR**: image of text to text. **Tesseract**: open-source OCR (v4+ uses LSTM neural net); **pytesseract** is the Python wrapper. **Language pack** (`eng`, `nep`): trained data per language.
- **Preprocessing / grayscale / upscaling / deskew / denoise**: cleaning steps (see 2.3).
- **Canny / Hough**: edge detector / line-finding by voting. **Adaptive threshold (binarization)**: black/white with a local cut-off. **Morphological opening/dilation**: shape operations; here used to isolate and erase table lines.
- **Confidence**: Tesseract's own 0-100 certainty per word.
- **Regex**: pattern language for text (e.g. `[0-9]{9}` = nine digits).
- **Heuristic**: rule of thumb that usually works.
- **PAN**: 9-digit Nepali tax ID. **VAT**: 13% in Nepal. **Taxable amount**: before VAT, after discount. **Net amount** = taxable + VAT.
- **HSN code**: 4-8 digit goods-classification code. **Part number/SKU**: supplier's item code.
- **Lakh/crore**: 100,000 / 10,000,000.
- **BS/AD, Miti**: Bikram Sambat (Nepali calendar, ~56-57 years ahead) / Gregorian; "Miti" = the date label.
- **Fuzzy matching**: approximate string similarity (0-1).
- **Deduplication**: avoid recording the same bill twice.
- **Reconciliation / voting / tolerance**: making independent numbers agree; majority of independent sources wins; allowed small difference for rounding.
- **Round-half-up**: 0.5 rounds up (accounting), unlike Python's banker's rounding.
- **Human-in-the-loop**: a person verifies output before it becomes real data.
- **Cache (SHA-256 key)**: store result keyed by image fingerprint.
- **Monkey-patching**: redefining a function later to change its behaviour.
- **EXIF orientation**: metadata telling how the phone was held.

### Likely OCR questions
- *Why Tesseract, not only Gemini?* Cost (no per-scan fee), privacy (supplier names/PANs/prices stay on your server), reliability (Gemini returned 503/429 errors), cheap hosting. Gemini remains an optional fallback.
- *How do you know a result is right?* We don't claim to; we check internal consistency and flag doubts. A human approves.
- *How accurate is it?* Not measured. Offer the evaluation plan: 30-50 labelled bills, field-level exact match (bill no, date, PAN, total, per-row qty/rate/amount), plus CER for text; held-out set.
- *Why not train your own OCR?* Needs thousands of labelled bills and GPUs; Tesseract + validation gives a practical result.
- *What does `needs_review` mean?* A row/field is uncertain or was repaired; the user must check it against the paper bill.

---

## PART 3. The 6 AI models

### 3.1 How the whole ML pipeline works
1. `ml/retrain.sh` (documented cron: Sunday 02:00; **no scheduler is installed in the repo**) reads shop ids from `ml/shops.txt`.
2. `ml/export_shop.py` exports the shop's tables via the backend (**pseudonymised**: names become "Customer 001", phone/email/address/PAN dropped, store_id becomes "S1"). If exported row counts don't match the DB, it stops and trains nothing.
3. `ml/train_shop.py` runs `ml/train_cells.py` in blocks: "core" blocks must succeed; each "helper" (sales_trend, cash_flow, comparison, churn, credit, restock, anomaly, report) is isolated, so one failing or lacking data doesn't stop the others. Minimum-history guards: 40 weeks (forecast), 36 (restock), 270 days (churn), 330 days (credit), 30 customers, 200 invoices.
4. **Safe publish** (`train_shop.py:88-103`): validate that each `*_results.json` parses as a JSON dict; copy to `<file>.tmp` in the destination folder; `os.replace` (atomic rename). Failed helper leaves old files. `retrain_status.json` records each helper's status/time (last run 2026-10-04: 8 ok, 0 failed). Atomicity is per file, not across files.
5. The temp working folder with exported data is deleted after each run.
6. API serving (`services/ai_results.py`): validates store id with a regex (blocks path traversal), caches by file modification time, returns **404 "No AI results for this shop yet"** if none, and adds real names/phones/balances from Supabase at request time.
7. The `POST .../train` endpoints do **not** train; they return an "trained offline" message.

**Dataset:** invoices 2025-10-04 to 2026-10-03, 1100 invoices, 52 complete weeks (weeks run Sunday-Saturday; partial weeks dropped; empty weeks = 0), 51 of 62 customers have invoices, 1300 active products. `rev_w` = billed invoice totals (including unpaid credit sales).

### 3.2 Summary table (numbers from `ml/results/<store>/*.json`)
| Helper (UI name) | Algorithm | Question | Key numbers |
|---|---|---|---|
| Business Direction (sales_trend) | Prophet + Optuna (30 trials) | Is weekly revenue growing/shrinking? Next 8 weeks? | holdout MAE 30,723 vs naive 30,681; trend -1.8% = "stable" |
| Cash Flow Forecast (cash_flow) | Prophet (default) + weekday split + flat expenses | Daily money in/out next 30-90 days | MAE 30,629 vs naive 30,681 |
| Restock Advisor (restock) | One pooled LightGBM (Poisson) over 1300 products | Units sold in next 4 weeks | MAE 1.236 vs 1.254/1.264 baselines; wMAPE 1.82 |
| Customers Leaving (churn) | LightGBM + SHAP (LR and recency baselines compared) | Who will stop buying within 60 days? | AUC 0.642 vs LR 0.683 vs recency-only 0.652 |
| Udharo Advisor (credit) | **Logistic Regression** served (LightGBM compared) | Is it safe to give more credit? | AUC 0.965, P 0.75, R 0.79, F1 0.73 (45 customers) |
| Unusual Transactions (anomaly) | Isolation Forest (300 trees) | Which invoices look wrong? | recall 42% on injected errors vs 3% chance |

### 3.3 Sales trend (`train_cells.py:118-161`)
- **Prophet**: Facebook's forecasting model = trend + optional seasonality. Here: linear trend, weekly/daily seasonality off, **yearly seasonality OFF** (one year of data can't support it), no holidays (the Nepali holiday helper exists but is not used), 80% interval, forecasts clipped at 0.
- **Optuna** tunes only `changepoint_prior_scale` (how flexible the trend is) in 0.001-0.2 using 6-fold expanding-window CV with 4-week horizon. Best = 0.00136 (almost a straight line).
- **Baseline (naive)**: mean of last 8 weeks.
- **Holdout**: train on all but last 8 weeks, test on those 8: Prophet MAE 30,722.85, RMSE 39,509, naive MAE 30,680.91. Average week = Rs 398,909, so MAE ~7.7%.
- Output insights: direction (growing > +3%, declining < -3%), avg weekly sales, best/worst week, best month Jun / worst Sep, `forecast_8w` with bounds, history.
- Weaknesses: only 52 weeks; Optuna CV folds overlap the holdout (code admits it); not better than naive; best/worst month from 12 points.

### 3.4 Cash flow (`:163-204`)
Forecast 14 weeks of revenue with default Prophet; split each week across weekdays by historical weekday share; expenses = average of last 26 weeks of the **expenses table** (Rs 2,557/day, flat); net = revenue - expenses. `INCLUDE_PURCHASES = False`, so **stock purchases are not cash-out**, which makes net cash flow look ~95% of revenue (unrealistic). Revenue is billed, not collected (credit sales not discounted). Quality = same as sales trend.

### 3.5 Model comparison (`model_comparison_weekly_revenue.json`)
6-fold CV, 4-week horizon: Prophet-tuned 30,412; blend 30,454; Holt damped 30,785; naive 30,794; Prophet-default 31,172. Improvement over naive ~1.2%. Code prints: "no model clearly beats the naive baseline (differences are within noise)". 80% interval coverage 79% (well calibrated).
**Honest answer to "does the forecast add value?"**: accuracy is within noise of naive because the weekly series is stable; it still provides calibrated intervals and a trend indicator.

### 3.5b Churn (`:332-408`)
- **Label:** at cut-off date T, `y=1` if the customer makes **no purchase in the next 60 days**. Snapshots every 14 days (16 cut-offs).
- **Features (8)**, all computed only from invoices before T: `recency_days` (days since last buy, cap 365), `frequency_30d`, `frequency_90d`, `monetary_90d`, `avg_gap_days`, `recency_vs_gap` (how overdue vs their normal rhythm; 2.2 = twice as long as usual), `avg_invoice`, `unpaid_ratio` (share of recent purchases unpaid).
- **Split:** temporal with a purge gap (train label windows end before test starts). Train 244 rows, test 350 rows (churn rate 0.16 vs 0.134, ~47 positives). Rows are customer x date so the same customers repeat (correlated; effective sample is smaller).
- **LightGBM params (untuned):** 150 trees, lr 0.05, 6 leaves, min_child_samples 15, subsample 0.8, colsample 0.8, `reg_lambda` 5. Deliberately small and regularised for tiny data.
- **Metrics:** AUC 0.642; average precision 0.21 (chance = 0.134); precision@0.5 0.278; recall@0.5 0.106; baselines: recency-only 0.652, logistic regression 0.683. **The served model is below both.**
- No class-imbalance handling in live code. Final model refit on all 594 rows. Risk thresholds HIGH >= 0.5, MED >= 0.25: 3 high, 7 medium, 41 low, 6 already lapsed.
- `is_churned` in the output = `recency_days > 60` (a fact, not a prediction). The router relabels the page: lapsed -> `stopped_coming`; active with model risk high/medium -> `slowing_down`; others -> `buying_regularly`. So the page's "high risk" number is not the raw model count (raw kept in `model_risk`).
- **SHAP** (TreeExplainer): top-3 features per customer, positive = raises risk, turned into sentences ("Share of recent purchases left unpaid: 70% (raises the risk)"). Poster figure 4 = Customer 024, risk 63%, top driver unpaid 70%.
- Weaknesses: 51 customers; AUC 0.64 is weak; "unpaid ratio" is the top driver so partly "customer owes money"; 11 customers with no invoices excluded; probabilities likely not calibrated.
- Value to claim: explainable ranking for the owner to phone customers, not high accuracy.

### 3.6 Credit scoring / Udharo Advisor (`:410-496`)
- **Label:** bad (1) if >= 30% (`BAD_UNPAID=0.30`) of the value bought after 2026-04-06 is still unpaid.
- **Features (9)** from the 180 days before: `n_purchases`, `total_value`, `avg_invoice`, `max_invoice`, `unpaid_ratio`, `share_unpaid_invoices`, `purchases_90d`, `avg_gap_days`, `recency_days`.
- **Training set:** customers with >= 3 earlier purchases: **45 customers, 8 bad (17.8%)**.
- **Evaluation:** repeated stratified k-fold (10 repeats), **not temporal**.
- **LR vs LightGBM:** LightGBM AUC 0.9466 (std 0.0885), accuracy 0.822, precision/recall/F1 = 0 (its probabilities stay below 0.5 so it flags no one; 82% accuracy = predict-everyone-fine level). Logistic Regression AUC **0.965** (std 0.0756), accuracy 0.918, precision 0.75, recall 0.79, F1 0.73. Rule: LightGBM served only if AUC > LR + 0.01 ("simpler model wins ties"), so **LR is served**.
- **LR setup:** median imputation, StandardScaler, L2, C=0.5. Weights saved to `credit_logistic_regression.json`. Explanation = standardised value x coefficient; top 3 reasons.
- **Scoring:** `credit_score = round(100 * (1 - p_bad))`. Grades A >= 80 (approve, 2.0x limit), B >= 65 (1.0x), C >= 50 (review, 0.5x), D >= 35 (0.25x), F < 35 (don't give credit, 0). Max credit = (limit or 10,000) x multiplier rounded to 1000. Customers with no purchases in 180 days (11) get neutral p=0.5 (score 50, grade C): a default, not a prediction. Result: A 39, B 1, C 13, D 0, F 9.
- **Imbalance:** here 17.8% bad (the old 35/62 figure is from the superseded script). Live code uses no class weights.
- **Why LR is a good answer:** interpretable and defensible for credit decisions; tiny data favours simple models; coefficients readable.
- **Caveats:** n=45, 8 positives, AUC std 0.076; random CV; features and label come from similar behaviour (people who left bills unpaid keep doing it) so high AUC = persistence of habit; scoring window overlaps label window; 30% cut-off and credit multipliers are business choices.

### 3.7 Restock Advisor (`:498-571`)
- One pooled **LightGBM regressor with Poisson objective** (suited to count data with many zeros) across 1300 products; 250 trees, lr 0.05, 15 leaves, min_child_samples 40.
- **Features (9):** `sold_last_week`, `sold_4w`, `sold_8w`, `sold_13w`, `weeks_with_sales_13w`, `weeks_since_last_sale`, `category_sold_4w`, `price` (lifetime median, mild look-ahead), `cost` (current).
- **Target:** units sold in next 4 weeks. 36 weekly cut-offs; test = last 12; 4-week purge gap.
- **Metrics:** LightGBM MAE 1.2357, RMSE 2.886; baseline 13-week avg x4 MAE 1.2544 / RMSE 3.30; baseline repeat-last-4-weeks MAE 1.2644 / RMSE 4.08. **wMAPE 1.82** (> 1 = error larger than units sold; worse than predicting zero; gain is mainly fewer big misses). Auto-parts demand is intermittent.
- Output: weekly = demand/4 (flat), `weeks_of_stock`, confidence (low 1179, medium 121, high 0). 364 products "no_sales_history".
- **The API overrides the file's rule:** file says restock if cover < 8 weeks (order to 12); API uses 2 and 4 weeks (shop policy), overridable by `?cover_weeks=&target_weeks=`.
- No SHAP for this model.

### 3.8 Anomaly detection (`:573-650`)
- **Isolation Forest**: unsupervised; random splits isolate unusual rows quickly; easier to isolate = more anomalous.
- **Features (10):** `log_total`, `n_items`, `max_line_share`, `discount_ratio`, `unpaid_ratio`, `tax_ratio`, `price_dev` (max deviation of unit price from that product's median), `max_qty`, `cust_dev` (total vs that customer's normal), `weekday`.
- **3% budget:** threshold = 3% quantile of scores, so exactly 33 of 1100 are flagged by construction. **The count is not a finding.**
- **Validation by injection:** 33 fake errors (11 spikes x8 total, 11 discounts of 50%, 11 wrong unit prices) added to features; recall: spike 0.27, heavy discount 0.64, wrong price 0.36, **overall 0.42 vs 0.03 chance**. Caveat: errors are the author's assumption; no real fraud labels.
- Explanations are rule-based reasons ("Bill total is zero", "Discount is X% of the bill", "VAT is X% ... normally 13%"), severity 1-100. Many top flags are zero-total invoices (possibly data-entry artefacts).

### 3.9 Legacy stuff (don't quote)
`ml/training/*.py`, `scripts/train_all.py`, `ml/models_saved/*`: older version with random train/test splits, class weights, hard-coded Bijeta store id, noisy numbers (e.g. churn AUC 0.75 on 50 customers with 2 positives in test; credit AUC 0.984 on ~16 test customers). If asked, "those were my first iteration; I replaced them with temporal snapshots and baselines because the original numbers were noise".

### 3.10 ML definitions
- **Model**: formula learned from past data that outputs a prediction.
- **Training/test data**: learn on one part, score on unseen part.
- **Temporal split**: train on earlier dates, test on later (like real life). **Random split**: shuffles rows, can leak the future into training for time data. **Purge gap**: a gap so training labels don't overlap the test period.
- **Cross-validation (CV)**: repeat train/test on several splits and average. **Stratified k-fold**: keeps class share equal in each fold. **Expanding-window CV**: always train on the past, test on the next block.
- **Leakage**: information from the answer/future slipping into features, giving falsely high scores.
- **Overfitting**: memorising quirks of small data. **Regularisation** (`reg_lambda`, few leaves) fights it.
- **Baseline**: deliberately simple method a model must beat.
- **Logistic regression**: weighted sum of features turned into a 0-100% probability; readable weights.
- **Gradient boosting / LightGBM**: many small trees built one after another, each fixing the previous errors. `n_estimators` trees; `learning_rate` size of each step; `num_leaves` tree size; `min_child_samples` min rows per leaf; `subsample`/`colsample_bytree` share of rows/columns per tree.
- **Poisson objective**: loss for non-negative counts.
- **Prophet**: forecasting with trend (+ seasonality). `changepoint_prior_scale` = trend flexibility. **Holt damped trend**: exponential smoothing whose trend flattens.
- **Optuna / TPE**: automatic hyperparameter search; "30 trials" = 30 values tried.
- **Hyperparameter**: setting chosen before training.
- **Isolation Forest, contamination**: see 3.8.
- **SHAP**: per-prediction feature contributions (positive raises risk).
- **AUC (ROC-AUC)**: chance a random positive is ranked above a random negative; 0.5 coin flip, 1 perfect; threshold-free.
- **Average precision**: area under precision-recall curve; chance level = positive rate.
- **Precision** = of flagged, how many truly positive. **Recall** = of true positives, how many found. **F1** = harmonic mean. **Accuracy** = share correct (misleading with imbalance). **Threshold** = probability cutoff (0.5).
- **Class imbalance, `class_weight`, `scale_pos_weight`**: make the rare class count more.
- **MAE** average absolute miss (Rs). **RMSE** like MAE but squares errors. **wMAPE** total abs error / total actual (works with zeros). **MAPE** not used.
- **RFM**: Recency, Frequency, Monetary; **percentile rank**.
- **Pseudonymisation**: replace names with labels.
- **Atomic replace (`os.replace`)**: swap a file in one step; reader sees old or new, never half.
- **Calibration**: do predicted probabilities match real frequencies (80% interval covering 79% of actuals = good).

### 3.11 Likely ML questions (with honest answers)
- *Why is churn LightGBM worse than logistic regression?* Only 51 customers and ~600 correlated snapshot rows; simple models do as well. I report it. Value = SHAP reasons and ranking.
- *Why serve LR for credit?* Interpretable, wins on AUC (0.965 vs 0.947), LightGBM flagged nobody at 0.5. The 0.01 rule makes the simpler model win ties.
- *Is AUC 0.965 trustworthy?* Only 45 customers, 8 bad, std 0.076, random CV. Indicative, not conclusive.
- *Why use precision/recall/AUC, not accuracy?* With 17.8% bad customers, "everyone is fine" scores ~82% accuracy while catching nobody (that is exactly what LightGBM did).
- *Why no seasonality/holidays?* One year of data; Prophet needs ~2 years for yearly cycles.
- *Why does net cash flow look so high?* Purchases excluded (`INCLUDE_PURCHASES=False`), billed not collected.
- *Why 3% anomalies?* Fixed review budget; validated with injected errors (42% vs 3% chance).
- *Why not live training in the API?* Free server is small; models are weekly; predictions precomputed = fast. Trade-off: staleness (up to a week).
- *Why JSON files?* See the earlier answer: batch-written, read-only, isolated from live DB, atomic publish. Scale limit: move to a table/object storage with versioning.
- *Does one shop affect another?* No: per-shop export, per-shop results folder, UUID-checked path, store id from the authenticated session.
- *What if training breaks?* Isolated helpers, atomic per-file publish, old results kept, status file records failures, API serves last good files; a shop with no results gets 404.
- *Where did the data come from?* One real shop (Bijeta Auto Parts), pseudonymised; README says some quantities/stock/dates were adjusted for evaluation. Generalisation to other shops is unproven.

---

## PART 4. Poster figures
- `poster_figures/figure3_forecast_vs_actual.png`: actual weekly revenue vs 8-week holdout forecast with 80% band, "MAE Rs 30.7k". The forecast is nearly flat around ~400k: say "the model predicts the average because the series is stable".
- `poster_figures/figure4_shap_explanation.png`: SHAP bars for Customer 024, risk 63%; red bars raise risk; top = unpaid share 70%, then average bill ~Rs 59,670, usual gap 22 days, days since last buy 49.

---

## PART 5. Weak points of the whole project (own them)
**Security / access**
- Permission system (`require_permission`, ~40 keys) is stored and editable but **not used by any router**; only `require_role` guards some endpoints. UI may hide features while API is more open.
- Unauthenticated: `/api/admin/model-status`, `/training-progress`, `/classify-products/preview`; hard-coded store id `58998cb1-...` in admin/classification/train_all.
- `/platform-admin/bootstrap` is open until the first admin exists.
- Several routers use the service-role client (bypasses RLS); safety depends on manual `.eq("store_id")` filters.
- Login returns `str(e)` as the 401 detail; no rate limiting; `secret_key` default "changeme" (unused).
- Upload check is client-declared content type + (OCR route) 10 MB limit.

**Data integrity**
- No DB transactions: multi-step writes (invoice, balance, items, stock; bill approval) can leave partial data on failure. Fix: Postgres RPC functions.
- Read-modify-write on balances/stock can lose updates under concurrency; stock can go negative.
- P&L counts only paid invoices as revenue and uses purchases as cost of goods (simplification).
- No pagination (Supabase returns max 1000 rows).

**Code quality**
- `.bak` files, dead code (`routers/ai_models.py`, `multipage.py`, `invoice_service.py`), duplicate `/item-details` route, business logic inside routers, OCR patches layered.
- README out of date (mentions XGBoost which isn't used; wrong AI endpoint paths; `ml/training` described as the training scripts).
- Tests: only 2 health checks; CI ignores failures.

**ML**: tiny single-shop data, weak models vs baselines (Part 3), no scheduler installed, results committed to git.

**Improvements to propose:** enforce permissions server-side, atomic Postgres functions, pagination, real tests (OCR labelled set + parser unit tests), more shops and 2+ years of data for seasonality/holidays, include purchases and collections in cash flow, results in a table/object storage with versioning, scheduled retraining in the cloud.

---

## PART 6. 12 one-line answers to memorise
1. **What is it?** A back-office and AI backend for Nepali retail shops: billing, stock, udharo, purchases, reports, bill scanning and six AI helpers.
2. **Why JSON for ML results?** Batch-written weekly, read-only afterwards, isolated from the live DB, published atomically with temp file + `os.replace`.
3. **Is the publish atomic?** Per file, yes: write `.tmp` in the same folder, then `os.replace` (atomic rename on POSIX). Not across multiple files.
4. **How accurate is OCR?** Not formally measured; it reconciles bill arithmetic and flags doubts; a human approves. Plan: 30-50 labelled bills, field-level accuracy.
5. **Why logistic regression for credit?** Interpretable, best AUC (0.965 vs 0.947) on 45 customers; LightGBM flagged nobody at 0.5.
6. **Why not accuracy?** Imbalanced classes; precision, recall, AUC show what accuracy hides.
7. **Do the forecasts beat naive?** About 1% better, within noise; they add calibrated intervals and a trend signal.
8. **How is leakage avoided?** Features computed only from data before the cut-off date; temporal splits with a purge gap (churn, restock).
9. **Why Tesseract?** Free, private, no API dependency; Gemini as optional fallback.
10. **How do you stop a wrong OCR read being saved?** Validator flags, human review, duplicate check, approval step.
11. **How do you keep shops separate?** `store_id` on every row, membership check on `X-Store-Id`, RLS, per-shop result folders.
12. **What would you improve first?** Server-side permission enforcement and atomic DB functions, then real tests and more data.
