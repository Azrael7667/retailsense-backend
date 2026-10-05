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
├── schemas/             # Pydantic models
├── services/            # Business logic
├── ml/                  # Machine learning
│   ├── training/        # Model training scripts
│   └── utils/           # Feature engineering
├── models/              # DB helpers
└── tests/               # Unit tests

## Deployment

Deployed on **Render**. Every push to `main` triggers automatic deployment.

## Machine learning models

The six AI helpers are trained offline for each shop. The API does not run the models. It serves the results they publish, so predictions are precomputed (see the report, Section 5.7).

### Where everything is

| Item | Location |
|---|---|
| Export of one shop (pseudonymised) | `ml/export_shop.py` |
| Training and evaluation of all six helpers | `ml/train_shop.py`, `ml/train_cells.py` |
| Notebook version of the training code | `ml/notebooks/retailsense_models.ipynb` |
| Weekly retraining script | `ml/retrain.sh` |
| Shops to retrain | `ml/shops.txt` |
| ML package versions | `ml/requirements-ml.txt` |
| Latest trained models and results | `ml/results/<store_id>/` |

### Trained model files (latest retrain, 4 October 2026)

| Helper | Model | Saved model | Served results |
|---|---|---|---|
| Business Direction | Prophet with Optuna | `sales_trend_model.json` | `sales_trend_results.json` |
| Cash Flow Forecast | Prophet | `cash_flow_revenue_model.json` | `cash_flow_results.json` |
| Restock Advisor | LightGBM | `restock_lightgbm.txt` | `restock_results.json` |
| Customers Leaving | LightGBM with SHAP | `churn_lightgbm.txt` | `churn_results.json` |
| Udharo Advisor | Logistic regression | `credit_logistic_regression.json` | `credit_results.json` |
| Unusual Transactions | Isolation Forest | `anomaly_isolation_forest.joblib` | `anomaly_results.json` |

`model_report*.json` and `model_comparison_weekly_revenue.json` hold the evaluation figures reported in the final report, and `retrain_status.json` records which helpers succeeded. The `.joblib` file is a scikit-learn model saved with joblib (a pickle format), so it must be loaded with the same scikit-learn version listed in `ml/requirements-ml.txt`.

### Data

The evaluation used the real records of one shop, Bijeta Auto Parts. The raw records are not included. The export step replaces customer names with labels such as "Customer 001" and removes phone, email, address and PAN. Purchase quantities, stock levels and dates were adjusted, and every change is listed in the final report (Section 8.6).

### Retraining

Create the ML environment once:

    python3 -m venv ~/retailsense-ml/venv
    ~/retailsense-ml/venv/bin/pip install -r ml/requirements-ml.txt

Retrain every shop in `ml/shops.txt`, or only the ones you name:

    ml/retrain.sh
    ml/retrain.sh <store_id>

This needs a `.env` with the Supabase settings (see `.env.example`), because step 1 exports the shop from the database. Results are written to `ml/results/<store_id>/`. A helper that fails does not overwrite its previous results.

## Running the API locally

    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env      # then fill in your own Supabase values
    uvicorn main:app --port 8000

Interactive API documentation is then at `http://localhost:8000/docs`. The Docker build (`Dockerfile`) installs Tesseract with English and Nepali data for the Scan Bill feature.
