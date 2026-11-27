<div align="center">

# 🎬 Kurox Movie Pro API

**A fast, serverless FastAPI wrapper for movie & TV metadata, search and stream/download link resolving — with API-key auth, GitHub login and daily-quota plans.**

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-optional-47A248?logo=mongodb&logoColor=white)
![Vercel](https://img.shields.io/badge/Deploy-Vercel-000000?logo=vercel&logoColor=white)

</div>

---

## ✨ Features

- 🔍 **Search, trending & home feed** – movies and TV series with posters and metadata
- 📄 **Detail endpoint** – seasons, episodes, subtitles and available qualities
- ▶️ **Stream resolver** – in-browser MP4 streaming through a built-in proxy
- ⬇️ **Download resolver** – permanent download URLs with 1080p / 720p / 480p options
- 🔐 **API-key system** – `x-api-key` header, `?api_key=` query or `Bearer` token
- 👤 **GitHub sign-in** – OAuth login (or direct username sign-in) with key regeneration
- 📊 **Plans & daily quotas** – Starter / Pro / Unlimited, enforced per API key
- 🛠️ **Admin endpoints** – list users and change plans
- 🌐 **Proxy support** – HTTP / SOCKS5 for datacenter hosts (Vercel, Heroku, AWS)
- 🛡️ **SSRF protection** – download/stream proxy only fetches from allow-listed CDN hosts
- 💾 **MongoDB storage** with automatic local JSON fallback

## 🧱 Tech Stack

`Python` · `FastAPI` · `httpx` · `Uvicorn` · `PyMongo` · `python-dotenv`

## 🚀 Getting Started

```bash
# 1. Clone
git clone https://github.com/NimeshMihiranga-Neno/moviepro-api.git
cd moviepro-api

# 2. Install
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# then edit .env

# 4. Run
uvicorn api.index:app --host 0.0.0.0 --port 3000 --reload
```

API runs at `http://localhost:3000`.

## ⚙️ Environment Variables

| Variable | Required | Description |
|---|---|---|
| `PORT` | No | Server port (default `3000`) |
| `MONGODB_URI` | No | MongoDB connection string. Falls back to a local JSON file if empty |
| `MONGODB_DB_NAME` | No | Database name (default `moviebox_db`) |
| `ADMIN_EMAIL` | Yes | Email that gets admin access |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | No | Enables GitHub OAuth login |
| `PROXY_URL` | No | `http://user:pass@host:port` or `socks5://...` |
| `PUBLIC_URL` | No | Public base URL (auto-detected if empty) |
| `ALLOWED_DL_HOSTS` | No | Comma-separated CDN hosts the proxy may fetch from |

## 📡 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api` | Service info & endpoint list |
| GET | `/api/country` | Available regions |
| GET | `/api/home` | Home feed |
| GET | `/api/trending?page=1` | Trending titles |
| GET | `/api/search?q=Avatar` | Search movies / series |
| GET | `/api/detail?path=<detail_path>` | Title details, seasons & episodes |
| GET | `/api/stream/{subject_id}` | Stream a title |
| GET | `/api/download/{subject_id}` | Download a title |
| GET | `/api/plans` | Plans catalog |
| GET | `/api/auth/github/login` | Start GitHub OAuth |
| GET | `/api/auth/me` | Current user & usage |
| POST | `/api/auth/regenerate-key` | Regenerate API key |
| GET | `/api/admin/users` | *(Admin)* List users |
| PUT | `/api/admin/users/{username}/plan` | *(Admin)* Change a user's plan |

### Example

```bash
curl "http://localhost:3000/api/search?q=Avatar" \
  -H "x-api-key: YOUR_API_KEY"
```

### Error codes

| Status | Code | Meaning |
|---|---|---|
| 401 | `API_KEY_REQUIRED` | No API key supplied |
| 403 | `PLAN_REQUIRED` | Key has no active plan |
| 429 | `DAILY_LIMIT_EXCEEDED` | Daily quota used up (resets 00:00 UTC) |

## 💳 Plans

| Plan | Daily requests | Price |
|---|---|---|
| Starter | 100 | LKR 49 / month |
| Pro Developer | 1,000 | LKR 149 / month |
| Unlimited VIP | Unlimited | LKR 249 / month |

## ☁️ Deployment

**Vercel** – `vercel.json` is included (Singapore region, 60s max duration). Just import the repo and add your environment variables.

**Heroku / VPS** – uses the included `Procfile`:

```
web: uvicorn api.index:app --host 0.0.0.0 --port $PORT
```

> 💡 Datacenter IPs are sometimes blocked upstream. If requests fail on Vercel/Heroku/AWS, set `PROXY_URL`.

## ⚠️ Disclaimer

This project is **unofficial** and is provided for **educational purposes only**. It is not affiliated with, endorsed by, or connected to any streaming service. It does not host, store or distribute any media files — it only relays public responses from third-party services. You are solely responsible for how you use it and for complying with the laws and terms of service that apply to you.

## 👨‍💻 Author

**Nimesh Mihiranga (Neno)** — Founder of [Black Cat Studio](https://github.com/NimeshMihiranga-Neno) · Owner of **Kurox Dev**

- GitHub: [@NimeshMihiranga-Neno](https://github.com/NimeshMihiranga-Neno)

## 📜 License

Released under the [MIT License](LICENSE).

---

<div align="center">⭐ If this project helped you, give it a star!</div>
