from fastapi import APIRouter, Depends, Query
from middleware.auth_middleware import get_current_user_with_role
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
    user=Depends(get_current_user_with_role)
):
    supabase = get_supabase_admin()
    store_id = user["store_id"]

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
    user=Depends(get_current_user_with_role)
):
    supabase = get_supabase_admin()
    store_id = user["store_id"]
    invoices = supabase.table("invoices").select("invoice_date, total, status").eq("store_id", store_id).gte("invoice_date", str(start_date)).lte("invoice_date", str(end_date)).execute().data
    return {"invoices": invoices, "total": round(sum(i["total"] for i in invoices), 2), "count": len(invoices)}


@router.get("/top-products")
async def top_products(limit: int = 10, user=Depends(get_current_user_with_role)):
    supabase = get_supabase_admin()
    items = supabase.table("invoice_items").select("product_name, quantity, total").execute().data
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
async def sales_report(start_date: date, end_date: date, user=Depends(get_current_user_with_role)):
    supabase = get_supabase_admin()
    store_id = user["store_id"]
    cust_map = _customer_map(supabase, store_id)

    res = (
        supabase.table("invoices")
        .select("invoice_number, invoice_date, total, paid_amount, status, customer_id")
        .eq("store_id", store_id)
        .gte("invoice_date", str(start_date))
        .lte("invoice_date", str(end_date))
        .order("invoice_date", desc=True)
        .execute()
    )
    rows = []
    for r in res.data or []:
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
async def purchase_report(start_date: date, end_date: date, user=Depends(get_current_user_with_role)):
    supabase = get_supabase_admin()
    store_id = user["store_id"]
    supp_map = _supplier_map(supabase, store_id)

    res = (
        supabase.table("purchases")
        .select("bill_number, purchase_date, total, status, supplier_id")
        .eq("store_id", store_id)
        .gte("purchase_date", str(start_date))
        .lte("purchase_date", str(end_date))
        .order("purchase_date", desc=True)
        .execute()
    )
    rows = [{
        "bill_number": r.get("bill_number"),
        "date": r.get("purchase_date"),
        "supplier_name": supp_map.get(r.get("supplier_id"), "-"),
        "total": r.get("total"),
        "status": r.get("status"),
    } for r in (res.data or [])]
    return {"rows": rows}


# ---------------- Day Book ----------------
@router.get("/daybook")
async def daybook_report(start_date: date, end_date: date, user=Depends(get_current_user_with_role)):
    supabase = get_supabase_admin()
    store_id = user["store_id"]
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
async def all_transactions_report(start_date: date, end_date: date, user=Depends(get_current_user_with_role)):
    return await daybook_report(start_date, end_date, user)


# ---------------- Party Statement ----------------
@router.get("/party-statement")
async def party_statement_report(party_id: UUID, start_date: date, end_date: date, user=Depends(get_current_user_with_role)):
    supabase = get_supabase_admin()
    store_id = user["store_id"]

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
async def all_parties_report(user=Depends(get_current_user_with_role)):
    supabase = get_supabase_admin()
    store_id = user["store_id"]

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
async def item_list_report(user=Depends(get_current_user_with_role)):
    supabase = get_supabase_admin()
    store_id = user["store_id"]
    cat_map = _category_map(supabase, store_id)

    res = supabase.table("products").select("sku, name, cost_price, selling_price, stock_quantity, category_id").eq("store_id", store_id).execute()
    rows = [{
        "sku": r.get("sku"),
        "name": r.get("name"),
        "category": cat_map.get(r.get("category_id"), "-"),
        "cost_price": r.get("cost_price"),
        "selling_price": r.get("selling_price"),
        "stock_quantity": r.get("stock_quantity"),
    } for r in (res.data or [])]
    return {"rows": rows}


# ---------------- Low Stock Summary ----------------
@router.get("/low-stock")
async def low_stock_report(user=Depends(get_current_user_with_role)):
    supabase = get_supabase_admin()
    store_id = user["store_id"]

    res = supabase.table("products").select("sku, name, stock_quantity, reorder_level").eq("store_id", store_id).execute()
    rows = [
        {"sku": r.get("sku"), "name": r.get("name"), "stock_quantity": r.get("stock_quantity"), "reorder_level": r.get("reorder_level")}
        for r in (res.data or [])
        if (r.get("stock_quantity") or 0) <= (r.get("reorder_level") or 0)
    ]
    return {"rows": rows}


# ---------------- Stock Quantity Report ----------------
@router.get("/stock-quantity")
async def stock_quantity_report(start_date: date, end_date: date, user=Depends(get_current_user_with_role)):
    supabase = get_supabase_admin()
    store_id = user["store_id"]

    products = supabase.table("products").select("id, sku, name, stock_quantity").eq("store_id", store_id).execute().data or []

    inv_ids = [i["id"] for i in supabase.table("invoices").select("id").eq("store_id", store_id).gte("invoice_date", str(start_date)).lte("invoice_date", str(end_date)).execute().data or []]
    pur_ids = [p["id"] for p in supabase.table("purchases").select("id").eq("store_id", store_id).gte("purchase_date", str(start_date)).lte("purchase_date", str(end_date)).execute().data or []]

    sold_by_product = defaultdict(float)
    purchased_by_product = defaultdict(float)

    if inv_ids:
        for it in supabase.table("invoice_items").select("product_id, quantity").in_("invoice_id", inv_ids).execute().data or []:
            if it.get("product_id"):
                sold_by_product[it["product_id"]] += it.get("quantity") or 0

    if pur_ids:
        for it in supabase.table("purchase_items").select("product_id, quantity").in_("purchase_id", pur_ids).execute().data or []:
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
async def income_expense_report(start_date: date, end_date: date, user=Depends(get_current_user_with_role)):
    supabase = get_supabase_admin()
    store_id = user["store_id"]

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
async def expense_category_report(start_date: date, end_date: date, user=Depends(get_current_user_with_role)):
    supabase = get_supabase_admin()
    store_id = user["store_id"]

    expenses = supabase.table("expenses").select("category, amount").eq("store_id", store_id).gte("expense_date", str(start_date)).lte("expense_date", str(end_date)).execute().data or []

    agg = defaultdict(lambda: {"count": 0, "total": 0})
    for e in expenses:
        cat = e.get("category") or "Other"
        agg[cat]["count"] += 1
        agg[cat]["total"] += e.get("amount") or 0

    rows = [{"category": k, "count": v["count"], "total": round(v["total"], 2)} for k, v in agg.items()]
    rows.sort(key=lambda x: x["total"], reverse=True)
    return {"rows": rows}
