# Staff Attendance System

## User management API

FastAPI + MySQL (`api/`, `database/`). Roles: `admin` (everything), `staff` (own data only), `system` (the kiosk machine: only `kiosk_scan`, no attendance of its own; see `ROLE_PERMISSIONS` in `api/deps.py`).

```bash
cp .env.example .env            # set real secrets (JWT_SECRET_KEY, MYSQL_*), SMTP_* for production
docker compose up -d mysql
uv run alembic upgrade head
uv run python -m api.cli create-admin --email admin@example.com --name "Admin"
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
uv run pytest tests/test_user_api.py
```

Endpoints under `/api/v1` (Swagger at `/docs` when `APP_ENV=development`):

| Method | Path | Access |
|---|---|---|
| POST | `/auth/login` (username or email) | public |
| POST | `/auth/signup` | public (staff user name, employee id, department, email, password; `SIGNUP_ENABLED`) |
| POST | `/auth/forgot-password`, `/auth/reset-password` | public (emailed single-use token) |
| GET | `/departments` | public (sign-up dropdown) |
| POST / PUT / DELETE | `/departments`, `/departments/{id}` | admin (`{code, name}`; delete blocked while users are assigned) |
| GET / POST | `/face/enrollment`, `/face/enroll` | staff, admin (checks MySQL + Qdrant for an existing enrollment/face, then ingests) |
| POST / GET | `/users` | admin (create / list) |
| GET | `/users/me` | any user |
| GET | `/users/{id}` | admin or self |
| POST | `/users/{id}/activate`, `/users/{id}/deactivate` | admin |
| DELETE | `/users/{id}` | admin |
| PUT | `/users/{id}/password` | self (needs `current_password`) or admin |

`APP_ENV=production` refuses to start with weak/placeholder secrets, console email, or wildcard CORS, and disables `/docs`.

**Web page:** `GET /` serves the login / sign-up / reset-password page. After sign-up (or login without enrolled face) a consent dialog precedes the webcam capture, which posts to `/face/enroll`. Set `FRONTEND_URL` to the API's public URL so reset emails link to `/reset-password?token=...`.

**Inference service** is mounted at `/api/v1/inference/*` (health, `/ws/stream/{id}?token=<jwt>`, recognize, testing...). Admins may call everything; other users only the live stream and health.

Seed departments (idempotent, matched by code): `uv run python -m api.cli seed-departments 247=AIML "002=CSE(CY)"`

**Admin console:** logging in as an admin on `/` redirects to `/admin` (Overview with counters and recent check-ins/outs, Users, Departments, Attendance daily summaries and raw events). It is only a shell; every call is authorized by the API (`GET /api/v1/admin/summary` is admin-only). The token lives in `sessionStorage`, so closing the tab logs out.

**Staff portal:** logging in as a staff user on `/` (once face recognition is set up) redirects to `/portal`: today's status (checked in/out, first check-in, last check-out, hours), and a report for Today / This week / Last week / This month / Last month / a custom range, with days present, total and average hours, average check-in time, missing check-outs, and a per-day table. It reads only `GET /attendance/me/today` and `GET /attendance/me`, which return the caller's own data and nothing else. Times are shown in `APP_TIMEZONE`; "today" and the week/month boundaries come from the server's date (weeks start on Monday). Hours are first check-in to last check-out; a day without a check-out is flagged "Missing check-out".

**Kiosk (`system` role):** create a user with role `system` (admin console → Users → Add user). Logging in as that user on `/` redirects to `/kiosk`, a camera-only page. Once one face has stayed in frame for 3 s it calls `POST /api/v1/kiosk/identify`, which recognizes the face but **records nothing**, and a confirm window shows the person's name/department with **Check in** / **Check out** buttons (only the valid one is enabled) and a close **X**. Pressing a button calls `POST /api/v1/kiosk/record` with the `match_token` from `/identify` (a signed token valid for 2 minutes, bound to that kiosk account) and the chosen `action`, then the page shows the check-in (green) or check-out (red) animation and a greeting. Pressing **X** (or the 20 s timeout) closes the window and nothing is logged, so a wrongly recognized person is never recorded. Other outcomes get their own message: `unknown` (face not registered or account inactive: "Oops! ... contact <admin name>"), `already_checked_in` / `not_checked_in`, `expired`, `no_face` (retried 3 times) and service unavailable. `POST /kiosk/detect` only counts faces. There is no liveness check, so a photo of a person can be recognized. The kiosk needs the inference models and Qdrant available.

### Attendance

Check-in / check-out events are stored append-only (`attendance_events`); the user-facing view is the day's **first check-in** and **last check-out**, admins see every event.

Face gating: the inference service writes `face_verified:<user_id>` to Redis (TTL `FACE_VERIFY_TTL`) whenever it recognizes a face. The face identity registered in Qdrant (`emp_id`) **must be the user's id**. `GET /attendance/me/status` returns `can_check_in` / `can_check_out` for the UI buttons, and the POST endpoints re-check the flag server-side and consume it (one recognition = one action).

| Method | Path | Access |
|---|---|---|
| GET | `/attendance/me/status` | any user (button state, today's first-in/last-out) |
| GET | `/attendance/me/today` | any user (own today: first-in/last-out, currently checked in; no Redis needed) |
| POST | `/attendance/check-in`, `/attendance/check-out` | staff, admin (needs verified face) |
| GET | `/attendance/me?date_from&date_to` | any user (own days: first in / last out) |
| GET | `/attendance/records?user_id&date_from&date_to` | admin (daily summaries with counts, all users) |
| GET | `/attendance/events?user_id&date_from&date_to` | admin (every raw event) |

Sequence is per work day (in `APP_TIMEZONE`): check-in is valid when not already checked in; check-out only after a check-in. A forgotten check-out leaves that day without a last check-out.
