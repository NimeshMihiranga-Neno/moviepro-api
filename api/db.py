import os
import json
import secrets
import datetime
from pathlib import Path

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "nimeshmihiranga.doc@gmail.com").strip().lower()
MONGODB_URI = os.environ.get("MONGODB_URI", "").strip()
MONGODB_DB_NAME = os.environ.get("MONGODB_DB_NAME", "moviebox_db").strip()

LOCAL_DB_FILE = Path(__file__).resolve().parent / "users_local_db.json"

_mongo_client = None
_users_col = None

def get_mongo_collection():
    global _mongo_client, _users_col
    if _users_col is not None:
        return _users_col
    if not MONGODB_URI:
        return None
    try:
        from pymongo import MongoClient
        _mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=4000)
        # Test connection ping
        _mongo_client.admin.command('ping')
        db = _mongo_client[MONGODB_DB_NAME]
        _users_col = db["users"]
        print(f"[MongoDB] Connected successfully to database '{MONGODB_DB_NAME}'")
        return _users_col
    except Exception as e:
        print(f"[MongoDB] Connection warning (using local fallback): {e}")
        return None

def _read_local_db() -> list:
    if not LOCAL_DB_FILE.exists():
        return []
    try:
        with open(LOCAL_DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _write_local_db(users: list):
    try:
        with open(LOCAL_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2, default=str)
    except Exception as e:
        print(f"[LocalDB] Write error: {e}")

def generate_api_key() -> str:
    return f"kurox_{secrets.token_hex(16)}"

def clean_doc(doc: dict) -> dict:
    if not doc:
        return None
    doc = dict(doc)
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

def find_user_by_identifier(identifier: str) -> dict | None:
    if not identifier:
        return None
    ident = str(identifier).strip().lower()
    col = get_mongo_collection()
    if col is not None:
        try:
            doc = col.find_one({
                "$or": [
                    {"email": {"$regex": f"^{ident}$", "$options": "i"}},
                    {"username": {"$regex": f"^{ident}$", "$options": "i"}},
                    {"githubId": ident},
                    {"apiKey": identifier.strip()}
                ]
            })
            return clean_doc(doc)
        except Exception as e:
            print(f"[MongoDB] find_user error: {e}")
    
    # Fallback to local
    users = _read_local_db()
    for u in users:
        if (
            str(u.get("email", "")).lower() == ident
            or str(u.get("username", "")).lower() == ident
            or str(u.get("githubId", "")).lower() == ident
            or str(u.get("apiKey", "")).strip() == identifier.strip()
        ):
            return u
    return None

def get_user_by_api_key(api_key: str) -> dict | None:
    if not api_key:
        return None
    api_key = api_key.strip()
    col = get_mongo_collection()
    if col is not None:
        try:
            doc = col.find_one({"apiKey": api_key})
            return clean_doc(doc)
        except Exception:
            pass
    users = _read_local_db()
    for u in users:
        if u.get("apiKey") == api_key:
            return u
    return None

def save_or_update_user(
    username: str,
    email: str = "",
    github_id: str = "",
    avatar_url: str = "",
    name: str = ""
) -> dict:
    username = (username or "github_user").strip()
    email = (email or "").strip().lower()
    github_id = str(github_id or "").strip()
    name = (name or username).strip()
    is_admin = bool(email and email == ADMIN_EMAIL or "nimeshmihiranga" in username.lower())
    
    existing = None
    if email:
        existing = find_user_by_identifier(email)
    if not existing and github_id:
        existing = find_user_by_identifier(github_id)
    if not existing and username:
        existing = find_user_by_identifier(username)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    col = get_mongo_collection()
    if existing:
        # Update existing
        api_key = existing.get("apiKey") or generate_api_key()
        plan = existing.get("plan") or ("Unlimited (LKR 249)" if is_admin else "No Active Plan")
        updates = {
            "username": username,
            "name": name or existing.get("name"),
            "email": email or existing.get("email"),
            "avatarUrl": avatar_url or existing.get("avatarUrl"),
            "apiKey": api_key,
            "plan": plan,
            "isAdmin": is_admin or existing.get("isAdmin", False),
            "role": "admin" if (is_admin or existing.get("isAdmin")) else "user",
            "updatedAt": now_iso
        }
        if github_id:
            updates["githubId"] = github_id

        if col is not None:
            try:
                col.update_one({"_id": existing.get("_id")}, {"$set": updates})
                existing.update(updates)
                return clean_doc(existing)
            except Exception as e:
                print(f"[MongoDB] update error: {e}")

        # Local update
        users = _read_local_db()
        for idx, u in enumerate(users):
            if (email and u.get("email") == email) or (username and u.get("username") == username) or (github_id and u.get("githubId") == github_id):
                users[idx].update(updates)
                _write_local_db(users)
                return users[idx]
        return updates

    # Create new user: Default to No Active Plan for non-admin
    api_key = generate_api_key()
    today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    new_user = {
        "githubId": github_id or f"gh_{secrets.token_hex(4)}",
        "username": username,
        "name": name,
        "email": email,
        "avatarUrl": avatar_url or f"https://api.dicebear.com/7.x/identicon/svg?seed={username}",
        "apiKey": api_key,
        "plan": "Unlimited (LKR 249)" if is_admin else "No Active Plan",
        "isAdmin": is_admin,
        "role": "admin" if is_admin else "user",
        "requestsToday": 0,
        "lastRequestDate": today_str,
        "createdAt": now_iso,
        "updatedAt": now_iso
    }

    if col is not None:
        try:
            res = col.insert_one(new_user)
            new_user["_id"] = str(res.inserted_id)
            return clean_doc(new_user)
        except Exception as e:
            print(f"[MongoDB] insert error: {e}")

    # Local save
    users = _read_local_db()
    users.append(new_user)
    _write_local_db(users)
    return new_user

def list_all_users() -> list:
    col = get_mongo_collection()
    if col is not None:
        try:
            docs = list(col.find().sort("createdAt", -1))
            return [clean_doc(d) for d in docs]
        except Exception as e:
            print(f"[MongoDB] list error: {e}")
    users = _read_local_db()
    return sorted(users, key=lambda x: x.get("createdAt", ""), reverse=True)

def update_user_plan(identifier: str, new_plan: str) -> dict | None:
    ident = str(identifier).strip()
    col = get_mongo_collection()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if col is not None:
        try:
            doc = col.find_one({
                "$or": [
                    {"email": {"$regex": f"^{ident}$", "$options": "i"}},
                    {"username": {"$regex": f"^{ident}$", "$options": "i"}},
                    {"apiKey": ident}
                ]
            })
            if doc:
                col.update_one({"_id": doc["_id"]}, {"$set": {"plan": new_plan, "updatedAt": now_iso}})
                doc["plan"] = new_plan
                doc["updatedAt"] = now_iso
                return clean_doc(doc)
        except Exception as e:
            print(f"[MongoDB] update_plan error: {e}")

    users = _read_local_db()
    for idx, u in enumerate(users):
        if (
            str(u.get("email", "")).lower() == ident.lower()
            or str(u.get("username", "")).lower() == ident.lower()
            or str(u.get("apiKey", "")).strip() == ident
        ):
            users[idx]["plan"] = new_plan
            users[idx]["updatedAt"] = now_iso
            _write_local_db(users)
            return users[idx]
    return None

def regenerate_user_key(identifier: str) -> str:
    new_key = generate_api_key()
    ident = str(identifier).strip()
    col = get_mongo_collection()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if col is not None:
        try:
            col.update_one(
                {"$or": [{"email": ident}, {"username": ident}, {"apiKey": ident}]},
                {"$set": {"apiKey": new_key, "updatedAt": now_iso}}
            )
            return new_key
        except Exception:
            pass

    users = _read_local_db()
    for idx, u in enumerate(users):
        if u.get("email") == ident or u.get("username") == ident or u.get("apiKey") == ident:
            users[idx]["apiKey"] = new_key
            users[idx]["updatedAt"] = now_iso
            _write_local_db(users)
            return new_key
    return new_key


def update_user_quota(user: dict, requests_today: int, last_date: str):
    """Saves updated request count and last request date in MongoDB or local DB."""
    ident = user.get("apiKey")
    col = get_mongo_collection()
    if col is not None:
        try:
            col.update_one(
                {"apiKey": ident},
                {"$set": {"requestsToday": requests_today, "lastRequestDate": last_date}}
            )
            return
        except Exception as e:
            print(f"[MongoDB] update quota error: {e}")
    users = _read_local_db()
    for idx, u in enumerate(users):
        if u.get("apiKey") == ident:
            users[idx]["requestsToday"] = requests_today
            users[idx]["lastRequestDate"] = last_date
            _write_local_db(users)
            break


def check_and_consume_quota(api_key: str) -> tuple[bool, int, str, dict | None]:
    """
    Validates API key and enforces plan status & daily limits.
    Returns: (is_allowed, status_code, message_or_error, user_dict)
    """
    if not api_key:
        return (
            False,
            401,
            "API_KEY_REQUIRED: API key is required. Please pass your API key in the 'X-API-Key' header or '?api_key=' parameter.",
            None,
        )

    user = get_user_by_api_key(api_key)
    if not user:
        return (
            False,
            401,
            "INVALID_API_KEY: Invalid API key provided. Please sign in to verify your key.",
            None,
        )

    # Admin bypass (Always unlimited)
    if user.get("isAdmin") or str(user.get("email", "")).strip().lower() == ADMIN_EMAIL:
        return True, 200, "OK", user

    plan = str(user.get("plan") or "").strip()
    plan_lower = plan.lower()

    # Verify if user has an active plan
    has_active_plan = (
        ("100" in plan or "1000" in plan or "unlimited" in plan_lower or "starter" in plan_lower or "pro" in plan_lower)
        and not ("no active plan" in plan_lower or "none" in plan_lower or "inactive" in plan_lower)
    )

    if not has_active_plan:
        return (
            False,
            403,
            "PLAN_REQUIRED: No active plan assigned to this API key. You cannot make API requests without an active plan. Please visit the API Plans tab to choose a plan (100 req/day for LKR 49, 1000 req/day for LKR 149, or Unlimited for LKR 249).",
            user,
        )

    # Determine daily limit
    if "unlimited" in plan_lower:
        daily_limit = float("inf")
    elif "1000" in plan or "pro" in plan_lower:
        daily_limit = 1000
    else:
        daily_limit = 100

    today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    last_date = user.get("lastRequestDate")
    requests_today = int(user.get("requestsToday") or 0)

    # Reset count if it's a new day
    if last_date != today_str:
        requests_today = 0
        last_date = today_str

    # Check daily limit
    if requests_today >= daily_limit:
        return (
            False,
            429,
            f"DAILY_LIMIT_EXCEEDED: You have exhausted your daily limit of {daily_limit} requests for '{plan}'. Requests reset tomorrow at 00:00 UTC. To continue, upgrade your plan in the API Plans tab.",
            user,
        )

    # Consume 1 request
    requests_today += 1
    user["requestsToday"] = requests_today
    user["lastRequestDate"] = today_str
    update_user_quota(user, requests_today, today_str)

    return True, 200, "OK", user

