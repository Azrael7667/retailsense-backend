from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from config import get_settings

load_dotenv()
settings = get_settings()

app = FastAPI(
    title="RetailSense Nepal API",
    description="Business intelligence backend for Nepali retail stores",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from routers import (
    admin,
    classification,
    auth, products, categories, customers,
    suppliers, invoices, purchases, expenses, payments, payments_out, sales_returns,
    purchase_returns,
    khata, reports, dashboard,
    pending_documents, reminders, platform_admin,
    ai_cash_flow, ai_inventory, ai_churn,
    ai_sales_trend, ai_anomaly, ai_credit,
    ocr
)

# Core routers
app.include_router(classification.router, prefix="/api/admin", tags=["Admin"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(auth.router,       prefix="/api/auth",       tags=["Auth"])
app.include_router(products.router,   prefix="/api/products",   tags=["Products"])
app.include_router(categories.router, prefix="/api/categories", tags=["Categories"])
app.include_router(customers.router,  prefix="/api/customers",  tags=["Customers"])
app.include_router(suppliers.router,  prefix="/api/suppliers",  tags=["Suppliers"])
app.include_router(invoices.router,   prefix="/api/invoices",   tags=["Invoices"])
app.include_router(purchases.router,  prefix="/api/purchases",  tags=["Purchases"])
app.include_router(expenses.router,   prefix="/api/expenses",   tags=["Expenses"])
app.include_router(payments.router,    prefix="/api/payments",   tags=["Payments"])
app.include_router(payments_out.router, prefix="/api/payments-out", tags=["Payments Out"])
app.include_router(sales_returns.router, prefix="/api/sales-returns", tags=["Sales Returns"])
app.include_router(purchase_returns.router, prefix="/api/purchase-returns", tags=["Purchase Returns"])
app.include_router(khata.router,      prefix="/api/khata",      tags=["Khata / Udharo"])
app.include_router(reports.router,    prefix="/api/reports",    tags=["Reports"])
app.include_router(dashboard.router,  prefix="/api/dashboard",  tags=["Dashboard"])
app.include_router(pending_documents.router, prefix="/api/pending-documents", tags=["Pending Documents"])
app.include_router(ocr.router, prefix="/api/ocr", tags=["OCR"])
app.include_router(reminders.router,  prefix="/api/reminders",  tags=["Reminders"])
app.include_router(platform_admin.router, prefix="/api/platform-admin", tags=["Platform Admin"])

# AI routers — each gets its own unique prefix
app.include_router(ai_cash_flow.router,   prefix="/api/ai/cashflow",   tags=["AI - Cash Flow"])
app.include_router(ai_inventory.router,   prefix="/api/ai/inventory",  tags=["AI - Inventory"])
app.include_router(ai_churn.router,       prefix="/api/ai/churn",      tags=["AI - Churn"])
app.include_router(ai_sales_trend.router, prefix="/api/ai/trend",      tags=["AI - Sales Trend"])
app.include_router(ai_anomaly.router,     prefix="/api/ai/anomaly",    tags=["AI - Anomaly"])
app.include_router(ai_credit.router,      prefix="/api/ai/credit",     tags=["AI - Credit"])

@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "app": "RetailSense Nepal API", "version": "1.0.0"}

@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy", "env": settings.app_env}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8081, reload=True)
