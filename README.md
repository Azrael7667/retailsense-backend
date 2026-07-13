# RetailSense Nepal — Backend API

FastAPI backend with ML models for RetailSense Nepal.

![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![Supabase](https://img.shields.io/badge/Database-Supabase-green)

## Overview

REST API backend providing ML-powered predictions and business intelligence for RetailSense Nepal. Built with FastAPI and Python, connected to Supabase PostgreSQL.

## API Endpoints

| Module | Endpoint | Description |
|---|---|---|
| Health | `GET /` | API status |
| Auth | `POST /api/auth/login` | User login |
| Auth | `POST /api/auth/register` | Store registration |
| Products | `GET /api/products/` | List products |
| Customers | `GET /api/customers/` | List customers |
| Invoices | `GET /api/invoices/` | List invoices |
| Reports | `GET /api/reports/profit-loss` | P&L report |
| AI | `GET /api/ai/cash-flow-forecast` | Cash flow prediction |
| AI | `GET /api/ai/inventory-demand` | Inventory demand |
| AI | `GET /api/ai/customer-churn` | Churn prediction |
| AI | `GET /api/ai/sales-trend` | Sales trend |
| AI | `GET /api/ai/anomaly-detection` | Anomaly detection |
| AI | `GET /api/ai/credit-scoring/{id}` | Credit score |

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | FastAPI 0.111 |
| Language | Python 3.12 |
| Database | Supabase (PostgreSQL) |
| ML — Forecasting | Prophet |
| ML — Classification | XGBoost, LightGBM |
| ML — Anomaly | Isolation Forest |
| ML — Explainability | SHAP |
| ML — Tuning | Optuna |
| Deployment | Render |

## Getting Started

### Prerequisites
- Python 3.12+
- pip

### Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/retailsense-backend.git
cd retailsense-backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment variables
cp .env.example .env
nano .env

# Start development server
uvicorn main:app --port 8081 --reload
```

### Environment Variables

```env
SUPABASE_URL=https://yourproject.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
APP_ENV=development
SECRET_KEY=your-secret-key
ALLOWED_ORIGINS=http://localhost:5173
```

### API Documentation

Once running, visit:
- Swagger UI: `http://localhost:8081/docs`
- ReDoc: `http://localhost:8081/redoc`

## Project Structure
retailsense-backend/
├── main.py              # FastAPI app entry point
├── config.py            # Settings from environment
├── database.py          # Supabase client
├── routers/             # API route handlers
│   ├── auth.py
│   ├── products.py
│   ├── customers.py
│   ├── invoices.py
│   ├── purchases.py
│   ├── expenses.py
│   ├── khata.py
│   ├── reports.py
│   ├── dashboard.py
│   └── ai_models.py
├── schemas/             # Pydantic models
├── services/            # Business logic
├── ml/                  # Machine learning
│   ├── training/        # Model training scripts
│   └── utils/           # Feature engineering
├── models/              # DB helpers
└── tests/               # Unit tests

## Deployment

Deployed on **Render**. Every push to `main` triggers automatic deployment.
