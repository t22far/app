# Leave Tracker

Flask web app for tracking staff holiday leave balances via QuickBooks Payroll.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
flask run
```

Open http://127.0.0.1:5000 - database is created and seeded automatically.

## Formula

Available = Allocated - Taken - Future Approved + Manual Adjustments

Green = >40 hrs | Amber = 10-40 hrs | Red = <10 hrs
