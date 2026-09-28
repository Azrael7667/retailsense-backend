from fastapi import APIRouter, Depends, HTTPException
from schemas.purchase_return import PurchaseReturnCreate
from middleware.auth_middleware import get_current_user, get_active_store_id
from database import get_supabase_admin
from utils.doc_numbers import next_doc_number, creator_name

router = APIRouter()


def _net_price(unit_price: float, discount_percent: float) -> float:
    disc = discount_percent or 0
    return round((unit_price or 0) * (1 - disc / 100.0), 4)


def _returned_by_item(supabase, purchase_id: str):
    """Quantity already returned per purchase_item_id."""
    prior = supabase.table("purchase_returns").select("id") \
        .eq("purchase_id", purchase_id).execute().data or []
    prior_ids = [r["id"] for r in prior]
    returned = {}
    if prior_ids:
        rows = supabase.table("purchase_return_items").select("purchase_item_id, quantity_returned") \
            .in_("return_id", prior_ids).execute().data or []
        for row in rows:
            key = row["purchase_item_id"]
            returned[key] = returned.get(key, 0) + (row["quantity_returned"] or 0)
    return returned


def _supplier_payable(supabase, supplier_id):
    """What we currently owe this supplier across all bills (0 for direct purchases)."""
    if not supplier_id:
        return 0.0
    sup = supabase.table("suppliers").select("balance").eq("id", supplier_id).single().execute()
    if not sup.data:
        return 0.0
    return max(float(sup.data["balance"] or 0), 0.0)


@router.get("/")
async def list_purchase_returns(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()
    return supabase.table("purchase_returns") \
        .select("*, suppliers(name, phone), purchases(bill_number, purchase_date)") \
        .eq("store_id", store_id) \
        .order("return_date", desc=True) \
        .order("created_at", desc=True) \
        .limit(500) \
        .execute().data


@router.get("/returnable/{purchase_id}")
async def returnable_for_purchase(purchase_id: str, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    """Everything the create screen needs: the bill, its lines, how much of each can still be
    returned, and the supplier's current payable (a return reduces that first)."""
    supabase = get_supabase_admin()
    pur = supabase.table("purchases") \
        .select("id, bill_number, purchase_date, total, paid_amount, supplier_id") \
        .eq("id", purchase_id).eq("store_id", store_id).single().execute()
    if not pur.data:
        raise HTTPException(status_code=404, detail="Purchase not found")

    items = supabase.table("purchase_items").select("*").eq("purchase_id", purchase_id).execute().data or []
    returned = _returned_by_item(supabase, purchase_id)

    lines = []
    for it in items:
        net = _net_price(it["unit_price"], it.get("discount_percent") or 0)
        lines.append({
            "id": it["id"],
            "product_id": it.get("product_id"),
            "product_name": it["product_name"],
            "quantity": it["quantity"],
            "unit_price": it["unit_price"],
            "discount_percent": it.get("discount_percent") or 0,
            "net_price": net,
            "returnable": max(0, round(it["quantity"] - returned.get(it["id"], 0), 4)),
        })
    return {
        "purchase": pur.data,
        "supplier_balance": _supplier_payable(supabase, pur.data.get("supplier_id")),
        "items": lines,
    }


@router.get("/{return_id}")
async def get_purchase_return(return_id: str, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()
    ret = supabase.table("purchase_returns") \
        .select("*, suppliers(name, phone), purchases(bill_number, purchase_date)") \
        .eq("id", return_id).eq("store_id", store_id).single().execute()
    if not ret.data:
        raise HTTPException(status_code=404, detail="Purchase return not found")
    items = supabase.table("purchase_return_items").select("*").eq("return_id", return_id).execute().data or []
    return {**ret.data, "items": items}


@router.post("/")
async def create_purchase_return(body: PurchaseReturnCreate, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()

    pur = supabase.table("purchases").select("*") \
        .eq("id", str(body.purchase_id)).eq("store_id", store_id).single().execute()
    if not pur.data:
        raise HTTPException(status_code=404, detail="Purchase not found")
    purchase = pur.data

    p_items = supabase.table("purchase_items").select("*") \
        .eq("purchase_id", str(body.purchase_id)).execute().data or []
    by_id = {row["id"]: row for row in p_items}
    returned = _returned_by_item(supabase, str(body.purchase_id))

    # ── Validate everything before writing anything ──
    return_items = []
    total_return = 0.0
    stock_needed = {}

    for line in body.items:
        row = by_id.get(str(line.purchase_item_id))
        if not row:
            raise HTTPException(status_code=400, detail="Item was not part of this purchase")
        if line.quantity_returned <= 0:
            raise HTTPException(status_code=400, detail="quantity_returned must be greater than 0")

        max_returnable = round(row["quantity"] - returned.get(row["id"], 0), 4)
        if line.quantity_returned > max_returnable:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot return {line.quantity_returned} of '{row['product_name']}' — only {max_returnable} returnable",
            )

        net = _net_price(row["unit_price"], row.get("discount_percent") or 0)
        line_amount = round(line.quantity_returned * net, 2)
        total_return = round(total_return + line_amount, 2)

        if row.get("product_id"):
            stock_needed[row["product_id"]] = stock_needed.get(row["product_id"], 0) + line.quantity_returned

        return_items.append({
            "purchase_item_id": row["id"],
            "product_id": row.get("product_id"),
            "product_name": row["product_name"],
            "quantity_returned": line.quantity_returned,
            "unit_price": net,
            "line_return_amount": line_amount,
        })

    if total_return <= 0:
        raise HTTPException(status_code=400, detail="Return must include at least one item")

    # Goods leave the shop, so there must be enough stock on hand
    stock_now = {}
    for pid, needed in stock_needed.items():
        prod = supabase.table("products").select("name, stock_quantity").eq("id", pid).single().execute()
        if prod.data:
            have = prod.data["stock_quantity"] or 0
            if have < needed:
                raise HTTPException(
                    status_code=400,
                    detail=f"Not enough stock of '{prod.data['name']}' to return {needed} (only {have} on hand)",
                )
            stock_now[pid] = have

    # Rule B: reduce everything we owe this supplier first (across all bills);
    # only the excess is cash refunded by the supplier.
    payable = _supplier_payable(supabase, purchase.get("supplier_id"))
    credit_applied = round(min(payable, total_return), 2)
    cash_refunded = round(total_return - credit_applied, 2)

    ret = supabase.table("purchase_returns").insert({
        "store_id": store_id,
        "purchase_id": str(body.purchase_id),
        "supplier_id": purchase.get("supplier_id"),
        "return_number": next_doc_number(store_id, "purchase_return"),
        "return_date": str(body.return_date),
        "reason": body.reason,
        "total_return_amount": total_return,
        "credit_applied_amount": credit_applied,
        "cash_refunded_amount": cash_refunded,
        "remarks": f"({', '.join(i['product_name'] for i in return_items)})",
        "created_by": user.id,
        "created_by_name": creator_name(user),
    }).execute().data[0]

    for item in return_items:
        item["return_id"] = ret["id"]
    supabase.table("purchase_return_items").insert(return_items).execute()

    # Reduce what we owe the supplier
    if purchase.get("supplier_id") and credit_applied > 0:
        sup = supabase.table("suppliers").select("balance").eq("id", purchase["supplier_id"]).single().execute()
        if sup.data:
            new_balance = round((sup.data["balance"] or 0) - credit_applied, 2)
            supabase.table("suppliers").update({"balance": new_balance}).eq("id", purchase["supplier_id"]).execute()

    # Take the goods out of stock
    for pid, needed in stock_needed.items():
        if pid in stock_now:
            supabase.table("products").update({"stock_quantity": stock_now[pid] - needed}).eq("id", pid).execute()

    return {**ret, "items": return_items}


@router.delete("/{return_id}")
async def delete_purchase_return(return_id: str, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase_admin()

    ret = supabase.table("purchase_returns").select("*") \
        .eq("id", return_id).eq("store_id", store_id).single().execute()
    if not ret.data:
        raise HTTPException(status_code=404, detail="Purchase return not found")
    r = ret.data

    items = supabase.table("purchase_return_items").select("*").eq("return_id", return_id).execute().data or []

    # Put the goods back into stock
    for item in items:
        if item.get("product_id"):
            prod = supabase.table("products").select("stock_quantity").eq("id", item["product_id"]).single().execute()
            if prod.data:
                supabase.table("products").update(
                    {"stock_quantity": (prod.data["stock_quantity"] or 0) + item["quantity_returned"]}
                ).eq("id", item["product_id"]).execute()

    # Restore what we owed the supplier
    if r.get("supplier_id") and (r["credit_applied_amount"] or 0) > 0:
        sup = supabase.table("suppliers").select("balance").eq("id", r["supplier_id"]).single().execute()
        if sup.data:
            new_balance = round((sup.data["balance"] or 0) + r["credit_applied_amount"], 2)
            supabase.table("suppliers").update({"balance": new_balance}).eq("id", r["supplier_id"]).execute()

    supabase.table("purchase_return_items").delete().eq("return_id", return_id).execute()
    supabase.table("purchase_returns").delete().eq("id", return_id).execute()

    return {"message": "Purchase return deleted"}
