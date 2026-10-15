# Leave Tracker

A simple holiday booking system for a small team. Staff sign in on their phone,
see exactly what holiday they have left, what they've taken and what's booked,
and request time off. Managers approve or reject, see everyone on one calendar,
and set each person's allowance per leave year.

Balances are in **hours**. The leave year runs 1 April – 31 March by default
(changeable in Settings) and every year starts fresh.

```
Remaining = allowance + carried over + adjustments − taken − booked ahead
```

## Running it

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
flask run
```

Open http://127.0.0.1:5000. The first visit asks you to create the admin account.

## Hosting on Railway

1. Deploy the repo. The `Procfile` runs it with gunicorn.
2. **Add a Volume** to the service mounted at `/data` — without this the
   database is wiped on every deploy.
3. Optional: set a `SECRET_KEY` variable. If you don't, one is generated and
   stored on the volume.

## First-time setup for a manager

1. Create your admin account on the setup screen.
2. **Staff → Add staff member** for each person. Set their usual hours per
   working day and this year's holiday allowance in hours. Give them a temporary
   password; they're asked to change it when they first sign in.
3. On each person's page, use **Record leave** to enter holidays already taken
   or agreed since the start of the leave year. They go in as approved.
4. From then on staff request time off themselves and it lands in **Approvals**.

## Roles

- **Staff** see their own balance, request time off, withdraw pending requests,
  and see the team calendar.
- **Admin** can do everything: approve/reject/cancel, add and deactivate staff,
  set allowances and adjustments, record leave on someone's behalf, and change
  settings.
