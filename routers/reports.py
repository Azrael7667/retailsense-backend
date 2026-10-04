from fastapi import APIRouter, Depends, Query
from middleware.auth_middleware import get_active_store_id
from database import get_supabase_admin
from datetime import date
from typing import Optional
from uuid import UUID
from collections import defaultdict

router = APIRouter()


@router.get("/profit-loss")
async def profit_loss(
    start_date: date,
    end_date: date,
    store_id: str = Depends(get_active_store_id)
):
    supabase = get_supabase_admin()

    invoices = supabase.table("invoices").select("total").eq("store_id", store_id).eq("status", "paid").gte("invoice_date", str(start_date)).lte("invoice_date", str(end_date)).execute().data
    purchases = supabase.table("purchases").select("total").eq("store_id", store_id).gte("purchase_date", str(start_date)).lte("purchase_date", str(end_date)).execute().data
    expenses = supabase.table("expenses").select("amount").eq("store_id", store_id).gte("expense_date", str(start_date)).lte("expense_date", str(end_date)).execute().data

    total_revenue  = sum(i["total"] for i in invoices)
    total_purchase = sum(p["total"] for p in purchases)
    total_expenses = sum(e["amount"] for e in expenses)
    gross_profit   = total_revenue - total_purchase
    net_profit     = gross_profit - total_expenses

    return {
        "period": {"start": str(start_date), "end": str(end_date)},
        "revenue":        round(total_revenue, 2),
        "cost_of_goods":  round(total_purchase, 2),
        "gross_profit":   round(gross_profit, 2),
        "expenses":       round(total_expenses, 2),
        "net_profit":     round(net_profit, 2),
        "gross_margin":   round((gross_profit / total_revenue * 100) if total_revenue else 0, 2),
    }


@router.get("/sales-summary")
async def sales_summary(
    start_date: date,
    end_date: date,
    store_id: str = Depends(get_active_store_id)
):
    supabase = get_supabase_admin()
    invoices = supabase.table("invoices").select("invoice_date, total, status").eq("store_id", store_id).gte("invoice_date", str(start_date)).lte("invoice_date", str(end_date)).execute().data
    return {"invoices": invoices, "total": round(sum(i["total"] for i in invoices), 2), "count": len(invoices)}


@router.get("/top-products")
async def top_products(limit: int = 10, store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()
    # NOTE: this query was never scoped to store_id at all before — it read
    # invoice_items across EVERY store. Fixed here by joining through
    # invoices, which does carry store_id.
    inv_ids = [i["id"] for i in supabase.table("invoices").select("id").eq("store_id", store_id).execute().data or []]
    items = []
    if inv_ids:
        items = supabase.table("invoice_items").select("product_name, quantity, total").in_("invoice_id", inv_ids).execute().data or []
    agg = defaultdict(lambda: {"quantity": 0, "revenue": 0})
    for item in items:
        agg[item["product_name"]]["quantity"] += item["quantity"]
        agg[item["product_name"]]["revenue"]  += item["total"]
    sorted_products = sorted(agg.items(), key=lambda x: x[1]["revenue"], reverse=True)[:limit]
    return [{"product": k, **v} for k, v in sorted_products]


def _customer_map(supabase, store_id):
    rows = supabase.table("customers").select("id, name").eq("store_id", store_id).execute().data or []
    return {r["id"]: r["name"] for r in rows}


def _supplier_map(supabase, store_id):
    rows = supabase.table("suppliers").select("id, name").eq("store_id", store_id).execute().data or []
    return {r["id"]: r["name"] for r in rows}


def _category_map(supabase, store_id):
    rows = supabase.table("categories").select("id, name").eq("store_id", store_id).execute().data or []
    return {r["id"]: r["name"] for r in rows}


# ---------------- Sales Report ----------------
@router.get("/sales")
async def sales_report(start_date: date, end_date: date, store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()
    cust_map = _customer_map(supabase, store_id)

    data = _all(lambda: (
        supabase.table("invoices")
        .select("invoice_number, invoice_date, total, paid_amount, status, customer_id")
        .eq("store_id", store_id)
        .gte("invoice_date", str(start_date))
        .lte("invoice_date", str(end_date))
        .order("invoice_date", desc=True)
        .order("id")
    ))
    rows = []
    for r in data:
        total = r.get("total") or 0
        paid = r.get("paid_amount") or 0
        rows.append({
            "invoice_number": r.get("invoice_number"),
            "date": r.get("invoice_date"),
            "customer_name": cust_map.get(r.get("customer_id"), "Walk-in"),
            "total": total,
            "paid_amount": paid,
            "balance": round(total - paid, 2),
            "status": r.get("status"),
        })
    return {"rows": rows}


# ---------------- Purchase Report ----------------
@router.get("/purchase")
async def purchase_report(start_date: date, end_date: date, store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()
    supp_map = _supplier_map(supabase, store_id)

    data = _all(lambda: (
        supabase.table("purchases")
        .select("bill_number, purchase_date, total, status, supplier_id")
        .eq("store_id", store_id)
        .gte("purchase_date", str(start_date))
        .lte("purchase_date", str(end_date))
        .order("purchase_date", desc=True)
        .order("id")
    ))
    rows = [{
        "bill_number": r.get("bill_number"),
        "date": r.get("purchase_date"),
        "supplier_name": supp_map.get(r.get("supplier_id"), "-"),
        "total": r.get("total"),
        "status": r.get("status"),
    } for r in data]
    return {"rows": rows}


# ---------------- Day Book ----------------
@router.get("/daybook")
async def daybook_report(start_date: date, end_date: date, store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()
    cust_map = _customer_map(supabase, store_id)
    supp_map = _supplier_map(supabase, store_id)

    sales = supabase.table("invoices").select("invoice_number, invoice_date, total, customer_id").eq("store_id", store_id).gte("invoice_date", str(start_date)).lte("invoice_date", str(end_date)).execute().data or []
    purchases = supabase.table("purchases").select("bill_number, purchase_date, total, supplier_id").eq("store_id", store_id).gte("purchase_date", str(start_date)).lte("purchase_date", str(end_date)).execute().data or []

    rows = []
    for r in sales:
        rows.append({"date": r.get("invoice_date"), "type": "Sale", "reference": r.get("invoice_number"), "party_name": cust_map.get(r.get("customer_id"), "Walk-in"), "amount": r.get("total")})
    for r in purchases:
        rows.append({"date": r.get("purchase_date"), "type": "Purchase", "reference": r.get("bill_number"), "party_name": supp_map.get(r.get("supplier_id"), "-"), "amount": r.get("total")})
    rows.sort(key=lambda x: x["date"] or "", reverse=True)
    return {"rows": rows}


# ---------------- All Transactions ----------------
@router.get("/all-transactions")
async def all_transactions_report(start_date: date, end_date: date, store_id: str = Depends(get_active_store_id)):
    return await daybook_report(start_date, end_date, store_id)


# ---------------- Party Statement ----------------
@router.get("/party-statement")
async def party_statement_report(party_id: UUID, start_date: date, end_date: date, store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()

    res = (
        supabase.table("invoices")
        .select("invoice_number, invoice_date, total, paid_amount")
        .eq("store_id", store_id)
        .eq("customer_id", str(party_id))
        .gte("invoice_date", str(start_date))
        .lte("invoice_date", str(end_date))
        .order("invoice_date")
        .execute()
    )
    running = 0
    rows = []
    for r in res.data or []:
        total = r.get("total") or 0
        paid = r.get("paid_amount") or 0
        running += (total - paid)
        rows.append({
            "date": r.get("invoice_date"),
            "reference": r.get("invoice_number"),
            "debit": total,
            "credit": paid,
            "balance": round(running, 2),
        })
    return {"rows": rows}


# ---------------- All Party Report ----------------
@router.get("/all-parties")
async def all_parties_report(store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()

    res = supabase.table("customers").select("name, phone, balance, credit_limit").eq("store_id", store_id).execute()
    rows = [{
        "party_name": r.get("name"),
        "phone": r.get("phone"),
        "credit_limit": r.get("credit_limit"),
        "balance": r.get("balance"),
    } for r in (res.data or [])]
    return {"rows": rows}


# ---------------- Item List Report ----------------
@router.get("/item-list")
async def item_list_report(store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()
    cat_map = _category_map(supabase, store_id)

    data = _all(lambda: supabase.table("products").select("sku, name, cost_price, selling_price, stock_quantity, category_id").eq("store_id", store_id).order("id"))
    rows = [{
        "sku": r.get("sku"),
        "name": r.get("name"),
        "category": cat_map.get(r.get("category_id"), "-"),
        "cost_price": r.get("cost_price"),
        "selling_price": r.get("selling_price"),
        "stock_quantity": r.get("stock_quantity"),
    } for r in data]
    return {"rows": rows}


# ---------------- Low Stock Summary ----------------
@router.get("/low-stock")
async def low_stock_report(store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()

    data = _all(lambda: supabase.table("products").select("sku, name, stock_quantity, reorder_level").eq("store_id", store_id).order("id"))
    rows = [
        {"sku": r.get("sku"), "name": r.get("name"), "stock_quantity": r.get("stock_quantity"), "reorder_level": r.get("reorder_level")}
        for r in data
        if (r.get("stock_quantity") or 0) <= (r.get("reorder_level") or 0)
    ]
    return {"rows": rows}


# ---------------- Stock Quantity Report ----------------
@router.get("/stock-quantity")
async def stock_quantity_report(start_date: date, end_date: date, store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()

    products = _all(lambda: supabase.table("products").select("id, sku, name, stock_quantity").eq("store_id", store_id).order("id"))

    inv_ids = [i["id"] for i in _all(lambda: supabase.table("invoices").select("id").eq("store_id", store_id).gte("invoice_date", str(start_date)).lte("invoice_date", str(end_date)).order("id"))]
    pur_ids = [p["id"] for p in _all(lambda: supabase.table("purchases").select("id").eq("store_id", store_id).gte("purchase_date", str(start_date)).lte("purchase_date", str(end_date)).order("id"))]

    sold_by_product = defaultdict(float)
    purchased_by_product = defaultdict(float)

    if inv_ids:
        for it in _items_chunked(supabase, "invoice_items", "invoice_id", "product_id, quantity", inv_ids):
            if it.get("product_id"):
                sold_by_product[it["product_id"]] += it.get("quantity") or 0

    if pur_ids:
        for it in _items_chunked(supabase, "purchase_items", "purchase_id", "product_id, quantity", pur_ids):
            if it.get("product_id"):
                purchased_by_product[it["product_id"]] += it.get("quantity") or 0

    rows = []
    for p in products:
        pid = p["id"]
        closing = p.get("stock_quantity") or 0
        sold = sold_by_product.get(pid, 0)
        purchased = purchased_by_product.get(pid, 0)
        opening = closing - purchased + sold
        rows.append({
            "sku": p.get("sku"), "name": p.get("name"),
            "opening_qty": round(opening, 2), "purchased_qty": round(purchased, 2),
            "sold_qty": round(sold, 2), "closing_qty": closing,
        })
    return {"rows": rows}


# ---------------- Income Expense Report ----------------
@router.get("/income-expense")
async def income_expense_report(start_date: date, end_date: date, store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()

    sales = supabase.table("invoices").select("invoice_date, total, invoice_number").eq("store_id", store_id).eq("status", "paid").gte("invoice_date", str(start_date)).lte("invoice_date", str(end_date)).execute().data or []
    expenses = supabase.table("expenses").select("expense_date, amount, category, description").eq("store_id", store_id).gte("expense_date", str(start_date)).lte("expense_date", str(end_date)).execute().data or []

    rows = []
    for s in sales:
        rows.append({"date": s.get("invoice_date"), "type": "Income", "category": "Sales", "description": s.get("invoice_number"), "amount": s.get("total")})
    for e in expenses:
        rows.append({"date": e.get("expense_date"), "type": "Expense", "category": e.get("category") or "Other", "description": e.get("description"), "amount": e.get("amount")})
    rows.sort(key=lambda x: x["date"] or "", reverse=True)
    return {"rows": rows}


# ---------------- Expense Category ----------------
@router.get("/expense-category")
async def expense_category_report(start_date: date, end_date: date, store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()

    expenses = supabase.table("expenses").select("category, amount").eq("store_id", store_id).gte("expense_date", str(start_date)).lte("expense_date", str(end_date)).execute().data or []

    agg = defaultdict(lambda: {"count": 0, "total": 0})
    for e in expenses:
        cat = e.get("category") or "Other"
        agg[cat]["count"] += 1
        agg[cat]["total"] += e.get("amount") or 0

    rows = [{"category": k, "count": v["count"], "total": round(v["total"], 2)} for k, v in agg.items()]
    rows.sort(key=lambda x: x["total"], reverse=True)
    return {"rows": rows}


# ---------------- paging helper (Supabase caps one request at 1000 rows) ----------------
def _all(build):
    rows, page = [], 0
    while True:
        data = build().range(page * 1000, (page + 1) * 1000 - 1).execute().data or []
        rows.extend(data)
        if len(data) < 1000:
            break
        page += 1
    return rows


def _id_map(supabase, table, col, ids):
    out, ids = {}, list({i for i in ids if i})
    for k in range(0, len(ids), 200):
        res = supabase.table(table).select(f"id, {col}").in_("id", ids[k:k + 200]).execute().data or []
        out.update({r["id"]: r[col] for r in res})
    return out


# ---------------- Sales Return Report ----------------
@router.get("/sales-return")
async def sales_return_report(start_date: date, end_date: date, store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()
    cust_map = _customer_map(supabase, store_id)
    data = _all(lambda: supabase.table("sales_returns")
                .select("return_number, return_date, invoice_id, customer_id, reason, total_refund_amount, credit_applied_amount, cash_refunded_amount")
                .eq("store_id", store_id)
                .gte("return_date", str(start_date)).lte("return_date", str(end_date))
                .order("return_date", desc=True))
    inv_map = _id_map(supabase, "invoices", "invoice_number", [r.get("invoice_id") for r in data])
    return {"rows": [{
        "return_number": r.get("return_number"),
        "date": r.get("return_date"),
        "customer_name": cust_map.get(r.get("customer_id"), "Walk-in"),
        "invoice_number": inv_map.get(r.get("invoice_id")),
        "total_refund_amount": r.get("total_refund_amount") or 0,
        "credit_applied_amount": r.get("credit_applied_amount") or 0,
        "cash_refunded_amount": r.get("cash_refunded_amount") or 0,
        "reason": r.get("reason"),
    } for r in data]}


# ---------------- Purchase Return Report ----------------
@router.get("/purchase-return")
async def purchase_return_report(start_date: date, end_date: date, store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()
    supp_map = _supplier_map(supabase, store_id)
    data = _all(lambda: supabase.table("purchase_returns")
                .select("return_number, return_date, purchase_id, supplier_id, reason, total_return_amount, credit_applied_amount, cash_refunded_amount")
                .eq("store_id", store_id)
                .gte("return_date", str(start_date)).lte("return_date", str(end_date))
                .order("return_date", desc=True))
    bill_map = _id_map(supabase, "purchases", "bill_number", [r.get("purchase_id") for r in data])
    return {"rows": [{
        "return_number": r.get("return_number"),
        "date": r.get("return_date"),
        "supplier_name": supp_map.get(r.get("supplier_id"), "-"),
        "bill_number": bill_map.get(r.get("purchase_id")),
        "total_return_amount": r.get("total_return_amount") or 0,
        "credit_applied_amount": r.get("credit_applied_amount") or 0,
        "cash_refunded_amount": r.get("cash_refunded_amount") or 0,
        "reason": r.get("reason"),
    } for r in data]}



def _items_chunked(supabase, table, fk, cols, ids):
    """Fetch line items for many parent ids: 100 ids per request (URL limit), paged past the 1000-row cap."""
    out = []
    for k in range(0, len(ids), 100):
        chunk = ids[k:k + 100]
        out.extend(_all(lambda: supabase.table(table).select(cols).in_(fk, chunk).order("id")))
    return out


# ---------------- Item Details Report ----------------
def _by_ids(supabase, table, cols, ids, extra=None):
    out, ids = [], list({i for i in ids if i})
    for k in range(0, len(ids), 100):
        q = supabase.table(table).select(cols).in_("id", ids[k:k + 100])
        if extra:
            q = extra(q)
        out.extend(q.execute().data or [])
    return out


@router.get("/item-details")
async def item_details_report(product_id: str, start_date: date, end_date: date, store_id: str = Depends(get_active_store_id)):
    from fastapi import HTTPException
    supabase = get_supabase_admin()
    found = supabase.table("products").select("id, sku, name, unit, cost_price, selling_price, stock_quantity, reorder_level, category_id").eq("id", product_id).eq("store_id", store_id).limit(1).execute().data or []
    if not found:
        raise HTTPException(status_code=404, detail="Item not found")
    p = found[0]
    cat = _category_map(supabase, store_id).get(p.get("category_id"), "-")
    cust_map = _customer_map(supabase, store_id)
    supp_map = _supplier_map(supabase, store_id)

    def in_range(col):
        return lambda q: q.eq("store_id", store_id).gte(col, str(start_date)).lte(col, str(end_date))

    rows = []

    s_items = _all(lambda: supabase.table("invoice_items").select("invoice_id, quantity, unit_price, total").eq("product_id", product_id).order("id"))
    invs = {r["id"]: r for r in _by_ids(supabase, "invoices", "id, invoice_number, invoice_date, customer_id", [i["invoice_id"] for i in s_items], in_range("invoice_date"))}
    for it in s_items:
        inv = invs.get(it["invoice_id"])
        if inv:
            rows.append({"date": inv["invoice_date"], "type": "Sale", "reference": inv["invoice_number"],
                         "party_name": cust_map.get(inv.get("customer_id"), "Walk-in"),
                         "qty_in": 0, "qty_out": it.get("quantity") or 0,
                         "rate": it.get("unit_price") or 0, "amount": it.get("total") or 0})

    b_items = _all(lambda: supabase.table("purchase_items").select("purchase_id, quantity, unit_price, discount_percent, total").eq("product_id", product_id).order("id"))
    purs = {r["id"]: r for r in _by_ids(supabase, "purchases", "id, bill_number, purchase_date, supplier_id", [i["purchase_id"] for i in b_items], in_range("purchase_date"))}
    for it in b_items:
        pu = purs.get(it["purchase_id"])
        if pu:
            net = round((it.get("unit_price") or 0) * (1 - (it.get("discount_percent") or 0) / 100.0), 2)
            rows.append({"date": pu["purchase_date"], "type": "Purchase", "reference": pu["bill_number"],
                         "party_name": supp_map.get(pu.get("supplier_id"), "-"),
                         "qty_in": it.get("quantity") or 0, "qty_out": 0,
                         "rate": net, "amount": it.get("total") or 0})

    sr_items = _all(lambda: supabase.table("sales_return_items").select("return_id, quantity_returned, unit_price, line_refund_amount, restock_flag").eq("product_id", product_id).order("id"))
    srs = {r["id"]: r for r in _by_ids(supabase, "sales_returns", "id, return_number, return_date, customer_id", [i["return_id"] for i in sr_items], in_range("return_date"))}
    for it in sr_items:
        r = srs.get(it["return_id"])
        if r:
            q = it.get("quantity_returned") or 0
            rows.append({"date": r["return_date"], "type": "Sales Return", "reference": r["return_number"],
                         "party_name": cust_map.get(r.get("customer_id"), "Walk-in"),
                         "qty_in": q if it.get("restock_flag") else 0, "qty_out": 0,
                         "rate": it.get("unit_price") or 0, "amount": it.get("line_refund_amount") or 0})

    pr_items = _all(lambda: supabase.table("purchase_return_items").select("return_id, quantity_returned, unit_price, line_return_amount").eq("product_id", product_id).order("id"))
    prs = {r["id"]: r for r in _by_ids(supabase, "purchase_returns", "id, return_number, return_date, supplier_id", [i["return_id"] for i in pr_items], in_range("return_date"))}
    for it in pr_items:
        r = prs.get(it["return_id"])
        if r:
            rows.append({"date": r["return_date"], "type": "Purchase Return", "reference": r["return_number"],
                         "party_name": supp_map.get(r.get("supplier_id"), "-"),
                         "qty_in": 0, "qty_out": it.get("quantity_returned") or 0,
                         "rate": it.get("unit_price") or 0, "amount": it.get("line_return_amount") or 0})

    rows.sort(key=lambda r: r["date"] or "", reverse=True)

    def tot(t, k):
        return round(sum(r[k] for r in rows if r["type"] == t), 2)

    return {
        "item": {**p, "category": cat},
        "rows": rows,
        "summary": {
            "purchased_qty": tot("Purchase", "qty_in"), "sold_qty": tot("Sale", "qty_out"),
            "sales_return_qty": tot("Sales Return", "qty_in"), "purchase_return_qty": tot("Purchase Return", "qty_out"),
            "purchased_amount": tot("Purchase", "amount"), "sold_amount": tot("Sale", "amount"),
        },
    }


# ---------------- Item Details Report ----------------
def _by_ids(supabase, table, cols, ids, extra=None):
    out, ids = [], list({i for i in ids if i})
    for k in range(0, len(ids), 100):
        q = supabase.table(table).select(cols).in_("id", ids[k:k + 100])
        if extra:
            q = extra(q)
        out.extend(q.execute().data or [])
    return out


@router.get("/item-details")
async def item_details_report(product_id: str, start_date: date, end_date: date, store_id: str = Depends(get_active_store_id)):
    from fastapi import HTTPException
    supabase = get_supabase_admin()
    found = supabase.table("products").select("id, sku, name, unit, cost_price, selling_price, stock_quantity, reorder_level, category_id").eq("id", product_id).eq("store_id", store_id).limit(1).execute().data or []
    if not found:
        raise HTTPException(status_code=404, detail="Item not found")
    p = found[0]
    cat = _category_map(supabase, store_id).get(p.get("category_id"), "-")
    cust_map = _customer_map(supabase, store_id)
    supp_map = _supplier_map(supabase, store_id)

    def in_range(col):
        return lambda q: q.eq("store_id", store_id).gte(col, str(start_date)).lte(col, str(end_date))

    rows = []

    s_items = _all(lambda: supabase.table("invoice_items").select("invoice_id, quantity, unit_price, total").eq("product_id", product_id).order("id"))
    invs = {r["id"]: r for r in _by_ids(supabase, "invoices", "id, invoice_number, invoice_date, customer_id", [i["invoice_id"] for i in s_items], in_range("invoice_date"))}
    for it in s_items:
        inv = invs.get(it["invoice_id"])
        if inv:
            rows.append({"date": inv["invoice_date"], "type": "Sale", "reference": inv["invoice_number"],
                         "party_name": cust_map.get(inv.get("customer_id"), "Walk-in"),
                         "qty_in": 0, "qty_out": it.get("quantity") or 0,
                         "rate": it.get("unit_price") or 0, "amount": it.get("total") or 0})

    b_items = _all(lambda: supabase.table("purchase_items").select("purchase_id, quantity, unit_price, discount_percent, total").eq("product_id", product_id).order("id"))
    purs = {r["id"]: r for r in _by_ids(supabase, "purchases", "id, bill_number, purchase_date, supplier_id", [i["purchase_id"] for i in b_items], in_range("purchase_date"))}
    for it in b_items:
        pu = purs.get(it["purchase_id"])
        if pu:
            net = round((it.get("unit_price") or 0) * (1 - (it.get("discount_percent") or 0) / 100.0), 2)
            rows.append({"date": pu["purchase_date"], "type": "Purchase", "reference": pu["bill_number"],
                         "party_name": supp_map.get(pu.get("supplier_id"), "-"),
                         "qty_in": it.get("quantity") or 0, "qty_out": 0,
                         "rate": net, "amount": it.get("total") or 0})

    sr_items = _all(lambda: supabase.table("sales_return_items").select("return_id, quantity_returned, unit_price, line_refund_amount, restock_flag").eq("product_id", product_id).order("id"))
    srs = {r["id"]: r for r in _by_ids(supabase, "sales_returns", "id, return_number, return_date, customer_id", [i["return_id"] for i in sr_items], in_range("return_date"))}
    for it in sr_items:
        r = srs.get(it["return_id"])
        if r:
            q = it.get("quantity_returned") or 0
            rows.append({"date": r["return_date"], "type": "Sales Return", "reference": r["return_number"],
                         "party_name": cust_map.get(r.get("customer_id"), "Walk-in"),
                         "qty_in": q if it.get("restock_flag") else 0, "qty_out": 0,
                         "rate": it.get("unit_price") or 0, "amount": it.get("line_refund_amount") or 0})

    pr_items = _all(lambda: supabase.table("purchase_return_items").select("return_id, quantity_returned, unit_price, line_return_amount").eq("product_id", product_id).order("id"))
    prs = {r["id"]: r for r in _by_ids(supabase, "purchase_returns", "id, return_number, return_date, supplier_id", [i["return_id"] for i in pr_items], in_range("return_date"))}
    for it in pr_items:
        r = prs.get(it["return_id"])
        if r:
            rows.append({"date": r["return_date"], "type": "Purchase Return", "reference": r["return_number"],
                         "party_name": supp_map.get(r.get("supplier_id"), "-"),
                         "qty_in": 0, "qty_out": it.get("quantity_returned") or 0,
                         "rate": it.get("unit_price") or 0, "amount": it.get("line_return_amount") or 0})

    rows.sort(key=lambda r: r["date"] or "", reverse=True)

    def tot(t, k):
        return round(sum(r[k] for r in rows if r["type"] == t), 2)

    return {
        "item": {**p, "category": cat},
        "rows": rows,
        "summary": {
            "purchased_qty": tot("Purchase", "qty_in"), "sold_qty": tot("Sale", "qty_out"),
            "sales_return_qty": tot("Sales Return", "qty_in"), "purchase_return_qty": tot("Purchase Return", "qty_out"),
            "purchased_amount": tot("Purchase", "amount"), "sold_amount": tot("Sale", "amount"),
        },
    }
