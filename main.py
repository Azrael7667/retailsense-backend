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

# ── Import and register all routers ──────────────────────────
from routers import (
    auth, products, categories, customers,
    suppliers, invoices, purchases, expenses,
    khata, reports, ai_models, dashboard
)

app.include_router(auth.router,       prefix="/api/auth",       tags=["Auth"])
app.include_router(products.router,   prefix="/api/products",   tags=["Products"])
app.include_router(categories.router, prefix="/api/categories", tags=["Categories"])
app.include_router(customers.router,  prefix="/api/customers",  tags=["Customers"])
app.include_router(suppliers.router,  prefix="/api/suppliers",  tags=["Suppliers"])
app.include_router(invoices.router,   prefix="/api/invoices",   tags=["Invoices"])
app.include_router(purchases.router,  prefix="/api/purchases",  tags=["Purchases"])
app.include_router(expenses.router,   prefix="/api/expenses",   tags=["Expenses"])
app.include_router(khata.router,      prefix="/api/khata",      tags=["Khata / Udharo"])
app.include_router(reports.router,    prefix="/api/reports",    tags=["Reports"])
app.include_router(ai_models.router,  prefix="/api/ai",         tags=["AI Models"])
app.include_router(dashboard.router,  prefix="/api/dashboard",  tags=["Dashboard"])

@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "app": "RetailSense Nepal API", "version": "1.0.0"}

@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy", "env": settings.app_env}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8081, reload=True)
