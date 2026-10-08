# Staff Attendance System with Face Recognition

A robust attendance management system that uses facial recognition to automatically track staff attendance in real-time. Built with Python, Django, FastAPI, and deep learning.

## 🌟 Features

- Real-time face detection and recognition
- Automated attendance marking
- Web-based admin interface
- High accuracy face recognition using deep learning
- Background task processing for face embeddings
- Employee Profile management
- Employee management (add/delete/update)
- Attendance reports and analytics

## 🏗️ System Architecture

### Components:
1. **Web Interface (Django)**
   - Employee management
   - Attendance records
   - Report generation
   - Employee profile management

2. **Face Recognition Backend (FastAPI)**
   - Real-time video processing
   - Face detection using MTCNN
   - Face recognition using ResNet
   - Embedding generation and matching

3. **Database**
   - SQLite for storing employee and attendance records
   - JSON file for storing face embeddings

## 🛠️ Technical Stack

- **Frontend**: HTML, CSS, JavaScript
- **Backend**: Django, FastAPI
- **Face Recognition**: 
  - MTCNN (Multi-task Cascaded Convolutional Networks)
  - ResNet (Deep Residual Learning)
  - FaceNet PyTorch
- **Database**: SQLite3
- **Camera Interface**: OpenCV with NVIDIA Jetson support
- **Background Tasks**: Asynchronous processing

## 📝 Process Flow

1. **Employee Registration**
   - Admin adds new employee with details
   - Upload multiple face photos
   - System processes photos in background
   - Generates and stores face embeddings

2. **Face Detection**
   - Camera captures real-time video feed
   - MTCNN detects faces in frames
   - Largest face is selected for recognition
   - Face alignment and preprocessing

3. **Face Recognition**
   - Convert detected face to embeddings
   - Compare with stored employee embeddings
   - Match found -> Mark attendance
   - Rate limiting prevents duplicate entries

4. **Attendance Processing**
   - Automatic attendance marking
   - Timestamp recording
   - Status tracking (IN/OUT)
   - Attendance report generation

## 🔧 Installation

1. Clone the repository:
```bash
git clone https://github.com/drackko/staff_attendance_system.git
```

2. Create virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Database setup:
```bash
python manage.py makemigrations
python manage.py migrate
```

5. Create admin user:
```bash
python manage.py createsuperuser
```

## 🚀 Running the System

1. Start Django server:
```bash
python manage.py runserver
```

2. Start Face Recognition backend:
```bash
python backend/face_detector.py
```

## 📂 Project Structure

```
Staff_attendance_system/
|
├── backend/
│   ├── face_detector.py
│   └── face_embeddings.json
├── Home/
│   ├── views.py
│   ├── models.py
│   └── urls.py
├── media/
│   └── profile_pics/ # employees profile pictures will be stored here
└── requirements.txt
```

## ⚙️ Configuration

Key settings can be modified in:
- `settings.py`: Django configuration
- `face_detect.py`: Recognition parameters
- `face_embeddings.json`: Embedding storage

## 🔐 Security Features

- Authentication required for admin access
- Secure embedding storage
- Rate limiting for attendance marking
- Transaction-safe database operations

## 📈 Performance Optimization

- Batch processing for embeddings
- Async video processing
- Background task handling
- Efficient embedding storage
- Camera pipeline optimization

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

**Kiosk (`system` role):** create a user with role `system` (admin console → Users → Add user). Logging in as that user on `/` redirects to `/kiosk`, a camera-only page. Once one face has stayed in frame for 3 s it calls `POST /api/v1/kiosk/identify`, which recognizes the face but **records nothing**, and a confirm window shows the person's name/department with **Check in** / **Check out** buttons (only the valid one is enabled) and a close **X**. Pressing a button calls `POST /api/v1/kiosk/record` with the `match_token` from `/identify` (a signed token valid for 2 minutes, bound to that kiosk account) and the chosen `action`, then the page shows the check-in (green) or check-out (red) animation and a greeting. Pressing **X** (or the 20 s timeout) closes the window and nothing is logged, so a wrongly recognized person is never recorded. Other outcomes get their own message: `unknown` (face not registered or account inactive: "Oops! ... contact <admin name>"), `already_checked_in` / `not_checked_in`, `expired`, `no_face` (retried 3 times) and service unavailable. `POST /kiosk/detect` only counts faces. There is no liveness check, so a photo of a person can be recognized. The kiosk needs the inference models and Qdrant available.

### Attendance

Check-in / check-out events are stored append-only (`attendance_events`); the user-facing view is the day's **first check-in** and **last check-out**, admins see every event.

Face gating: the inference service writes `face_verified:<user_id>` to Redis (TTL `FACE_VERIFY_TTL`) whenever it recognizes a face. The face identity registered in Qdrant (`emp_id`) **must be the user's id**. `GET /attendance/me/status` returns `can_check_in` / `can_check_out` for the UI buttons, and the POST endpoints re-check the flag server-side and consume it (one recognition = one action).

| Method | Path | Access |
|---|---|---|
| GET | `/attendance/me/status` | any user (button state, today's first-in/last-out) |
| POST | `/attendance/check-in`, `/attendance/check-out` | staff, admin (needs verified face) |
| GET | `/attendance/me?date_from&date_to` | any user (own days: first in / last out) |
| GET | `/attendance/records?user_id&date_from&date_to` | admin (daily summaries with counts, all users) |
| GET | `/attendance/events?user_id&date_from&date_to` | admin (every raw event) |

Sequence is per work day (in `APP_TIMEZONE`): check-in is valid when not already checked in; check-out only after a check-in. A forgotten check-out leaves that day without a last check-out.
