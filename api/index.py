try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import os
import re
import json
import secrets
from urllib.parse import urlparse, quote

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, RedirectResponse, HTMLResponse

try:
    from api.db import (
        save_or_update_user,
        find_user_by_identifier,
        get_user_by_api_key,
        list_all_users,
        update_user_plan,
        regenerate_user_key,
        check_and_consume_quota,
        ADMIN_EMAIL,
        get_mongo_collection,
    )
except (ImportError, ModuleNotFoundError):
    from db import (
        save_or_update_user,
        find_user_by_identifier,
        get_user_by_api_key,
        list_all_users,
        update_user_plan,
        regenerate_user_key,
        check_and_consume_quota,
        ADMIN_EMAIL,
        get_mongo_collection,
    )

app = FastAPI(title="Kurox Movie Pro API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SERVICE_NAME = "MovieBox API"
CREATOR = {"owner": "Nimesh Mihiranga", "brand": "Kurox Dev", "role": "Owner & Developer"}

API_BASE = "https://h5-api.aoneroom.com/wefeed-h5api-bff"
HOST = "moviebox.ph"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

# CDN hosts the download proxy may fetch from (SSRF protection)
ALLOWED_DL_HOSTS = tuple(
    h.strip().lower()
    for h in os.environ.get("ALLOWED_DL_HOSTS", "hakunaymatata.com").split(",")
    if h.strip()
)

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Referer": f"https://{HOST}/",
    "Origin": f"https://{HOST}",
    "X-Client-Info": '{"timezone":"Asia/Dhaka"}',
    "X-Request-Lang": "en",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

PLAYER_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "X-Client-Info": '{"timezone":"Asia/Dhaka"}',
    "X-Source": "",
}

_token = None


# ----------------------------------------------------------------- helpers
class UpstreamError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message


@app.exception_handler(UpstreamError)
async def upstream_error_handler(request: Request, exc: UpstreamError):
    return fail(exc.code, exc.message)


def ok(action: str, **body):
    return {"status": True, "service": SERVICE_NAME, "creator": CREATOR, "action": action, **body}


def fail(code: int, error: str, **extra):
    return JSONResponse(
        status_code=code,
        content={"status": False, "service": SERVICE_NAME, "creator": CREATOR, "error": error, **extra},
    )


def to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def fmt_size(b):
    n = to_int(b)
    return f"{n / 1048576:.1f} MB" if n else None


def clean_slug(p: str) -> str:
    p = (p or "").strip()
    p = re.sub(r"^https?://[^/]+/", "", p)
    p = re.sub(r"^/?detail/", "", p)
    return p.strip("/")


def type_name(t) -> str:
    return "Movie" if t == 1 else "TV Series" if t == 2 else "Unknown"


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")


def safe_filename(name: str) -> str:
    name = re.sub(r"[^\w.\- ()\[\]]", "", name or "").strip() or "video.mp4"
    return name if "." in name else name + ".mp4"


def public_base(request: Request) -> str:
    env = os.environ.get("PUBLIC_URL")
    if env:
        return env.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


async def get_token(force: bool = False) -> str:
    """Guest JWT from the x-user header (cached per warm instance)."""
    global _token
    if _token and not force:
        return _token
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=25) as c:
            r = await c.get(f"{API_BASE}/home?host={HOST}", headers=DEFAULT_HEADERS)
        xu = r.headers.get("x-user")
        if xu:
            try:
                _token = json.loads(xu).get("token")
            except Exception:
                pass
        if not _token:
            m = re.search(r"token=([^;]+)", r.headers.get("set-cookie", ""))
            if m:
                _token = m.group(1)
    except Exception:
        pass
    return _token or ""


async def upstream(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    global _token
    token = await get_token()
    headers = {**DEFAULT_HEADERS}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=25) as c:
            if method == "POST":
                r = await c.post(url, headers=headers, json=payload)
            else:
                r = await c.get(url, headers=headers)
    except Exception as e:
        raise UpstreamError(502, f"Request failed: {e}")

    xu = r.headers.get("x-user")
    if xu:
        try:
            new_token = json.loads(xu).get("token")
            if new_token:
                _token = new_token
        except Exception:
            pass

    if r.status_code != 200:
        raise UpstreamError(502, f"Upstream API error: {r.status_code}")
    return r.json()


async def play_data(subject_id: str, slug: str, se: int, ep: int) -> dict:
    """Same flow as the real web player: get-domain, then play with a videoPlayPage Referer."""
    dom = await upstream(f"{API_BASE}/media-player/get-domain")
    domain = (dom.get("data") or "https://netfilm.world").rstrip("/")
    referer = (
        f"{domain}/spa/videoPlayPage/movies/{slug}"
        f"?id={subject_id}&type=/movie/detail&detailSe={se}&detailEp={ep}&lang=en"
    )
    url = (
        f"{domain}/wefeed-h5api-bff/subject/play"
        f"?subjectId={subject_id}&se={se}&ep={ep}&detailPath={slug}"
    )
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=25) as c:
            r = await c.get(url, headers={**PLAYER_HEADERS, "Referer": referer})
        return r.json().get("data") or {}
    except Exception:
        return {}


async def resolve_streams(subject_id: str, slug: str, se: int, ep: int) -> list[dict]:
    """Try (se, ep) first, then the 0/0 <-> 1/1 alternative (movies vs series numbering)."""
    alt = (1, 1) if (se, ep) == (0, 0) else (0, 0)
    for s, e in ((se, ep), alt):
        data = await play_data(subject_id, slug, s, e)
        raw = data.get("streams") or []
        if raw:
            return norm_streams(raw)
    return []


def norm_streams(raw: list[dict]) -> list[dict]:
    out = []
    for s in raw:
        if not s.get("url"):
            continue
        res = to_int(s.get("resolutions"))
        out.append(
            {
                "quality": f"{res}p" if res else "HD",
                "resolution": f"{res}p" if res else "HD",
                "resNum": res,
                "url": s["url"],
                "sizeBytes": to_int(s.get("size")),
                "fileSize": fmt_size(s.get("size")),
                "duration": s.get("duration"),
                "codec": s.get("codecName"),
            }
        )
    out.sort(key=lambda x: -x["resNum"])
    return out


def permanent_url(base: str, subject_id: str, slug: str, se: int, ep: int, quality: str) -> str:
    return (
        f"{base}/api/download/{quote(str(subject_id))}"
        f"?detail_path={quote(slug)}&se={se}&ep={ep}&quality={quote(quality)}"
    )


# ------------------------------------------------------------------ routes
@app.get("/")
@app.get("/api")
async def root():
    return ok(
        "root",
        message="Kurox Movie Pro API (Python) is running",
        version="1.0.0",
        endpoints={
            "country": "/api/country",
            "home": "/api/home",
            "trending": "/api/trending?page=1",
            "search": "/api/search?q=Avatar",
            "detail": "/api/detail?path=<detail_path>",
            "download": "/api/download/{subject_id}",
            "stream": "/api/stream/{subject_id}",
            "plans": "/api/plans",
        },
    )


async def enforce_api_access(request: Request):
    """
    Developer API enforcement:
    - If called from web app ('x-web-client: kurox-web'), allow free browsing.
    - If called externally without API key: return 401 API_KEY_REQUIRED.
    - If user has no active plan (e.g. newly signed up without approval/purchase): return 403 PLAN_REQUIRED.
    - If daily requests exceed plan quota: return 429 DAILY_LIMIT_EXCEEDED.
    """
    if request.headers.get("x-web-client") == "kurox-web":
        return None

    api_key = (
        request.headers.get("x-api-key")
        or request.query_params.get("api_key")
        or request.query_params.get("apiKey")
    )
    auth_header = request.headers.get("authorization", "")
    if not api_key and auth_header.startswith("Bearer "):
        api_key = auth_header.replace("Bearer ", "").strip()

    allowed, status_code, msg, user = check_and_consume_quota(api_key or "")
    if not allowed:
        parts = msg.split(":", 1)
        code_part = parts[0].strip()
        error_part = parts[1].strip() if len(parts) > 1 else msg
        return JSONResponse(
            status_code=status_code,
            content={
                "status": False,
                "service": SERVICE_NAME,
                "error": error_part,
                "code": code_part,
                "plan": user.get("plan") if user else "None",
                "upgradeUrl": "/api/plans"
            }
        )
    return None


@app.get("/api/country")
async def country(request: Request):
    gate_err = await enforce_api_access(request)
    if gate_err:
        return gate_err
    data = await upstream(f"{API_BASE}/country-code")
    return ok("country-code", detectedCountry=(data.get("data") or {}).get("countryCode") or "Unknown")


@app.get("/api/home")
async def home(request: Request):
    gate_err = await enforce_api_access(request)
    if gate_err:
        return gate_err
    data = await upstream(f"{API_BASE}/home?host={HOST}")
    return ok("home", data=data.get("data"))


@app.get("/api/trending")
async def trending(request: Request, page: int = 1, tabId: str = ""):
    gate_err = await enforce_api_access(request)
    if gate_err:
        return gate_err
    try:
        url = f"{API_BASE}/subject/trending?page={page}&perPage=18"
        if tabId:
            url += f"&tabId={tabId}"
        data = await upstream(url)
        if data.get("code") == 0 and data.get("data"):
            return ok("trending", page=page, data=data["data"])
    except UpstreamError:
        pass

    # Fallback: collect subjects from the home feed
    home_data = await upstream(f"{API_BASE}/home?host={HOST}")
    items, seen = [], set()
    for op in (home_data.get("data") or {}).get("operatingList") or []:
        if op.get("type") in ("SUBJECTS_MOVIE", "SUBJECTS_TV", "SUBJECTS_ANIMATION"):
            for s in op.get("subjects") or []:
                sid = s.get("subjectId")
                if sid and sid not in seen:
                    seen.add(sid)
                    items.append(s)
    return ok("trending", page=page, data={"items": items[:18]})


@app.get("/api/search")
async def search(request: Request, q: str = "", keyword: str = "", page: int = 1, perPage: int = 12, type: int = 0):
    gate_err = await enforce_api_access(request)
    if gate_err:
        return gate_err

    query = q or keyword
    if not query:
        return fail(400, "Query parameter 'q' is required (e.g., /api/search?q=Avatar)")

    data = await upstream(
        f"{API_BASE}/subject/search",
        method="POST",
        payload={"keyword": query, "page": page, "perPage": perPage, "subjectType": type},
    )
    inner = data.get("data") or {}
    raw = inner.get("items") or inner.get("list") or []
    items = []
    for it in raw:
        sid = it.get("subjectId")
        dur = to_int(it.get("duration"))
        items.append(
            {
                "id": sid,
                "title": it.get("title"),
                "type": type_name(it.get("subjectType")),
                "releaseDate": it.get("releaseDate"),
                "duration": f"{dur // 60} mins" if dur else None,
                "genres": (it.get("genre") or "").split(",") if it.get("genre") else [],
                "country": it.get("countryName"),
                "imdbRating": it.get("imdbRatingValue"),
                "cover": (it.get("cover") or {}).get("url"),
                "hasResource": it.get("hasResource"),
                "detailPath": it.get("detailPath") or f"{slugify(it.get('title'))}-{sid}",
            }
        )
    pager = inner.get("pager") or {}
    return ok(
        "search",
        query=query,
        page=page,
        count=len(items),
        total=pager.get("totalCount") or len(items),
        hasMore=pager.get("hasMore") or False,
        results=items,
    )


@app.get("/api/detail")
async def detail(request: Request, path: str = "", slug: str = "", id: str = "", se: int = 0, ep: int = 0):
    gate_err = await enforce_api_access(request)
    if gate_err:
        return gate_err
    slug_clean = clean_slug(path or slug or id)
    if not slug_clean:
        return fail(400, "Parameter 'path' is required (e.g., /api/detail?path=the-furious-cMR7rG3xUj7)")

    raw = await upstream(f"{API_BASE}/detail?detailPath={quote(slug_clean)}")
    data = raw.get("data") or {}
    subject = data.get("subject") or {}
    if raw.get("code") != 0 or not subject:
        return fail(404, raw.get("message") or "Movie or series not found")

    sid = subject.get("subjectId")
    is_series = subject.get("subjectType") == 2
    season = (se or 1) if is_series else 0
    episode = (ep or 1) if is_series else 0

    streams = await resolve_streams(sid, slug_clean, season, episode)
    base = public_base(request)

    downloads = [
        {
            "quality": s["quality"],
            "resolution": s["resolution"],
            "fileSize": s["fileSize"],
            "sizeBytes": s["sizeBytes"],
            "permanentDownloadUrl": permanent_url(base, sid, slug_clean, season, episode, s["quality"]),
        }
        for s in streams
    ]

    cast = [
        {
            "name": st.get("name"),
            "character": st.get("character"),
            "avatar": (st.get("avatar") or {}).get("url") if isinstance(st.get("avatar"), dict) else None,
        }
        for st in (data.get("stars") or [])
    ]

    dur = to_int(subject.get("duration"))
    return ok(
        "detail",
        data={
            "id": sid,
            "title": subject.get("title"),
            "type": type_name(subject.get("subjectType")),
            "description": subject.get("description"),
            "releaseDate": subject.get("releaseDate"),
            "duration": f"{dur // 60} mins" if dur else None,
            "genre": subject.get("genre"),
            "country": subject.get("countryName"),
            "imdbRating": subject.get("imdbRatingValue"),
            "cover": (subject.get("cover") or {}).get("url"),
            "subtitles": (subject.get("subtitles") or "").split(",") if subject.get("subtitles") else [],
            "stream": {"isTrailer": False, "qualities": downloads} if downloads else None,
            "downloads": downloads,
            "seasons": (data.get("resource") or {}).get("seasons") or [],
            "cast": cast,
        },
    )


async def proxy_download(request: Request, url: str, filename: str):
    """Stream a CDN file through this server as an attachment (Range / resume supported)."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not any(host == h or host.endswith("." + h) for h in ALLOWED_DL_HOSTS):
        return fail(400, "CDN host not allowed")

    headers = {
        "User-Agent": UA,
        "Referer": f"https://{HOST}/",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]

    client = httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=None))
    try:
        cdn = await client.send(client.build_request("GET", url, headers=headers), stream=True)
    except Exception as e:
        await client.aclose()
        return fail(502, "CDN fetch failed", details=str(e))

    if cdn.status_code >= 400:
        code = cdn.status_code
        await cdn.aclose()
        await client.aclose()
        return fail(502, f"CDN returned {code}", cdnStatus=code, cdnHost=host)

    async def body():
        try:
            async for chunk in cdn.aiter_raw(chunk_size=256 * 1024):
                yield chunk
        finally:
            await cdn.aclose()
            await client.aclose()

    out = {
        "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}",
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    for h in ("Content-Length", "Content-Range"):
        if cdn.headers.get(h):
            out[h] = cdn.headers[h]

    return StreamingResponse(
        body(),
        status_code=cdn.status_code,
        media_type=cdn.headers.get("content-type", "video/mp4"),
        headers=out,
    )


@app.get("/api/download/{subject_id}")
async def download(
    request: Request,
    subject_id: str,
    detail_path: str = "",
    path: str = "",
    se: int = 0,
    ep: int = 0,
    quality: str = "480p",
    name: str = "",
):
    """Direct download. Fetches a FRESH CDN link on every request, so it never expires."""
    slug = clean_slug(detail_path or path)
    streams = await resolve_streams(subject_id, slug, se, ep)
    if not streams:
        return fail(404, "No stream found for this title/episode.")

    want = re.sub(r"\D", "", quality)
    pick = next((s for s in streams if str(s["resNum"]) == want), streams[0])

    filename = safe_filename(name or f"{slug or subject_id}-S{se}E{ep}-{pick['quality']}.mp4")
    return await proxy_download(request, pick["url"], filename)


async def proxy_stream(request: Request, url: str):
    """Stream a CDN file through this server for in-browser HTML5 video playback."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not any(host == h or host.endswith("." + h) for h in ALLOWED_DL_HOSTS):
        return fail(400, "CDN host not allowed")

    headers = {
        "User-Agent": UA,
        "Referer": f"https://{HOST}/",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]

    client = httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=None))
    try:
        cdn = await client.send(client.build_request("GET", url, headers=headers), stream=True)
    except Exception as e:
        await client.aclose()
        return fail(502, "CDN fetch failed", details=str(e))

    if cdn.status_code >= 400:
        code = cdn.status_code
        await cdn.aclose()
        await client.aclose()
        return fail(502, f"CDN returned {code}", cdnStatus=code, cdnHost=host)

    async def body():
        try:
            async for chunk in cdn.aiter_raw(chunk_size=256 * 1024):
                yield chunk
        finally:
            await cdn.aclose()
            await client.aclose()

    out = {
        "Content-Disposition": "inline",
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
    }
    for h in ("Content-Length", "Content-Range"):
        if cdn.headers.get(h):
            out[h] = cdn.headers[h]

    return StreamingResponse(
        body(),
        status_code=cdn.status_code,
        media_type=cdn.headers.get("content-type", "video/mp4"),
        headers=out,
    )


@app.get("/api/stream/{subject_id}")
async def stream_video(
    request: Request,
    subject_id: str,
    detail_path: str = "",
    path: str = "",
    se: int = 0,
    ep: int = 0,
    quality: str = "",
):
    """Direct video streaming endpoint for in-browser watching (no signup needed)."""
    slug = clean_slug(detail_path or path)
    streams = await resolve_streams(subject_id, slug, se, ep)
    if not streams:
        return fail(404, "No stream found for this title/episode.")

    if quality:
        want = re.sub(r"\D", "", quality)
        pick = next((s for s in streams if str(s["resNum"]) == want), streams[0])
    else:
        pick = streams[0]

    return await proxy_stream(request, pick["url"])


# ------------------------------------------------------------------ Auth & User Routes
@app.get("/api/auth/config")
async def auth_config(request: Request):
    """Returns GitHub OAuth configuration and callback URL."""
    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    base = public_base(request)
    callback_url = f"{base}/api/auth/github/callback"
    return ok(
        "auth_config",
        hasOauth=bool(client_id),
        clientId=client_id,
        callbackUrl=callback_url,
        loginUrl="/api/auth/github/login" if client_id else "",
    )


@app.get("/api/auth/github/login")
async def auth_github_login(request: Request):
    """Directly redirect user to GitHub OAuth authorization page."""
    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    if not client_id:
        return HTMLResponse(
            """<html><body style="background:#07090e;color:#fff;font-family:sans-serif;padding:40px;text-align:center;">
            <h2>⚠️ GITHUB_CLIENT_ID Not Configured</h2>
            <p style="color:#94a3b8;">Please add GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET to your .env or Vercel Environment Variables.</p>
            <a href="/" style="color:#00d2ff;font-weight:bold;">← Return to Home</a>
            </body></html>""",
            status_code=400,
        )

    base = public_base(request)
    redirect_uri = f"{base}/api/auth/github/callback"
    github_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={quote(client_id)}"
        f"&redirect_uri={quote(redirect_uri)}"
        f"&scope=read:user,user:email"
    )
    return RedirectResponse(url=github_url)


@app.get("/api/auth/github/callback")
async def auth_github_callback(request: Request, code: str = "", error: str = ""):
    """GitHub OAuth callback endpoint. Exchanges code, retrieves user, saves to MongoDB."""
    if error or not code:
        return HTMLResponse(
            f"""<html><body style="background:#07090e;color:#fff;font-family:sans-serif;padding:40px;text-align:center;">
            <h2 style="color:#ef4444;">GitHub Authentication Error</h2>
            <p>{error or 'Authorization code missing'}</p>
            <a href="/?tab=docs" style="color:#00d2ff;">← Return to Docs</a>
            </body></html>""",
            status_code=400,
        )

    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return HTMLResponse(
            """<html><body style="background:#07090e;color:#fff;font-family:sans-serif;padding:40px;text-align:center;">
            <h2 style="color:#ef4444;">GitHub OAuth Configuration Missing</h2>
            <p>Please ensure GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET are set in your server environment.</p>
            <a href="/?tab=docs" style="color:#00d2ff;">← Return to Docs</a>
            </body></html>""",
            status_code=500,
        )

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            token_res = await c.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={"client_id": client_id, "client_secret": client_secret, "code": code},
            )
            token_data = token_res.json()
            access_token = token_data.get("access_token")

            if not access_token:
                desc = token_data.get("error_description") or "Invalid code or client secret"
                return HTMLResponse(
                    f"""<html><body style="background:#07090e;color:#fff;font-family:sans-serif;padding:40px;text-align:center;">
                    <h2 style="color:#ef4444;">Authentication Failed</h2>
                    <p>{desc}</p>
                    <a href="/?tab=docs" style="color:#00d2ff;">← Return to Docs</a>
                    </body></html>""",
                    status_code=400,
                )

            gh_headers = {"Authorization": f"Bearer {access_token}", "User-Agent": "Kurox-Movie-Pro"}
            u_res = await c.get("https://api.github.com/user", headers=gh_headers)
            gh_user = u_res.json() if u_res.status_code == 200 else {}

            username = gh_user.get("login") or "github_user"
            name = gh_user.get("name") or username
            avatar_url = gh_user.get("avatar_url") or ""
            github_id = str(gh_user.get("id", ""))
            email = gh_user.get("email") or ""

            if not email:
                em_res = await c.get("https://api.github.com/user/emails", headers=gh_headers)
                if em_res.status_code == 200:
                    emails_list = em_res.json()
                    primary = next((e.get("email") for e in emails_list if e.get("primary")), None)
                    if primary:
                        email = primary

            user = save_or_update_user(
                username=username,
                email=email,
                github_id=github_id,
                avatar_url=avatar_url,
                name=name,
            )

            user_json = json.dumps(user)
            html = f"""<!DOCTYPE html>
<html>
<head><title>Logging in...</title></head>
<body style="background:#07090e;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
  <div style="text-align:center;padding:20px;">
    <h2 style="color:#00d2ff;margin-bottom:8px;">Signed in as @{username}!</h2>
    <p style="color:#94a3b8;">Redirecting to your API Key and Developer Docs...</p>
  </div>
  <script>
    try {{
      localStorage.setItem('kurox_user', JSON.stringify({user_json}));
    }} catch (e) {{}}
    window.location.href = '/?tab=docs';
  </script>
</body>
</html>"""
            return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(
            f"""<html><body style="background:#07090e;color:#fff;font-family:sans-serif;padding:40px;text-align:center;">
            <h2 style="color:#ef4444;">Server Error</h2>
            <p>{e}</p>
            <a href="/?tab=docs" style="color:#00d2ff;">← Return to Docs</a>
            </body></html>""",
            status_code=500,
        )


@app.post("/api/auth/github")
async def auth_github(request: Request):
    """Secure Official GitHub OAuth Exchange Endpoint (Manual signup disabled for production security)."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    code = body.get("code")
    if not code:
        return fail(400, "Official GitHub OAuth code is required. Manual signups are disabled.")

    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return fail(500, "Server GitHub OAuth credentials (GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET) not configured.")

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            token_res = await c.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={"client_id": client_id, "client_secret": client_secret, "code": code},
            )
            token_data = token_res.json()
            access_token = token_data.get("access_token")
            if not access_token:
                desc = token_data.get("error_description") or "Invalid GitHub authorization code"
                return fail(401, f"GitHub authentication failed: {desc}")

            gh_headers = {"Authorization": f"Bearer {access_token}", "User-Agent": "Kurox-Movie-Pro"}
            u_res = await c.get("https://api.github.com/user", headers=gh_headers)
            if u_res.status_code != 200:
                return fail(401, "Failed to retrieve verified user profile from GitHub API.")

            gh_user = u_res.json()
            username = gh_user.get("login") or "github_user"
            name = gh_user.get("name") or username
            avatar_url = gh_user.get("avatar_url") or ""
            github_id = str(gh_user.get("id", ""))
            email = gh_user.get("email") or ""

            if not email:
                em_res = await c.get("https://api.github.com/user/emails", headers=gh_headers)
                if em_res.status_code == 200:
                    emails_list = em_res.json()
                    primary = next((e.get("email") for e in emails_list if e.get("primary")), None)
                    if primary:
                        email = primary

            user = save_or_update_user(
                username=username,
                email=email,
                github_id=github_id,
                avatar_url=avatar_url,
                name=name,
            )
            return ok("auth_success", user=user)
    except Exception as e:
        return fail(500, f"OAuth exchange error: {e}")


@app.get("/api/auth/me")
async def auth_me(request: Request, api_key: str = "", username: str = "", email: str = ""):
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        api_key = auth_header.replace("Bearer ", "").strip()
    elif request.headers.get("x-api-key"):
        api_key = request.headers.get("x-api-key").strip()

    user = None
    if api_key:
        user = get_user_by_api_key(api_key)
    if not user and email:
        user = find_user_by_identifier(email)
    if not user and username:
        user = find_user_by_identifier(username)

    if not user:
        return fail(404, "User not found or unauthenticated.")
    return ok("user_profile", user=user)


@app.post("/api/auth/regenerate-key")
async def regen_key(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    ident = body.get("identifier") or body.get("username") or body.get("email")
    if not ident:
        return fail(400, "User identifier is required.")
    new_key = regenerate_user_key(ident)
    return ok("regenerate_key", apiKey=new_key)


# ------------------------------------------------------------------ Admin Routes
@app.get("/api/admin/users")
async def admin_users(request: Request, email: str = ""):
    req_email = (email or request.headers.get("x-admin-email", "")).strip().lower()
    if req_email != ADMIN_EMAIL:
        return fail(403, f"Admin privileges required. Unauthorized for '{req_email}'.")
    users = list_all_users()
    return ok("admin_users", count=len(users), adminEmail=ADMIN_EMAIL, users=users)


@app.put("/api/admin/users/{username}/plan")
async def admin_change_plan(request: Request, username: str):
    try:
        body = await request.json()
    except Exception:
        body = {}
    req_email = (body.get("admin_email") or request.headers.get("x-admin-email", "")).strip().lower()
    if req_email != ADMIN_EMAIL:
        return fail(403, "Admin privileges required.")
    new_plan = body.get("plan", "").strip()
    if not new_plan:
        return fail(400, "Plan name is required.")
    updated = update_user_plan(username, new_plan)
    if not updated:
        return fail(404, f"User '{username}' not found.")
    return ok("update_plan", user=updated)


# ------------------------------------------------------------------ Plans Route
@app.get("/api/plans")
async def plans_catalog():
    plans = [
        {
            "id": "starter",
            "name": "Starter Plan",
            "requests": "100 Requests for day",
            "price": "LKR 49",
            "priceNumber": 49,
            "period": "/month",
            "badge": "Entry Tier",
            "features": [
                "100 API Requests daily quota",
                "Full Search & Detail Endpoints",
                "Dynamic JSON Metadata & Posters",
                "Direct CDN Stream Resolver",
                "Standard Response Time"
            ],
        },
        {
            "id": "pro",
            "name": "Pro Developer",
            "requests": "1000 Requests for day",
            "price": "LKR 149",
            "priceNumber": 149,
            "period": "/month",
            "popular": True,
            "badge": "Most Popular",
            "features": [
                "1,000 API Requests daily quota",
                "1080p, 720p, 480p Multi-Stream",
                "Built-in Geo & Datacenter Proxy Bypass",
                "WhatsApp Bot (Baileys) Integration Code",
                "High Speed Serverless Routing"
            ],
        },
        {
            "id": "unlimited",
            "name": "Unlimited VIP",
            "requests": "Unlimited Requests for day",
            "price": "LKR 249",
            "priceNumber": 249,
            "period": "/month",
            "badge": "VIP Unlimited",
            "features": [
                "Unlimited Requests Daily (Zero Limits)",
                "Direct In-Browser MP4 Video Stream",
                "Permanent CDN Download URLs",
                "Dedicated Bandwidth & Support",
                "Full Baileys Bot Automation Code",
                "Direct WhatsApp VIP Priority Support"
            ],
        },
    ]
    return ok("plans", whatsapp="94760771665", adminEmail=ADMIN_EMAIL, plans=plans)

