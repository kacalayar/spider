#!/usr/bin/env python3
import base64
import datetime
import html
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ENV_FILE = "/etc/spider-bridge/config.env"
APPLY_CMD = "/usr/local/sbin/spider-bridge-apply"
STATE_DIR = "/var/lib/spider-bridge"
COUNTRIES_CACHE_FILE = os.path.join(STATE_DIR, "countries.json")
UPDATE_OFFSET_FILE = os.path.join(STATE_DIR, "telegram_update_offset")
USERS_FILE = os.path.join(STATE_DIR, "users.json")
GOST_SERVICE_NAME = "spider-bridge-proxy"
USER_SERVICE_PREFIX = "spider-bridge-user"
COUNTRY_SOURCE_URL = "https://spider.cloud/proxy-locations"
COUNTRY_CACHE_TTL_SECONDS = 24 * 60 * 60
COUNTRY_PAGE_SIZE = 40
USER_PORT_START = 32000
USER_PORT_END = 32999
HTTPS_IP_CHECK_URLS = [
    ("api.ipify", "https://api.ipify.org?format=json"),
    ("icanhazip", "https://icanhazip.com"),
    ("ident.me", "https://ident.me"),
    ("checkip.amazonaws", "https://checkip.amazonaws.com"),
]
HTTP_IP_CHECK_URLS = [
    ("api.ipify", "http://api.ipify.org?format=json"),
    ("icanhazip", "http://icanhazip.com"),
    ("ident.me", "http://ident.me"),
    ("checkip.amazonaws", "http://checkip.amazonaws.com"),
]
FRAUD_CHECK_URL_TEMPLATE = "https://proxycheck.io/v2/{ip}?risk=1&vpn=1&asn=1&node=1&time=1"
SPIDER_CREDITS_URL = "https://api.spider.cloud/data/credits"
TELEGRAM_SAFE_TEXT_LIMIT = 3500

PROXY_TYPES = [
    "default",
    "residential",
    "residential_static",
    "residential_fast",
    "residential_core",
    "residential_plus",
    "residential_premium",
    "mobile",
    "isp",
]

ENV_KEYS = [
    "BRIDGE_ENGINE",
    "SPIDER_API_KEY",
    "SPIDER_PROXY_TYPE",
    "SPIDER_COUNTRY_CODE",
    "SPIDER_COUNTRY_PARAM",
    "SPIDER_EXTRA_PARAMS",
    "SPIDER_UPSTREAM_SCHEME",
    "SPIDER_UPSTREAM_HOST",
    "SPIDER_UPSTREAM_PORT",
    "LOCAL_PROXY_USER",
    "LOCAL_PROXY_PASS",
    "LOCAL_PROXY_PORT",
    "VPS_PUBLIC_IP",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ADMIN_IDS",
    "SETUP_TOKEN",
]

HELP_TEXT = """<b>Spider Bridge Bot</b>

Perintah:
/status - lihat konfigurasi aktif
/countries - pilih lokasi dari Spider Proxy Locations
/refreshcountries - refresh daftar lokasi dari Spider
/pools - pilih pool Spider dengan tombol
/setcountry US - ubah lokasi, contoh US atau ID
/setcountry off - pakai default Spider
/setcountryparam country_code - pilih parameter country_code atau country
/setproxy residential - ubah pool Spider, pakai default untuk tanpa proxy=...
/setengine gost - pastikan engine GOST
/setupstream socks5 - pastikan upstream Spider SOCKS5
/showproxy - tampilkan format ip:port:user:pass
/test - test proxy lokal via Spider
/testurl https://example.com - test URL real lewat proxy lokal
/diag - diagnosa bridge ke Spider upstream
/balance - cek credit balance Spider
/apply - tulis ulang config dan restart proxy
/setlocalpass PASSWORD - ubah password proxy lokal
/setlocaluser USER - ubah user proxy lokal
/setport 3128 - ubah port proxy lokal
/whoami - lihat Telegram user ID
/addadmin USER_ID - tambah admin
/deladmin USER_ID - hapus admin
/adduser USER_ID 30d - tambah user rental
/deluser USER_ID - hapus user rental
/listuser - lihat user rental
"""

USER_HELP_TEXT = """<b>Spider Bridge User</b>

Perintah:
/status - lihat proxy Anda
/showproxy - tampilkan ip:port:user:pass
/countries - pilih lokasi Spider
/pools - pilih pool Spider
/setcountry US - ubah lokasi proxy Anda
/setcountry off - pakai default Spider
/setproxy residential - ubah pool proxy Anda
/test - test proxy Anda
/testurl https://example.com - test URL real
/whoami - lihat Telegram user ID
"""


def log(message):
    print(message, flush=True)


def escape(value):
    return html.escape(str(value), quote=False)


def load_env():
    data = {}
    if not os.path.exists(ENV_FILE):
        return data

    with open(ENV_FILE, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key] = value
    return data


def save_env(data):
    tmp_path = ENV_FILE + ".tmp"
    lines = []

    for key in ENV_KEYS:
        if key in data:
            lines.append(f"{key}={data[key]}\n")

    for key in sorted(set(data) - set(ENV_KEYS)):
        lines.append(f"{key}={data[key]}\n")

    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.writelines(lines)

    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, ENV_FILE)


def read_countries_cache():
    if not os.path.exists(COUNTRIES_CACHE_FILE):
        return [], 0

    try:
        with open(COUNTRIES_CACHE_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return [], 0

    countries = payload.get("countries", [])
    fetched_at = int(payload.get("fetched_at", 0) or 0)
    valid = sorted(
        {
            country.upper()
            for country in countries
            if isinstance(country, str) and re.fullmatch(r"[A-Z]{2}", country.upper())
        }
    )
    return valid, fetched_at


def write_countries_cache(countries):
    os.makedirs(STATE_DIR, mode=0o755, exist_ok=True)
    payload = {
        "source": COUNTRY_SOURCE_URL,
        "fetched_at": int(time.time()),
        "countries": countries,
    }
    tmp_path = COUNTRIES_CACHE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(tmp_path, 0o644)
    os.replace(tmp_path, COUNTRIES_CACHE_FILE)


def read_update_offset():
    try:
        with open(UPDATE_OFFSET_FILE, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError:
        return None

    if not value.isdigit():
        return None
    return int(value)


def write_update_offset(offset):
    os.makedirs(STATE_DIR, mode=0o755, exist_ok=True)
    tmp_path = UPDATE_OFFSET_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(f"{int(offset)}\n")
    os.chmod(tmp_path, 0o644)
    os.replace(tmp_path, UPDATE_OFFSET_FILE)


def read_users():
    if not os.path.exists(USERS_FILE):
        return {}

    try:
        with open(USERS_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}

    users = payload.get("users", payload)
    if not isinstance(users, dict):
        return {}

    valid = {}
    for user_id, record in users.items():
        if str(user_id).isdigit() and isinstance(record, dict):
            valid[str(user_id)] = record
    return valid


def write_users(users):
    os.makedirs(STATE_DIR, mode=0o755, exist_ok=True)
    payload = {
        "version": 1,
        "updated_at": int(time.time()),
        "users": users,
    }
    tmp_path = USERS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, USERS_FILE)


def format_expiry(expires_at):
    expires_at = int(expires_at or 0)
    if expires_at <= 0:
        return "never"
    return datetime.datetime.fromtimestamp(expires_at).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def format_time_remaining(expires_at):
    expires_at = int(expires_at or 0)
    if expires_at <= 0:
        return "permanent"

    seconds = expires_at - int(time.time())
    if seconds <= 0:
        return "expired"

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def parse_expiry(value):
    raw = (value or "").strip()
    lowered = raw.lower()
    if lowered in {"never", "permanent", "permanen", "none", "0", "-"}:
        return 0

    now = datetime.datetime.now().astimezone()
    match = re.fullmatch(r"(\d+)([mhdw]?)", lowered)
    if match:
        amount = int(match.group(1))
        unit = match.group(2) or "d"
        if amount <= 0:
            raise ValueError("durasi harus lebih dari 0")
        if unit == "m":
            delta = datetime.timedelta(minutes=amount)
        elif unit == "h":
            delta = datetime.timedelta(hours=amount)
        elif unit == "w":
            delta = datetime.timedelta(weeks=amount)
        else:
            delta = datetime.timedelta(days=amount)
        return int((now + delta).timestamp())

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y/%m/%d %H:%M"):
        try:
            parsed = datetime.datetime.strptime(raw, fmt).replace(tzinfo=now.tzinfo)
            break
        except ValueError:
            parsed = None
    if parsed is None:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                date_value = datetime.datetime.strptime(raw, fmt).date()
                parsed = datetime.datetime.combine(date_value, datetime.time(23, 59, 59), tzinfo=now.tzinfo)
                break
            except ValueError:
                parsed = None

    if parsed is None:
        raise ValueError("format expired: 30d, 12h, 2026-07-13, atau never")
    if parsed.timestamp() <= now.timestamp():
        raise ValueError("expired harus di masa depan")
    return int(parsed.timestamp())


def is_user_active(record):
    if not record or not record.get("enabled", True):
        return False
    expires_at = int(record.get("expires_at", 0) or 0)
    return expires_at <= 0 or expires_at > int(time.time())


def user_service_name(user_id):
    return f"{USER_SERVICE_PREFIX}-{int(user_id)}.service"


def user_unit_path(user_id):
    return f"/etc/systemd/system/{user_service_name(user_id)}"


def random_local_credential(prefix, size=10):
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    suffix = "".join(secrets.choice(alphabet) for _ in range(size))
    return f"{prefix}{suffix}"[:64]


def port_available(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
            handle.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            handle.bind(("0.0.0.0", int(port)))
        return True
    except OSError:
        return False


def allocate_user_port(env, users):
    used = {str(record.get("port", "")) for record in users.values()}
    used.add(str(env.get("LOCAL_PROXY_PORT", "3128")))
    for port in range(USER_PORT_START, USER_PORT_END + 1):
        if str(port) not in used and port_available(port):
            return str(port)
    raise RuntimeError(f"Tidak ada port kosong di range {USER_PORT_START}-{USER_PORT_END}")


def user_config_env(env, record):
    data = dict(env)
    data["LOCAL_PROXY_USER"] = record.get("username", "")
    data["LOCAL_PROXY_PASS"] = record.get("password", "")
    data["LOCAL_PROXY_PORT"] = str(record.get("port", ""))
    data["SPIDER_PROXY_TYPE"] = record.get("pool") or env.get("SPIDER_PROXY_TYPE", "residential")
    data["SPIDER_COUNTRY_CODE"] = record.get("country", "")
    data["SPIDER_COUNTRY_PARAM"] = record.get("country_param") or env.get("SPIDER_COUNTRY_PARAM", "country_code")
    data["SPIDER_UPSTREAM_SCHEME"] = "socks5"
    data["SPIDER_UPSTREAM_PORT"] = "8887"
    return data


def systemd_escape_percent(value):
    return str(value).replace("%", "%%")


def write_user_service(env, user_id, record):
    cfg = user_config_env(env, record)
    local_user = urllib.parse.quote(cfg.get("LOCAL_PROXY_USER", ""), safe="")
    local_pass = urllib.parse.quote(cfg.get("LOCAL_PROXY_PASS", ""), safe="")
    spider_user = urllib.parse.quote(cfg.get("SPIDER_API_KEY", ""), safe="")
    spider_pass = urllib.parse.quote(spider_password(cfg), safe="")
    upstream_host = cfg.get("SPIDER_UPSTREAM_HOST", "proxy.spider.cloud")
    local_url = f"http://{local_user}:{local_pass}@0.0.0.0:{cfg.get('LOCAL_PROXY_PORT')}"
    upstream_url = f"socks5://{spider_user}:{spider_pass}@{upstream_host}:8887"
    expires_at = int(record.get("expires_at", 0) or 0)
    exec_start_pre = ""
    if expires_at > 0:
        exec_start_pre = f"ExecStartPre=/bin/sh -c 'test \"$(date +%%s)\" -lt {expires_at}'\n"

    unit = f"""[Unit]
Description=Spider Bridge User Proxy {user_id}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
{exec_start_pre}ExecStart=/usr/local/bin/gost -L {systemd_escape_percent(local_url)} -F {systemd_escape_percent(upstream_url)}
Restart=on-failure
RestartSec=3
User=root
NoNewPrivileges=true
PrivateTmp=true
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
"""
    path = user_unit_path(user_id)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(unit)
    os.chmod(tmp_path, 0o644)
    os.replace(tmp_path, path)


def systemctl(*args, timeout=30):
    return subprocess.run(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def open_ufw_port(port):
    try:
        status = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=8, check=False)
    except Exception:
        return
    if re.search(r"^Status:\s+active", status.stdout, re.IGNORECASE | re.MULTILINE):
        subprocess.run(["ufw", "allow", f"{port}/tcp"], capture_output=True, text=True, timeout=20, check=False)


def apply_user_service(env, user_id, record):
    if not is_user_active(record):
        stop_user_service(user_id, remove_unit=False)
        return False, "User sudah expired atau disabled."

    write_user_service(env, user_id, record)
    outputs = []
    for args in (("daemon-reload",), ("enable", user_service_name(user_id)), ("restart", user_service_name(user_id))):
        result = systemctl(*args, timeout=45)
        output = (result.stdout + "\n" + result.stderr).strip()
        if output:
            outputs.append(output[-800:])
        if result.returncode != 0:
            return False, "\n".join(outputs) or f"systemctl {' '.join(args)} gagal"
    open_ufw_port(record.get("port", ""))
    return True, f"{user_service_name(user_id)} active on port {record.get('port')}"


def stop_user_service(user_id, remove_unit=True):
    outputs = []
    service = user_service_name(user_id)
    for args in (("disable", "--now", service),):
        result = systemctl(*args, timeout=45)
        output = (result.stdout + "\n" + result.stderr).strip()
        if output:
            outputs.append(output[-800:])
    if remove_unit:
        try:
            os.remove(user_unit_path(user_id))
        except FileNotFoundError:
            pass
        except OSError as exc:
            outputs.append(str(exc))
    systemctl("daemon-reload", timeout=30)
    return "\n".join(outputs).strip()


def reconcile_user_services(env, users=None, token=None):
    users = users if users is not None else read_users()
    changed = False
    for user_id, record in users.items():
        if is_user_active(record):
            continue
        if record.get("enabled", True):
            record["enabled"] = False
            record["updated_at"] = int(time.time())
            changed = True
        stop_user_service(user_id, remove_unit=False)
        if token:
            try:
                set_commands_for_chat(token, user_id, PUBLIC_COMMANDS)
            except Exception as exc:
                log(f"Unable to reset expired user commands for {user_id}: {exc}")
    if changed:
        write_users(users)


def fetch_spider_countries():
    request = urllib.request.Request(
        COUNTRY_SOURCE_URL,
        headers={"User-Agent": "spider-bridge-bot/1.0"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        body = response.read().decode("utf-8", errors="replace")

    countries = sorted({match.group(1).upper() for match in re.finditer(r"\bcountry=([a-z]{2})\b", body)})
    if len(countries) < 20:
        raise RuntimeError(f"Spider locations parse returned only {len(countries)} countries")

    write_countries_cache(countries)
    return countries


def get_spider_countries(force_refresh=False):
    cached, fetched_at = read_countries_cache()
    cache_age = int(time.time()) - fetched_at if fetched_at else None

    if cached and not force_refresh and cache_age is not None and cache_age < COUNTRY_CACHE_TTL_SECONDS:
        return cached, "cache", None

    try:
        countries = fetch_spider_countries()
        return countries, "spider", None
    except Exception as exc:
        if cached:
            return cached, "stale-cache", str(exc)
        return [], "unavailable", str(exc)


def telegram_api(token, method, payload=None, timeout=35):
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        parsed = json.loads(response.read().decode("utf-8"))

    if not parsed.get("ok"):
        raise RuntimeError(f"Telegram API error: {parsed}")

    return parsed.get("result")


def split_text_chunks(text, limit=TELEGRAM_SAFE_TEXT_LIMIT):
    text = str(text)
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < 1:
            cut = text.rfind("\n", 0, limit)
        if cut < 1:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    chunks.append(text)
    return chunks or [""]


def html_to_plain(text):
    plain = re.sub(r"</?(?:b|code|blockquote)>", "", str(text))
    return html.unescape(plain)


def send_message(token, chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    chunks = split_text_chunks(text)
    for index, chunk in enumerate(chunks):
        payload["text"] = chunk
        if reply_markup and index > 0:
            payload.pop("reply_markup", None)
        try:
            telegram_api(token, "sendMessage", payload)
        except Exception as exc:
            log(f"HTML sendMessage failed, retrying as plain text: {exc}")
            fallback = {
                "chat_id": chat_id,
                "text": html_to_plain(chunk)[-TELEGRAM_SAFE_TEXT_LIMIT:],
                "disable_web_page_preview": True,
            }
            telegram_api(token, "sendMessage", fallback)


def send_chat_action(token, chat_id, action="typing"):
    try:
        telegram_api(token, "sendChatAction", {"chat_id": chat_id, "action": action}, timeout=10)
    except Exception as exc:
        log(f"Unable to send chat action: {exc}")


def edit_message(token, chat_id, message_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        telegram_api(token, "editMessageText", payload)
    except RuntimeError as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


def answer_callback(token, callback_id, text=""):
    telegram_api(
        token,
        "answerCallbackQuery",
        {"callback_query_id": callback_id, "text": text[:190]},
        timeout=10,
    )


def parse_admin_ids(value):
    ids = set()
    for item in value.split(","):
        item = item.strip()
        if item.isdigit():
            ids.add(int(item))
    return ids


def sender_id(update):
    if "message" in update:
        return update["message"].get("from", {}).get("id")
    if "callback_query" in update:
        return update["callback_query"].get("from", {}).get("id")
    return None


def chat_id(update):
    if "message" in update:
        return update["message"].get("chat", {}).get("id")
    if "callback_query" in update:
        return update["callback_query"].get("message", {}).get("chat", {}).get("id")
    return None


def callback_message_id(update):
    if "callback_query" not in update:
        return None
    return update["callback_query"].get("message", {}).get("message_id")


def is_admin(env, update):
    user_id = sender_id(update)
    return user_id in parse_admin_ids(env.get("TELEGRAM_ADMIN_IDS", ""))


def valid_country(value):
    return value == "" or re.fullmatch(r"[A-Z]{2}", value or "") is not None


def valid_local_credential(value):
    return re.fullmatch(r"[A-Za-z0-9._-]{3,64}", value or "") is not None


def valid_port(value):
    if not re.fullmatch(r"[0-9]+", value or ""):
        return False
    port = int(value)
    return 1 <= port <= 65535


def valid_country_param(value):
    return value in {"country", "country_code"}


def valid_bridge_engine(value):
    return value == "gost"


def valid_upstream_scheme(value):
    return value == "socks5"


def valid_test_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def default_upstream_port(scheme):
    return "8887"


def bridge_engine(env):
    return "gost"


def upstream_scheme(env):
    return "socks5"


def upstream_port(env):
    port = env.get("SPIDER_UPSTREAM_PORT") or default_upstream_port(upstream_scheme(env))
    if port in {"8888", "8889"}:
        return "8887"
    return port


def upstream_endpoint(env):
    scheme = upstream_scheme(env)
    host = env.get("SPIDER_UPSTREAM_HOST", "proxy.spider.cloud")
    return f"{scheme}://{host}:{upstream_port(env)}"


def spider_password(env):
    proxy_type = env.get("SPIDER_PROXY_TYPE", "residential").lower()
    if proxy_type == "datacenter":
        proxy_type = "isp"

    parts = []
    if proxy_type and proxy_type != "default":
        parts.append(f"proxy={proxy_type}")

    country = env.get("SPIDER_COUNTRY_CODE", "")
    country_param = env.get("SPIDER_COUNTRY_PARAM", "country_code")
    if country:
        country_value = country.lower() if country_param == "country" else country.upper()
        parts.append(f"{country_param}={country_value}")

    extra = env.get("SPIDER_EXTRA_PARAMS", "")
    if extra:
        parts.append(extra)

    return "&".join(parts)


def spider_auth_header(env):
    raw = f"{env.get('SPIDER_API_KEY', '')}:{spider_password(env)}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def build_keyboard(kind):
    if kind == "pools":
        rows = []
        for index in range(0, len(PROXY_TYPES), 2):
            rows.append(
                [
                    {"text": pool, "callback_data": f"proxy:{pool}"}
                    for pool in PROXY_TYPES[index : index + 2]
                ]
            )
        return {"inline_keyboard": rows}

    return None


def build_countries_keyboard(countries, page):
    page_count = max(1, (len(countries) + COUNTRY_PAGE_SIZE - 1) // COUNTRY_PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    start = page * COUNTRY_PAGE_SIZE
    visible = countries[start : start + COUNTRY_PAGE_SIZE]

    rows = []
    for index in range(0, len(visible), 5):
        rows.append(
            [
                {"text": country, "callback_data": f"country:{country}"}
                for country in visible[index : index + 5]
            ]
        )

    nav = []
    if page > 0:
        nav.append({"text": "Prev", "callback_data": f"countries:{page - 1}"})
    nav.append({"text": f"{page + 1}/{page_count}", "callback_data": f"noop:countries:{page}"})
    if page + 1 < page_count:
        nav.append({"text": "Next", "callback_data": f"countries:{page + 1}"})
    rows.append(nav)
    rows.append([{"text": "Default Spider", "callback_data": "country:"}])
    return {"inline_keyboard": rows}


def service_state(name):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        return result.stdout.strip() or result.stderr.strip() or "unknown"
    except Exception as exc:
        return f"unknown ({exc})"


def proxy_service_name(env):
    return GOST_SERVICE_NAME


def proxy_service_label(env):
    return "GOST"


def run_apply():
    try:
        result = subprocess.run(
            [APPLY_CMD],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        output = (stdout + "\n" + stderr).strip()
        message = "Apply timeout setelah 90 detik."
        if output:
            message += f"\n{output[-1400:]}"
        message += "\nCek di VPS: journalctl -u spider-bridge-bot -n 80 --no-pager"
        return False, message[-1800:]
    except FileNotFoundError:
        return False, f"Apply command tidak ditemukan: {APPLY_CMD}"
    except Exception as exc:
        return False, f"Apply gagal sebelum selesai: {exc}"

    output = (result.stdout + "\n" + result.stderr).strip()
    if not output:
        output = f"{APPLY_CMD} exit code {result.returncode}"
    return result.returncode == 0, output[-1800:]


def proxy_line(env):
    host = env.get("VPS_PUBLIC_IP") or "<VPS_IP>"
    return f"{host}:{env.get('LOCAL_PROXY_PORT', '3128')}:{env.get('LOCAL_PROXY_USER', 'proxyuser')}:{env.get('LOCAL_PROXY_PASS', '<password>')}"


def proxy_copy_keyboard(env):
    return proxy_copy_keyboard_for_text(proxy_line(env))


def proxy_copy_keyboard_for_text(text):
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Copy proxy",
                    "copy_text": {"text": text},
                }
            ]
        ]
    }


def local_proxy_url(env):
    user = urllib.parse.quote(env.get("LOCAL_PROXY_USER", ""), safe="")
    password = urllib.parse.quote(env.get("LOCAL_PROXY_PASS", ""), safe="")
    port = env.get("LOCAL_PROXY_PORT", "3128")
    return f"http://{user}:{password}@127.0.0.1:{port}"


def fetch_ip(opener, url, timeout):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "spider-bridge-bot/1.0"},
    )
    try:
        if opener is None:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(4096).decode("utf-8", errors="replace")
        else:
            with opener.open(request, timeout=timeout) as response:
                body = response.read(4096).decode("utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "ip": "", "raw": "", "error": str(exc), "url": url}

    ip = extract_ip_from_body(body)
    if not ip:
        return {
            "ok": True,
            "ip": "",
            "raw": body.strip(),
            "error": "response does not contain an IP",
            "url": url,
        }
    if not is_global_ip(ip):
        return {
            "ok": False,
            "ip": "",
            "raw": body.strip(),
            "error": f"non-public IP returned: {ip}",
            "url": url,
        }

    return {"ok": True, "ip": ip, "raw": body.strip(), "error": "", "url": url}


def fetch_first_ip(opener, candidates, timeout):
    failures = []
    for label, url in candidates:
        result = fetch_ip(opener, url, timeout)
        result["target"] = label
        if result["ok"] and result["ip"]:
            return result
        failures.append(f"{label}: {result.get('error') or result.get('raw') or 'no IP'}")

    return {
        "ok": False,
        "ip": "",
        "raw": "",
        "error": " | ".join(failures)[-1500:],
        "url": "",
        "target": "all",
    }


def status_exit_ip(check):
    https = check.get("https", {})
    http = check.get("http", {})
    if https.get("ok") and https.get("ip"):
        return https["ip"], "HTTPS"
    if http.get("ok") and http.get("ip"):
        return http["ip"], "HTTP"
    return "", ""


def fetch_fraud_score(ip, timeout=12):
    if not ip or not is_global_ip(ip):
        return {"ok": False, "ip": ip, "error": "exit IP tidak valid", "source": "proxycheck.io"}

    url = FRAUD_CHECK_URL_TEMPLATE.format(ip=urllib.parse.quote(ip, safe=""))
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "spider-bridge-bot/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(65536).decode("utf-8", errors="replace"))
    except Exception as exc:
        return {"ok": False, "ip": ip, "error": str(exc), "source": "proxycheck.io"}

    if payload.get("status") != "ok":
        return {
            "ok": False,
            "ip": ip,
            "error": str(payload.get("message") or payload.get("status") or "unknown response"),
            "source": "proxycheck.io",
        }

    details = payload.get(ip)
    if not isinstance(details, dict):
        return {"ok": False, "ip": ip, "error": "IP detail tidak ditemukan di response", "source": "proxycheck.io"}

    risk = details.get("risk", "")
    return {
        "ok": True,
        "ip": ip,
        "risk": risk,
        "proxy": details.get("proxy", ""),
        "type": details.get("type", ""),
        "provider": details.get("provider") or details.get("organisation") or "",
        "asn": details.get("asn", ""),
        "country": details.get("country", ""),
        "isocode": details.get("isocode", ""),
        "source": "proxycheck.io",
        "error": "",
    }


def fraud_risk_level(risk):
    try:
        score = int(risk)
    except (TypeError, ValueError):
        return "unknown"
    if score >= 75:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def format_fraud_score(result):
    if not result.get("ok"):
        return "\n".join(
            [
                "Fraud score: <code>unavailable</code>",
                f"Fraud score error: <code>{escape(result.get('error', 'unknown error'))}</code>",
            ]
        )

    country = result.get("country", "")
    isocode = result.get("isocode", "")
    country_text = f"{country} ({isocode})" if country and isocode else country or isocode or "-"
    provider = result.get("provider") or "-"
    asn = result.get("asn") or "-"
    proxy = result.get("proxy") or "-"
    proxy_type = result.get("type") or "-"
    risk = result.get("risk", "unknown")
    level = fraud_risk_level(risk)
    return "\n".join(
        [
            f"Fraud score: <code>{escape(risk)}/100</code> (<code>{escape(level)}</code>)",
            f"Proxy/VPN flag: <code>{escape(proxy)}</code>",
            f"IP type: <code>{escape(proxy_type)}</code>",
            f"Provider: <code>{escape(provider)}</code>",
            f"ASN: <code>{escape(asn)}</code>",
            f"Country: <code>{escape(country_text)}</code>",
            f"Source: <code>{escape(result.get('source', 'proxycheck.io'))}</code>",
        ]
    )


def fetch_spider_credits(env, timeout=12):
    api_key = env.get("SPIDER_API_KEY", "")
    if not api_key:
        return {"ok": False, "error": "SPIDER_API_KEY belum dikonfigurasi", "source": "spider.cloud"}

    request = urllib.request.Request(
        SPIDER_CREDITS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "spider-bridge-bot/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(65536).decode("utf-8", errors="replace"))
    except Exception as exc:
        return {"ok": False, "error": str(exc), "source": "spider.cloud"}

    return {"ok": True, "payload": payload, "source": "spider.cloud"}


def flatten_balance_payload(value, prefix="", depth=0):
    if depth > 2:
        return {}
    if isinstance(value, dict):
        flattened = {}
        for key, item in value.items():
            child_key = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(item, (dict, list)):
                flattened.update(flatten_balance_payload(item, child_key, depth + 1))
            elif item is not None:
                flattened[child_key] = item
        return flattened
    if isinstance(value, list):
        flattened = {}
        for index, item in enumerate(value[:10]):
            child_key = f"{prefix}[{index}]" if prefix else f"[{index}]"
            if isinstance(item, (dict, list)):
                flattened.update(flatten_balance_payload(item, child_key, depth + 1))
            elif item is not None:
                flattened[child_key] = item
        return flattened
    return {prefix or "value": value}


def format_spider_credits(result):
    if not result.get("ok"):
        return "\n".join(
            [
                "Spider balance: <code>unavailable</code>",
                f"Spider balance error: <code>{escape(result.get('error', 'unknown error'))}</code>",
            ]
        )

    payload = result.get("payload")
    flattened = flatten_balance_payload(payload)
    priority_words = ("credit", "balance", "remain", "available", "used", "usage", "quota", "limit")
    selected = [
        (key, value)
        for key, value in flattened.items()
        if any(word in key.lower() for word in priority_words)
    ]
    if not selected:
        compact = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return "\n".join(
            [
                "Spider balance: <code>OK</code>",
                f"Response: <code>{escape(compact[:900])}</code>",
                f"Source: <code>{escape(result.get('source', 'spider.cloud'))}</code>",
            ]
        )

    lines = ["Spider balance: <code>OK</code>"]
    for key, value in selected[:8]:
        lines.append(f"{escape(key)}: <code>{escape(value)}</code>")
    lines.append(f"Source: <code>{escape(result.get('source', 'spider.cloud'))}</code>")
    return "\n".join(lines)


def sanitize_text(text, env):
    sanitized = str(text)
    replacements = [
        env.get("SPIDER_API_KEY", ""),
        env.get("LOCAL_PROXY_PASS", ""),
        spider_auth_header(env),
        urllib.parse.quote(env.get("SPIDER_API_KEY", ""), safe=""),
        urllib.parse.quote(env.get("LOCAL_PROXY_PASS", ""), safe=""),
        urllib.parse.quote(spider_password(env), safe=""),
    ]
    for secret in replacements:
        if secret:
            sanitized = sanitized.replace(secret, "<redacted>")
    return sanitized


def read_http_response(sock, max_bytes=65536):
    chunks = []
    total = 0
    while total < max_bytes:
        chunk = sock.recv(min(8192, max_bytes - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def read_http_headers(sock, max_bytes=32768):
    data = b""
    while b"\r\n\r\n" not in data and len(data) < max_bytes:
        chunk = sock.recv(min(4096, max_bytes - len(data)))
        if not chunk:
            break
        data += chunk
    return data


def parse_http_response(data):
    header_bytes, _, body = data.partition(b"\r\n\r\n")
    header_text = header_bytes.decode("iso-8859-1", errors="replace")
    status_line = header_text.splitlines()[0] if header_text.splitlines() else ""
    match = re.match(r"HTTP/\S+\s+(\d+)\s*(.*)", status_line)
    code = int(match.group(1)) if match else 0
    reason = match.group(2).strip() if match else status_line
    body_text = body.decode("utf-8", errors="replace").strip()
    return {"code": code, "reason": reason, "status_line": status_line, "body": body_text}


def extract_ip_from_body(body):
    if not body:
        return ""
    try:
        payload = json.loads(body)
        ip = str(payload.get("ip", "")).strip()
        if ip:
            return ip
    except json.JSONDecodeError:
        pass

    match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", body)
    return match.group(0) if match else ""


def is_global_ip(value):
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return False


def open_spider_proxy_socket(env, timeout=12):
    host = env.get("SPIDER_UPSTREAM_HOST", "proxy.spider.cloud")
    port = int(upstream_port(env))
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    if upstream_scheme(env) == "https":
        context = ssl.create_default_context()
        sock = context.wrap_socket(sock, server_hostname=host)
        sock.settimeout(timeout)
    return sock


def direct_spider_http_check(env):
    failures = []
    for label, url in HTTP_IP_CHECK_URLS:
        sock = None
        try:
            parsed_url = urllib.parse.urlsplit(url)
            target_host = parsed_url.netloc
            target_path = urllib.parse.urlunsplit(("", "", parsed_url.path or "/", parsed_url.query, ""))
            request_target = f"http://{target_host}{target_path}"

            sock = open_spider_proxy_socket(env)
            request = (
                f"GET {request_target} HTTP/1.1\r\n"
                f"Host: {target_host}\r\n"
                f"Proxy-Authorization: {spider_auth_header(env)}\r\n"
                "User-Agent: spider-bridge-bot/1.0\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            sock.sendall(request.encode("ascii"))
            parsed = parse_http_response(read_http_response(sock))
            ip = extract_ip_from_body(parsed["body"])
            ok = parsed["code"] == 200 and bool(ip) and is_global_ip(ip)
            if ok:
                return {
                    "ok": True,
                    "ip": ip,
                    "status": parsed["status_line"],
                    "error": "",
                    "target": label,
                }
            if ip and not is_global_ip(ip):
                error = f"non-public IP returned: {ip}"
            else:
                error = f"{parsed['status_line']} {parsed['body'][:240]}".strip()
            failures.append(f"{label}: {sanitize_text(error, env)}")
        except Exception as exc:
            failures.append(f"{label}: {sanitize_text(str(exc), env)}")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    return {
        "ok": False,
        "ip": "",
        "status": "",
        "error": " | ".join(failures)[-1500:],
        "target": "all",
    }


def direct_spider_connect_check(env):
    failures = []
    for label, url in HTTPS_IP_CHECK_URLS:
        sock = None
        try:
            parsed_url = urllib.parse.urlsplit(url)
            target_host = parsed_url.hostname or parsed_url.netloc
            target_port = parsed_url.port or 443

            sock = open_spider_proxy_socket(env)
            request = (
                f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
                f"Host: {target_host}:{target_port}\r\n"
                f"Proxy-Authorization: {spider_auth_header(env)}\r\n"
                "User-Agent: spider-bridge-bot/1.0\r\n"
                "\r\n"
            )
            sock.sendall(request.encode("ascii"))
            parsed = parse_http_response(read_http_headers(sock))
            if parsed["code"] == 200:
                return {
                    "ok": True,
                    "ip": "",
                    "status": parsed["status_line"],
                    "error": "",
                    "target": label,
                }
            error = f"{parsed['status_line']} {parsed['body'][:240]}".strip()
            failures.append(f"{label}: {sanitize_text(error, env)}")
        except Exception as exc:
            failures.append(f"{label}: {sanitize_text(str(exc), env)}")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    return {
        "ok": False,
        "ip": "",
        "status": "",
        "error": " | ".join(failures)[-1500:],
        "target": "all",
    }


def recv_exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("socket closed during SOCKS5 handshake")
        data += chunk
    return data


def socks5_status_text(code):
    statuses = {
        0x00: "succeeded",
        0x01: "general SOCKS server failure",
        0x02: "connection not allowed by ruleset",
        0x03: "network unreachable",
        0x04: "host unreachable",
        0x05: "connection refused",
        0x06: "TTL expired",
        0x07: "command not supported",
        0x08: "address type not supported",
    }
    return statuses.get(code, f"unknown SOCKS5 status {code}")


def open_spider_socks5_tunnel(env, target_host, target_port, timeout=15):
    upstream_host = env.get("SPIDER_UPSTREAM_HOST", "proxy.spider.cloud")
    upstream_port = int(upstream_port(env))
    sock = socket.create_connection((upstream_host, upstream_port), timeout=timeout)
    sock.settimeout(timeout)

    username = env.get("SPIDER_API_KEY", "").encode("utf-8")
    password = spider_password(env).encode("utf-8")
    if len(username) > 255 or len(password) > 255:
        sock.close()
        raise ValueError("SOCKS5 username/password is too long")

    sock.sendall(b"\x05\x01\x02")
    method = recv_exact(sock, 2)
    if method[0] != 0x05:
        sock.close()
        raise ConnectionError(f"invalid SOCKS5 method response: {method!r}")
    if method[1] == 0xFF:
        sock.close()
        raise PermissionError("SOCKS5 upstream rejected available auth methods")
    if method[1] == 0x02:
        sock.sendall(b"\x01" + bytes([len(username)]) + username + bytes([len(password)]) + password)
        auth = recv_exact(sock, 2)
        if auth != b"\x01\x00":
            sock.close()
            raise PermissionError(f"SOCKS5 authentication failed: {auth!r}")
    elif method[1] != 0x00:
        sock.close()
        raise ConnectionError(f"unsupported SOCKS5 auth method selected: {method[1]}")

    encoded_host = target_host.encode("idna")
    if len(encoded_host) > 255:
        sock.close()
        raise ValueError("target hostname is too long for SOCKS5")

    request = b"\x05\x01\x00\x03" + bytes([len(encoded_host)]) + encoded_host + int(target_port).to_bytes(2, "big")
    sock.sendall(request)
    header = recv_exact(sock, 4)
    if header[0] != 0x05:
        sock.close()
        raise ConnectionError(f"invalid SOCKS5 connect response: {header!r}")
    if header[1] != 0x00:
        status = socks5_status_text(header[1])
        sock.close()
        raise ConnectionError(f"SOCKS5 connect failed: {status}")

    atyp = header[3]
    if atyp == 0x01:
        recv_exact(sock, 4)
    elif atyp == 0x03:
        length = recv_exact(sock, 1)[0]
        recv_exact(sock, length)
    elif atyp == 0x04:
        recv_exact(sock, 16)
    else:
        sock.close()
        raise ConnectionError(f"invalid SOCKS5 bind address type: {atyp}")
    recv_exact(sock, 2)
    return sock


def direct_spider_socks5_fetch_ip(env, candidates, timeout=20):
    failures = []
    for label, url in candidates:
        sock = None
        try:
            parsed_url = urllib.parse.urlsplit(url)
            target_host = parsed_url.hostname or parsed_url.netloc
            target_port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
            target_path = urllib.parse.urlunsplit(("", "", parsed_url.path or "/", parsed_url.query, ""))

            sock = open_spider_socks5_tunnel(env, target_host, target_port, timeout=timeout)
            if parsed_url.scheme == "https":
                context = ssl.create_default_context()
                sock = context.wrap_socket(sock, server_hostname=target_host)
                sock.settimeout(timeout)

            request = (
                f"GET {target_path} HTTP/1.1\r\n"
                f"Host: {target_host}\r\n"
                "User-Agent: spider-bridge-bot/1.0\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            sock.sendall(request.encode("ascii"))
            parsed = parse_http_response(read_http_response(sock))
            ip = extract_ip_from_body(parsed["body"])
            ok = parsed["code"] == 200 and bool(ip) and is_global_ip(ip)
            if ok:
                return {
                    "ok": True,
                    "ip": ip,
                    "status": parsed["status_line"],
                    "error": "",
                    "target": label,
                }
            if ip and not is_global_ip(ip):
                error = f"non-public IP returned: {ip}"
            else:
                error = f"{parsed['status_line']} {parsed['body'][:240]}".strip()
            failures.append(f"{label}: {sanitize_text(error, env)}")
        except Exception as exc:
            failures.append(f"{label}: {sanitize_text(str(exc), env)}")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    return {
        "ok": False,
        "ip": "",
        "status": "",
        "error": " | ".join(failures)[-1500:],
        "target": "all",
    }


def direct_spider_checks(env):
    if upstream_scheme(env) == "socks5":
        return {
            "connect": direct_spider_socks5_fetch_ip(env, HTTPS_IP_CHECK_URLS),
            "http": direct_spider_socks5_fetch_ip(env, HTTP_IP_CHECK_URLS),
        }

    return {
        "connect": direct_spider_connect_check(env),
        "http": direct_spider_http_check(env),
    }


def proxy_live_check(env, include_direct=True):
    proxy_handler = urllib.request.ProxyHandler(
        {
            "http": local_proxy_url(env),
            "https": local_proxy_url(env),
        }
    )
    proxy_opener = urllib.request.build_opener(proxy_handler)
    https = fetch_first_ip(proxy_opener, HTTPS_IP_CHECK_URLS, timeout=25)
    http = fetch_first_ip(proxy_opener, HTTP_IP_CHECK_URLS, timeout=25)

    direct = None
    if include_direct:
        direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        direct = fetch_first_ip(direct_opener, HTTPS_IP_CHECK_URLS, timeout=12)

    return {"https": https, "http": http, "direct": direct}


def fetch_url_via_local_proxy(env, url):
    proxy_handler = urllib.request.ProxyHandler(
        {
            "http": local_proxy_url(env),
            "https": local_proxy_url(env),
        }
    )
    opener = urllib.request.build_opener(proxy_handler)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "spider-bridge-bot/1.0"},
    )

    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(600).decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status": getattr(response, "status", response.getcode()),
                "url": response.geturl(),
                "content_type": response.headers.get("Content-Type", ""),
                "body": body.strip(),
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(600).decode("utf-8", errors="replace") if exc.fp else ""
        return {
            "ok": False,
            "status": exc.code,
            "url": url,
            "content_type": exc.headers.get("Content-Type", "") if exc.headers else "",
            "body": body.strip(),
            "error": f"HTTP Error {exc.code}: {exc.reason}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "",
            "url": url,
            "content_type": "",
            "body": "",
            "error": str(exc),
        }


def format_url_test(result):
    status = result["status"] if result["status"] != "" else "n/a"
    lines = [
        "<b>URL Test</b>",
        f"Result: <code>{'OK' if result['ok'] else 'FAIL'}</code>",
        f"HTTP status: <code>{escape(status)}</code>",
        f"URL: <code>{escape(result['url'])}</code>",
    ]
    if result["content_type"]:
        lines.append(f"Content-Type: <code>{escape(result['content_type'])}</code>")
    if result["error"]:
        lines.append(f"Error: <code>{escape(result['error'])}</code>")
    if result["body"]:
        preview = re.sub(r"\s+", " ", result["body"]).strip()[:280]
        lines.append(f"Preview: <code>{escape(preview)}</code>")
    return "\n".join(lines)


def format_single_check(label, result):
    if result["ok"] and result["ip"]:
        target = f" via {result.get('target')}" if result.get("target") else ""
        return [
            f"{label}: ✅ <code>OK</code>",
            f"{label} exit IP{escape(target)}: <code>{escape(result['ip'])}</code>",
        ]
    if result["ok"]:
        return [
            f"{label}: ⚠️ <code>UNKNOWN</code>",
            f"{label} response: <code>{escape(result['raw'] or result['error'] or 'empty response')}</code>",
        ]
    return [
        f"{label}: ❌ <code>FAIL</code>",
        f"{label} error: <code>{escape(result['error'])}</code>",
    ]


def format_live_check(check, env):
    https = check["https"]
    http = check["http"]
    direct = check.get("direct")

    lines = []
    lines.extend(format_single_check("HTTPS CONNECT check", https))
    lines.extend(format_single_check("HTTP check", http))

    if direct:
        if direct["ok"] and direct["ip"]:
            lines.append(f"Direct VPS IP: <code>{escape(direct['ip'])}</code>")
            comparison_ip = https["ip"] if https["ok"] and https["ip"] else http["ip"]
            if comparison_ip:
                if comparison_ip == direct["ip"]:
                    lines.append("Exit comparison: <code>SAME_AS_VPS</code>")
                else:
                    lines.append("Exit comparison: <code>DIFFERENT_FROM_VPS</code>")
        elif not direct["ok"]:
            lines.append(f"Direct VPS IP: <code>unavailable ({escape(direct['error'])})</code>")

    if not https["ok"] and "403" in https["error"]:
        lines.append("Hint: <code>Spider menolak CONNECT ke target IP-check. Coba target website real atau ganti pool/country.</code>")
    if ("502" in https["error"] or "502" in http["error"]) and not (https["ok"] or http["ok"]):
        lines.append(f"Hint: <code>502 berasal dari jalur {escape(proxy_service_label(env))} ke Spider. Jalankan /diag.</code>")
    if "Blocked by network blocker" in https["error"] or "Blocked by network blocker" in http["error"]:
        lines.append("Hint: <code>Target IP-check diblok oleh Spider/network blocker. Status mencoba fallback target lain.</code>")

    return "\n".join(lines)


def format_check_result(label, result):
    if result["ok"]:
        detail = result["ip"] or result.get("status") or "OK"
        suffix = "exit IP" if result["ip"] else "status"
        target = f" via {result.get('target')}" if result.get("target") else ""
        return [f"{label}: ✅ <code>OK</code>", f"{label} {suffix}{escape(target)}: <code>{escape(detail)}</code>"]

    detail = result.get("error") or result.get("status") or "unknown error"
    return [
        f"{label}: ❌ <code>FAIL</code>",
        f"{label} error: <code>{escape(detail)}</code>",
    ]


def tail_file(path, env, max_lines=24, max_chars=1500):
    if not os.path.exists(path):
        return f"{path} tidak ada."

    try:
        with open(path, "rb") as handle:
            try:
                handle.seek(-65536, os.SEEK_END)
            except OSError:
                handle.seek(0)
            data = handle.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return f"Tidak bisa membaca {path}: {exc}"

    lines = data.splitlines()[-max_lines:]
    text = "\n".join(lines)[-max_chars:]
    return sanitize_text(text or "(empty)", env)


def journal_tail(unit, env, max_lines=40, max_chars=1500):
    try:
        result = subprocess.run(
            ["journalctl", "-u", unit, "-n", str(max_lines), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        return f"Tidak bisa membaca journal {unit}: {exc}"

    output = (result.stdout + "\n" + result.stderr).strip()
    return sanitize_text(output[-max_chars:] or "(empty)", env)


def diagnostic_sections(env):
    local_check = proxy_live_check(env, include_direct=True)
    direct_check = direct_spider_checks(env)
    engine = bridge_engine(env)
    service_label = proxy_service_label(env)
    service_name = proxy_service_name(env)
    local_label = f"Via local {service_label}"
    direct_https_label = "Direct Spider HTTPS via SOCKS5" if upstream_scheme(env) == "socks5" else "Direct Spider HTTPS CONNECT"
    direct_http_label = "Direct Spider HTTP via SOCKS5" if upstream_scheme(env) == "socks5" else "Direct Spider HTTP"

    summary = [
        "<b>Spider Bridge Diagnostics</b>",
        "",
        f"Engine: <code>{escape(engine)}</code>",
        f"{service_label}: <code>{escape(service_state(service_name))}</code>",
        f"Bot: <code>{escape(service_state('spider-bridge-bot'))}</code>",
        f"Spider upstream: <code>{escape(upstream_endpoint(env))}</code>",
        f"Spider password params: <code>{escape(spider_password(env))}</code>",
        "",
        f"<b>{escape(local_label)}</b>",
        format_live_check(local_check, env),
        "",
        "<b>Direct to Spider upstream</b>",
    ]
    summary.extend(format_check_result(direct_https_label, direct_check["connect"]))
    summary.extend(format_check_result(direct_http_label, direct_check["http"]))

    local_ok = local_check["https"]["ok"] or local_check["http"]["ok"]
    direct_ok = direct_check["connect"]["ok"] or direct_check["http"]["ok"]
    if direct_ok and not local_ok:
        recommendation = f"Cek service <code>{escape(service_name)}</code> dan koneksi VPS ke parent Spider."
        summary.extend(
            [
                "",
                f"Diagnosis: <code>Spider direct OK, {escape(service_label)} path FAIL.</code>",
                recommendation,
            ]
        )
    elif not direct_ok:
        blocked = "Blocked by network blocker" in direct_check["connect"]["error"] or "Blocked by network blocker" in direct_check["http"]["error"]
        summary.extend(
            [
                "",
                "Diagnosis: <code>Direct Spider FAIL.</code>",
                (
                    "Target test diblok oleh Spider/network blocker. Coba target real yang ingin Anda akses, "
                    "atau ganti pool/country."
                    if blocked
                    else "Fokus cek API key Spider, saldo/quota, pool/country, atau akses outbound VPS ke Spider."
                ),
            ]
        )
    else:
        summary.extend(["", "Diagnosis: <code>Proxy path OK.</code>"])

    gost_tail = escape(journal_tail(GOST_SERVICE_NAME, env, max_lines=35, max_chars=1400))
    return [
        "\n".join(summary),
        f"<b>GOST journal tail</b>\n<code>{gost_tail}</code>",
    ]


def diagnostic_text(env):
    return "\n\n".join(diagnostic_sections(env))


def status_text(env):
    country = env.get("SPIDER_COUNTRY_CODE") or "default"
    check = proxy_live_check(env, include_direct=True)
    exit_ip, exit_source = status_exit_ip(check)
    fraud = fetch_fraud_score(exit_ip) if exit_ip else {"ok": False, "ip": "", "error": "exit IP belum tersedia"}
    balance = fetch_spider_credits(env)
    engine = bridge_engine(env)
    service_label = proxy_service_label(env)
    service_name = proxy_service_name(env)
    proxy = proxy_line(env)
    exit_line = f"{exit_ip} via {exit_source}" if exit_ip else "unavailable"
    return f"""🕷️ <b>Spider Bridge Status</b>

⚙️ Engine: <code>{escape(engine)}</code>
🔌 {service_label}: <code>{escape(service_state(service_name))}</code>
🤖 Bot: <code>{escape(service_state("spider-bridge-bot"))}</code>

{format_live_check(check, env)}

<blockquote>📋 Proxy siap dipakai
{escape(proxy)}</blockquote>

🌐 Exit IP: <code>{escape(exit_line)}</code>
🛡️ <b>Fraud / Risk</b>
{format_fraud_score(fraud)}

💳 <b>Spider Balance</b>
{format_spider_credits(balance)}

🔐 Local user: <code>{escape(env.get("LOCAL_PROXY_USER", ""))}</code>
🧬 Spider pool: <code>{escape(env.get("SPIDER_PROXY_TYPE", "residential"))}</code>
📍 Spider country: <code>{escape(country)}</code>
🏷️ Spider country param: <code>{escape(env.get("SPIDER_COUNTRY_PARAM", "country_code"))}</code>
"""


def user_status_text(env, user_id, record):
    cfg = user_config_env(env, record)
    country = cfg.get("SPIDER_COUNTRY_CODE") or "default"
    check = proxy_live_check(cfg, include_direct=True)
    exit_ip, exit_source = status_exit_ip(check)
    fraud = fetch_fraud_score(exit_ip) if exit_ip else {"ok": False, "ip": "", "error": "exit IP belum tersedia"}
    proxy = proxy_line(cfg)
    exit_line = f"{exit_ip} via {exit_source}" if exit_ip else "unavailable"
    return f"""<b>Spider Bridge User Status</b>

Telegram ID: <code>{escape(user_id)}</code>
Service: <code>{escape(service_state(user_service_name(user_id)))}</code>
Expired: <code>{escape(format_expiry(record.get("expires_at", 0)))}</code>

{format_live_check(check, cfg)}

<blockquote>Proxy Anda
{escape(proxy)}</blockquote>

Exit IP: <code>{escape(exit_line)}</code>
<b>Fraud / Risk</b>
{format_fraud_score(fraud)}

Local user: <code>{escape(cfg.get("LOCAL_PROXY_USER", ""))}</code>
Spider pool: <code>{escape(cfg.get("SPIDER_PROXY_TYPE", "residential"))}</code>
Spider country: <code>{escape(country)}</code>
"""


PUBLIC_COMMANDS = [
    {"command": "start", "description": "Start bot"},
    {"command": "help", "description": "Show help"},
    {"command": "whoami", "description": "Show your Telegram user ID"},
    {"command": "claim", "description": "Claim first admin"},
]

USER_COMMANDS = [
    {"command": "status", "description": "Show your proxy status"},
    {"command": "showproxy", "description": "Show your proxy"},
    {"command": "countries", "description": "Choose Spider location"},
    {"command": "refreshcountries", "description": "Refresh Spider locations"},
    {"command": "pools", "description": "Choose Spider proxy pool"},
    {"command": "setcountry", "description": "Set your country"},
    {"command": "setproxy", "description": "Set your pool"},
    {"command": "test", "description": "Test your proxy"},
    {"command": "testurl", "description": "Test a URL through proxy"},
    {"command": "whoami", "description": "Show your Telegram user ID"},
]

ADMIN_COMMANDS = [
        {"command": "status", "description": "Show bridge status"},
        {"command": "countries", "description": "Choose Spider location"},
        {"command": "refreshcountries", "description": "Refresh Spider locations"},
        {"command": "pools", "description": "Choose Spider proxy pool"},
        {"command": "setcountry", "description": "Set country code"},
        {"command": "setcountryparam", "description": "Set country or country_code param"},
        {"command": "setproxy", "description": "Set Spider pool"},
        {"command": "setengine", "description": "Ensure GOST engine"},
        {"command": "setupstream", "description": "Ensure Spider SOCKS5 upstream"},
        {"command": "showproxy", "description": "Show ip:port:user:pass"},
        {"command": "test", "description": "Test local proxy"},
        {"command": "testurl", "description": "Test a real URL through local proxy"},
        {"command": "diag", "description": "Diagnose bridge and Spider upstream"},
        {"command": "balance", "description": "Show Spider balance"},
        {"command": "apply", "description": "Restart proxy with current config"},
        {"command": "whoami", "description": "Show your Telegram user ID"},
        {"command": "addadmin", "description": "Add bot admin"},
        {"command": "deladmin", "description": "Remove bot admin"},
        {"command": "adduser", "description": "Add rental proxy user"},
        {"command": "deluser", "description": "Remove rental proxy user"},
        {"command": "listuser", "description": "List rental proxy users"},
    ]

ADMIN_ONLY_COMMANDS = {
    "/diag",
    "/balance",
    "/apply",
    "/setlocaluser",
    "/setlocalpass",
    "/setport",
    "/setengine",
    "/setupstream",
    "/setcountryparam",
    "/addadmin",
    "/deladmin",
    "/adduser",
    "/deluser",
    "/listuser",
}


def set_commands_for_chat(token, chat, commands):
    payload = {
        "commands": commands,
        "scope": {
            "type": "chat",
            "chat_id": int(chat),
        },
    }
    telegram_api(token, "setMyCommands", payload, timeout=10)


def set_my_commands(token, env):
    telegram_api(token, "setMyCommands", {"commands": PUBLIC_COMMANDS}, timeout=10)

    users = read_users()
    for user_id, record in users.items():
        if not is_user_active(record):
            continue
        try:
            set_commands_for_chat(token, user_id, USER_COMMANDS)
        except Exception as exc:
            log(f"Unable to set user commands for {user_id}: {exc}")

    for admin_id in parse_admin_ids(env.get("TELEGRAM_ADMIN_IDS", "")):
        try:
            set_commands_for_chat(token, admin_id, ADMIN_COMMANDS)
        except Exception as exc:
            log(f"Unable to set admin commands for {admin_id}: {exc}")


def claim_admin(token, update, env, args):
    chat = chat_id(update)
    user = sender_id(update)
    setup_token = env.get("SETUP_TOKEN", "")

    if not setup_token:
        send_message(token, chat, "Setup token sudah tidak aktif.")
        return

    if not args or args[0] != setup_token:
        send_message(token, chat, "Token salah. Jalankan <code>/whoami</code> atau cek output installer di VPS.")
        return

    ids = parse_admin_ids(env.get("TELEGRAM_ADMIN_IDS", ""))
    ids.add(int(user))
    env["TELEGRAM_ADMIN_IDS"] = ",".join(str(item) for item in sorted(ids))
    env["SETUP_TOKEN"] = ""
    save_env(env)
    try:
        set_commands_for_chat(token, user, ADMIN_COMMANDS)
    except Exception as exc:
        log(f"Unable to set claimed admin commands for {user}: {exc}")
    send_message(token, chat, "Admin berhasil diklaim. Jalankan <code>/status</code>.")


def require_admin_or_reply(token, update, env):
    if is_admin(env, update):
        return True

    chat = chat_id(update)
    user = sender_id(update)
    message = f"Akses ditolak. Telegram user ID Anda: <code>{escape(user)}</code>."
    if env.get("SETUP_TOKEN"):
        message += "\nJika ini setup pertama, gunakan <code>/claim TOKEN_DARI_INSTALLER</code>."
    send_message(token, chat, message)
    return False


def handle_set_country(token, chat, env, value):
    normalized = value.upper()
    if normalized in {"OFF", "DEFAULT", "NONE", "-"}:
        normalized = ""

    if not valid_country(normalized):
        send_message(token, chat, "Country harus ISO 2 huruf, contoh <code>US</code>, <code>ID</code>, atau <code>off</code>.")
        return

    if normalized:
        countries, source, error = get_spider_countries(force_refresh=False)
        if countries and normalized not in countries:
            send_message(
                token,
                chat,
                f"Country <code>{escape(normalized)}</code> tidak ada di daftar Spider saat ini. "
                f"Jalankan <code>/refreshcountries</code> atau cek kode country-nya.",
            )
            return
        if not countries and error:
            send_message(
                token,
                chat,
                f"Tidak bisa validasi daftar country dari Spider ({escape(error)}). Config tetap dicoba.",
            )

    env["SPIDER_COUNTRY_CODE"] = normalized
    save_env(env)
    ok, output = run_apply()
    if ok:
        send_message(token, chat, f"Country diubah ke <code>{escape(normalized or 'default')}</code>.\n<code>{escape(output)}</code>")
    else:
        send_message(token, chat, f"Gagal apply config:\n<code>{escape(output)}</code>")


def handle_set_country_param(token, chat, env, value):
    normalized = value.lower()
    if not valid_country_param(normalized):
        send_message(token, chat, "Country param harus <code>country_code</code> atau <code>country</code>.")
        return

    env["SPIDER_COUNTRY_PARAM"] = normalized
    save_env(env)
    ok, output = run_apply()
    if ok:
        send_message(token, chat, f"Country param diubah ke <code>{escape(normalized)}</code>.\n<code>{escape(output)}</code>")
    else:
        send_message(token, chat, f"Gagal apply config:\n<code>{escape(output)}</code>")


def handle_set_proxy(token, chat, env, value):
    normalized = value.lower()
    if normalized == "datacenter":
        normalized = "isp"

    if normalized not in PROXY_TYPES:
        send_message(token, chat, "Pool tidak didukung. Gunakan <code>/pools</code> untuk pilihan.")
        return

    env["SPIDER_PROXY_TYPE"] = normalized
    save_env(env)
    ok, output = run_apply()
    if ok:
        send_message(token, chat, f"Pool diubah ke <code>{escape(normalized)}</code>.\n<code>{escape(output)}</code>")
    else:
        send_message(token, chat, f"Gagal apply config:\n<code>{escape(output)}</code>")


def handle_set_engine(token, chat, env, args):
    if not args:
        send_message(token, chat, "Contoh: <code>/setengine gost</code>")
        return

    engine = args[0].lower()
    if not valid_bridge_engine(engine):
        send_message(token, chat, "Engine hanya <code>gost</code>. Squid sudah dihapus dari installer ini.")
        return

    env["BRIDGE_ENGINE"] = "gost"
    env["SPIDER_UPSTREAM_SCHEME"] = "socks5"
    env["SPIDER_UPSTREAM_PORT"] = default_upstream_port("socks5")

    send_message(
        token,
        chat,
        f"Menerapkan engine <code>gost</code> dengan upstream <code>{escape(upstream_endpoint(env))}</code>...",
    )
    save_env(env)

    ok, output = run_apply()
    if ok:
        send_message(
            token,
            chat,
            "Engine diubah ke <code>gost</code>.\n"
            f"<code>{escape(output)}</code>\n\n"
            "Jalankan <code>/status</code> atau <code>/testurl https://whoer.net</code>.",
        )
    else:
        send_message(token, chat, f"Gagal apply engine:\n<code>{escape(output)}</code>")


def handle_set_upstream(token, chat, env, args):
    if not args:
        send_message(token, chat, "Contoh: <code>/setupstream socks5</code>")
        return

    scheme = args[0].lower()
    if not valid_upstream_scheme(scheme):
        send_message(token, chat, "Upstream scheme hanya <code>socks5</code>. GOST bridge memakai Spider SOCKS5 <code>8887</code>.")
        return

    port = args[1] if len(args) > 1 else default_upstream_port(scheme)
    if not valid_port(port):
        send_message(token, chat, "Port upstream harus angka 1-65535.")
        return
    if port in {"8888", "8889"}:
        send_message(token, chat, "Port upstream Spider untuk GOST harus <code>8887</code>, bukan <code>8888</code> atau <code>8889</code>.")
        return

    env["SPIDER_UPSTREAM_SCHEME"] = scheme
    env["SPIDER_UPSTREAM_PORT"] = port
    env["BRIDGE_ENGINE"] = "gost"
    send_message(
        token,
        chat,
        f"Menerapkan upstream Spider ke <code>{escape(upstream_endpoint(env))}</code> dengan engine <code>{escape(bridge_engine(env))}</code>...",
    )
    save_env(env)

    ok, output = run_apply()
    if ok:
        send_message(
            token,
            chat,
            f"Upstream Spider diubah ke <code>{escape(upstream_endpoint(env))}</code>.\n"
            f"<code>{escape(output)}</code>\n\n"
            "Jalankan <code>/status</code> untuk melihat exit IP live.",
        )
    else:
        send_message(token, chat, f"Gagal apply upstream:\n<code>{escape(output)}</code>")


def handle_test(token, chat, env):
    check = proxy_live_check(env, include_direct=True)
    send_message(token, chat, f"<b>Proxy Test</b>\n\n{format_live_check(check, env)}")


def handle_test_url(token, chat, env, args):
    if not args or not valid_test_url(args[0]):
        send_message(token, chat, "Contoh: <code>/testurl https://example.com</code>")
        return

    url = args[0]
    send_message(token, chat, f"Testing URL lewat proxy lokal:\n<code>{escape(url)}</code>")
    result = fetch_url_via_local_proxy(env, url)
    send_message(token, chat, format_url_test(result))


def handle_diag(token, chat, env):
    send_message(token, chat, f"Menjalankan diagnosa {escape(proxy_service_label(env))} dan Spider upstream...")
    for section in diagnostic_sections(env):
        send_message(token, chat, section)


def countries_text(countries, source, error):
    note = f"Daftar country dari <code>{escape(source)}</code>. Total: <code>{len(countries)}</code>."
    if error:
        note += f"\nCache dipakai karena refresh gagal: <code>{escape(error)}</code>"
    return note


def handle_countries(token, chat, page=0, force_refresh=False, edit_message_id=None):
    countries, source, error = get_spider_countries(force_refresh=force_refresh)
    if not countries:
        text = (
            f"Tidak bisa mengambil daftar country dari Spider.\n<code>{escape(error or 'unknown error')}</code>\n\n"
            "Anda masih bisa set manual, contoh <code>/setcountry US</code>."
        )
        if edit_message_id:
            edit_message(token, chat, edit_message_id, text)
        else:
            send_message(token, chat, text)
        return

    text = countries_text(countries, source, error)
    keyboard = build_countries_keyboard(countries, page)
    if edit_message_id:
        edit_message(token, chat, edit_message_id, text, keyboard)
    else:
        send_message(token, chat, text, keyboard)


def normalize_country_arg(value):
    normalized = (value or "").upper()
    if normalized in {"OFF", "DEFAULT", "NONE", "-"}:
        normalized = ""
    if not valid_country(normalized):
        raise ValueError("Country harus ISO 2 huruf, contoh US, ID, atau off.")
    return normalized


def parse_user_options(env, tokens):
    options = {
        "country": env.get("SPIDER_COUNTRY_CODE", "US"),
        "pool": env.get("SPIDER_PROXY_TYPE", "residential"),
        "country_param": env.get("SPIDER_COUNTRY_PARAM", "country_code"),
        "username": "",
        "password": "",
        "port": "",
    }

    for token in tokens:
        if "=" in token:
            key, value = token.split("=", 1)
            key = key.lower()
        else:
            key, value = "", token

        lower_value = value.lower()
        if key in {"country", "c"}:
            options["country"] = normalize_country_arg(value)
        elif key in {"pool", "proxy", "p"}:
            pool = lower_value
            if pool == "datacenter":
                pool = "isp"
            if pool not in PROXY_TYPES:
                raise ValueError("Pool tidak didukung. Gunakan residential, mobile, isp, atau default.")
            options["pool"] = pool
        elif key in {"user", "username", "u"}:
            if not valid_local_credential(value):
                raise ValueError("Username proxy harus 3-64 chars: A-Z a-z 0-9 . _ -")
            options["username"] = value
        elif key in {"pass", "password", "pw"}:
            if not valid_local_credential(value):
                raise ValueError("Password proxy harus 3-64 chars: A-Z a-z 0-9 . _ -")
            options["password"] = value
        elif key == "port":
            if not valid_port(value):
                raise ValueError("Port harus angka 1-65535.")
            options["port"] = value
        elif not key and lower_value in PROXY_TYPES:
            options["pool"] = lower_value
        elif not key and lower_value == "datacenter":
            options["pool"] = "isp"
        elif not key:
            options["country"] = normalize_country_arg(value)
        else:
            raise ValueError(f"Opsi tidak dikenal: {token}")

    options["country"] = normalize_country_arg(options["country"])
    if options["pool"] == "datacenter":
        options["pool"] = "isp"
    if options["pool"] not in PROXY_TYPES:
        raise ValueError("Pool tidak didukung.")
    if not valid_country_param(options["country_param"]):
        options["country_param"] = "country_code"
    return options


def validate_user_port_choice(env, users, user_id, port):
    if str(port) == str(env.get("LOCAL_PROXY_PORT", "3128")):
        raise ValueError("Port sudah dipakai proxy utama.")
    for other_id, record in users.items():
        if str(other_id) != str(user_id) and str(record.get("port", "")) == str(port):
            raise ValueError(f"Port sudah dipakai user {other_id}.")
    existing = users.get(str(user_id), {})
    if str(existing.get("port", "")) != str(port) and not port_available(port):
        raise ValueError(f"Port {port} sedang dipakai service lain.")


def format_user_summary(user_id, record):
    state = "active" if is_user_active(record) else "expired/disabled"
    return "\n".join(
        [
            f"<b>User</b> <code>{escape(user_id)}</code>",
            f"Proxy: <code>{escape(record.get('port', ''))}:{escape(record.get('username', ''))}:{escape(record.get('password', ''))}</code>",
            f"Country: <code>{escape(record.get('country') or 'default')}</code>",
            f"Pool: <code>{escape(record.get('pool', 'residential'))}</code>",
            f"Expired: <code>{escape(format_expiry(record.get('expires_at', 0)))}</code>",
            f"Sisa: <code>{escape(format_time_remaining(record.get('expires_at', 0)))}</code>",
            f"Status: <code>{escape(state)}</code>",
        ]
    )


def handle_add_user(token, chat, env, args):
    if len(args) < 2 or not args[0].isdigit():
        send_message(
            token,
            chat,
            "Contoh: <code>/adduser 123456789 30d country=SG pool=default</code>\n"
            "Expired bisa <code>12h</code>, <code>30d</code>, <code>2026-07-13</code>, atau <code>never</code>.",
        )
        return

    user_id = str(int(args[0]))
    try:
        expires_at = parse_expiry(args[1])
        options = parse_user_options(env, args[2:])
    except ValueError as exc:
        send_message(token, chat, f"Format /adduser salah: <code>{escape(exc)}</code>")
        return

    users = read_users()
    existing = users.get(user_id, {})
    username = options["username"] or existing.get("username") or random_local_credential("u", 11)
    password = options["password"] or existing.get("password") or random_local_credential("p", 14)
    port = options["port"] or existing.get("port") or allocate_user_port(env, users)

    try:
        validate_user_port_choice(env, users, user_id, port)
    except ValueError as exc:
        send_message(token, chat, f"Tidak bisa membuat user: <code>{escape(exc)}</code>")
        return

    now = int(time.time())
    record = {
        "telegram_id": user_id,
        "username": username,
        "password": password,
        "port": str(port),
        "country": options["country"],
        "pool": options["pool"],
        "country_param": options["country_param"],
        "expires_at": expires_at,
        "enabled": True,
        "created_at": int(existing.get("created_at", now) or now),
        "updated_at": now,
    }

    ok, output = apply_user_service(env, user_id, record)
    if not ok:
        send_message(token, chat, f"Gagal membuat service user:\n<code>{escape(output)}</code>")
        return

    users[user_id] = record
    write_users(users)
    try:
        set_commands_for_chat(token, user_id, USER_COMMANDS)
    except Exception as exc:
        log(f"Unable to set user commands for {user_id}: {exc}")
    cfg = user_config_env(env, record)
    proxy = proxy_line(cfg)
    send_message(
        token,
        chat,
        (
            "<b>User proxy aktif</b>\n"
            f"Telegram ID: <code>{escape(user_id)}</code>\n"
            f"Expired: <code>{escape(format_expiry(expires_at))}</code>\n"
            f"Country: <code>{escape(record['country'] or 'default')}</code>\n"
            f"Pool: <code>{escape(record['pool'])}</code>\n\n"
            f"<code>{escape(proxy)}</code>"
        ),
        proxy_copy_keyboard_for_text(proxy),
    )


def handle_del_user(token, chat, args):
    if not args or not args[0].isdigit():
        send_message(token, chat, "Contoh: <code>/deluser 123456789</code>")
        return

    user_id = str(int(args[0]))
    users = read_users()
    if user_id not in users:
        send_message(token, chat, "User tidak ditemukan.")
        return

    output = stop_user_service(user_id, remove_unit=True)
    users.pop(user_id, None)
    write_users(users)
    try:
        set_commands_for_chat(token, user_id, PUBLIC_COMMANDS)
    except Exception as exc:
        log(f"Unable to reset commands for deleted user {user_id}: {exc}")
    detail = f"\n<code>{escape(output)}</code>" if output else ""
    send_message(token, chat, f"User <code>{escape(user_id)}</code> dihapus.{detail}")


def handle_users(token, chat):
    users = read_users()
    if not users:
        send_message(token, chat, "Belum ada user rental.")
        return

    lines = ["<b>User rental</b>"]
    for user_id in sorted(users, key=lambda item: int(item)):
        lines.append("")
        lines.append(format_user_summary(user_id, users[user_id]))
    send_message(token, chat, "\n".join(lines))


def handle_user_set_country(token, chat, env, user_id, value):
    try:
        normalized = normalize_country_arg(value)
    except ValueError as exc:
        send_message(token, chat, str(exc))
        return

    if normalized:
        countries, source, error = get_spider_countries(force_refresh=False)
        if countries and normalized not in countries:
            send_message(token, chat, f"Country <code>{escape(normalized)}</code> tidak ada di daftar Spider saat ini.")
            return
        if not countries and error:
            send_message(token, chat, f"Tidak bisa validasi country dari Spider ({escape(error)}). Config tetap dicoba.")

    users = read_users()
    record = users.get(str(user_id))
    if not is_user_active(record):
        send_message(token, chat, "Akun Anda sudah expired atau disabled.")
        return
    record["country"] = normalized
    record["updated_at"] = int(time.time())
    ok, output = apply_user_service(env, user_id, record)
    if ok:
        write_users(users)
        send_message(token, chat, f"Country proxy Anda diubah ke <code>{escape(normalized or 'default')}</code>.\n<code>{escape(output)}</code>")
    else:
        send_message(token, chat, f"Gagal apply proxy Anda:\n<code>{escape(output)}</code>")


def handle_user_set_proxy(token, chat, env, user_id, value):
    normalized = value.lower()
    if normalized == "datacenter":
        normalized = "isp"
    if normalized not in PROXY_TYPES:
        send_message(token, chat, "Pool tidak didukung. Gunakan <code>/pools</code> untuk pilihan.")
        return

    users = read_users()
    record = users.get(str(user_id))
    if not is_user_active(record):
        send_message(token, chat, "Akun Anda sudah expired atau disabled.")
        return
    record["pool"] = normalized
    record["updated_at"] = int(time.time())
    ok, output = apply_user_service(env, user_id, record)
    if ok:
        write_users(users)
        send_message(token, chat, f"Pool proxy Anda diubah ke <code>{escape(normalized)}</code>.\n<code>{escape(output)}</code>")
    else:
        send_message(token, chat, f"Gagal apply proxy Anda:\n<code>{escape(output)}</code>")


def handle_admin_command(token, update, env, command, args):
    chat = chat_id(update)

    if command in {"/start", "/help"}:
        send_message(token, chat, HELP_TEXT)
        return

    if command == "/status":
        send_message(token, chat, status_text(env), proxy_copy_keyboard(env))
        return

    if command == "/countries":
        handle_countries(token, chat, page=0, force_refresh=False)
        return

    if command == "/refreshcountries":
        handle_countries(token, chat, page=0, force_refresh=True)
        return

    if command == "/pools":
        send_message(token, chat, "Pilih pool Spider:", build_keyboard("pools"))
        return

    if command == "/setcountry":
        if not args:
            send_message(token, chat, "Contoh: <code>/setcountry US</code> atau <code>/setcountry off</code>")
            return
        handle_set_country(token, chat, env, args[0])
        return

    if command == "/setcountryparam":
        if not args:
            send_message(token, chat, "Contoh: <code>/setcountryparam country_code</code> atau <code>/setcountryparam country</code>")
            return
        handle_set_country_param(token, chat, env, args[0])
        return

    if command == "/setproxy":
        if not args:
            send_message(token, chat, "Contoh: <code>/setproxy residential</code> atau <code>/setproxy default</code>")
            return
        handle_set_proxy(token, chat, env, args[0])
        return

    if command == "/setengine":
        handle_set_engine(token, chat, env, args)
        return

    if command == "/setupstream":
        handle_set_upstream(token, chat, env, args)
        return

    if command == "/showproxy":
        send_message(token, chat, f"📋 <b>Local Proxy</b>\n<code>{escape(proxy_line(env))}</code>", proxy_copy_keyboard(env))
        return

    if command == "/test":
        handle_test(token, chat, env)
        return

    if command == "/testurl":
        handle_test_url(token, chat, env, args)
        return

    if command == "/diag":
        handle_diag(token, chat, env)
        return

    if command == "/balance":
        send_message(token, chat, f"<b>Spider Balance</b>\n{format_spider_credits(fetch_spider_credits(env))}")
        return

    if command == "/apply":
        ok, output = run_apply()
        prefix = "Apply berhasil" if ok else "Apply gagal"
        send_message(token, chat, f"{prefix}:\n<code>{escape(output)}</code>")
        return

    if command == "/setlocalpass":
        if not args or not valid_local_credential(args[0]):
            send_message(token, chat, "Password lokal harus 3-64 chars: A-Z a-z 0-9 . _ -")
            return
        env["LOCAL_PROXY_PASS"] = args[0]
        save_env(env)
        ok, output = run_apply()
        prefix = "Password lokal diubah" if ok else "Gagal apply password lokal"
        send_message(token, chat, f"{prefix}:\n<code>{escape(output)}</code>")
        return

    if command == "/setlocaluser":
        if not args or not valid_local_credential(args[0]):
            send_message(token, chat, "User lokal harus 3-64 chars: A-Z a-z 0-9 . _ -")
            return
        env["LOCAL_PROXY_USER"] = args[0]
        save_env(env)
        ok, output = run_apply()
        prefix = "User lokal diubah" if ok else "Gagal apply user lokal"
        send_message(token, chat, f"{prefix}:\n<code>{escape(output)}</code>")
        return

    if command == "/setport":
        if not args or not valid_port(args[0]):
            send_message(token, chat, "Port harus angka 1-65535.")
            return
        env["LOCAL_PROXY_PORT"] = args[0]
        save_env(env)
        ok, output = run_apply()
        prefix = "Port lokal diubah" if ok else "Gagal apply port lokal"
        send_message(token, chat, f"{prefix}:\n<code>{escape(output)}</code>")
        return

    if command == "/addadmin":
        if not args or not args[0].isdigit():
            send_message(token, chat, "Contoh: <code>/addadmin 123456789</code>")
            return
        admin_id = int(args[0])
        ids = parse_admin_ids(env.get("TELEGRAM_ADMIN_IDS", ""))
        ids.add(admin_id)
        env["TELEGRAM_ADMIN_IDS"] = ",".join(str(item) for item in sorted(ids))
        save_env(env)
        try:
            set_commands_for_chat(token, admin_id, ADMIN_COMMANDS)
        except Exception as exc:
            log(f"Unable to set admin commands for {admin_id}: {exc}")
        send_message(token, chat, "Admin ditambahkan.")
        return

    if command == "/deladmin":
        if not args or not args[0].isdigit():
            send_message(token, chat, "Contoh: <code>/deladmin 123456789</code>")
            return
        admin_id = int(args[0])
        ids = parse_admin_ids(env.get("TELEGRAM_ADMIN_IDS", ""))
        ids.discard(admin_id)
        env["TELEGRAM_ADMIN_IDS"] = ",".join(str(item) for item in sorted(ids))
        save_env(env)
        users = read_users()
        fallback_commands = USER_COMMANDS if is_user_active(users.get(str(admin_id))) else PUBLIC_COMMANDS
        try:
            set_commands_for_chat(token, admin_id, fallback_commands)
        except Exception as exc:
            log(f"Unable to reset admin commands for {admin_id}: {exc}")
        send_message(token, chat, "Admin dihapus.")
        return

    if command == "/adduser":
        handle_add_user(token, chat, env, args)
        return

    if command == "/deluser":
        handle_del_user(token, chat, args)
        return

    if command == "/listuser":
        handle_users(token, chat)
        return

    send_message(token, chat, "Perintah tidak dikenal. Jalankan <code>/help</code>.")


def handle_user_command(token, update, env, command, args, record):
    chat = chat_id(update)
    user_id = str(sender_id(update))
    cfg = user_config_env(env, record)

    if command in {"/start", "/help"}:
        send_message(token, chat, USER_HELP_TEXT)
        return

    if command == "/status":
        send_message(token, chat, user_status_text(env, user_id, record), proxy_copy_keyboard(cfg))
        return

    if command in {"/showproxy", "/myproxy"}:
        proxy = proxy_line(cfg)
        send_message(token, chat, f"<b>Proxy Anda</b>\n<code>{escape(proxy)}</code>", proxy_copy_keyboard_for_text(proxy))
        return

    if command == "/countries":
        handle_countries(token, chat, page=0, force_refresh=False)
        return

    if command == "/refreshcountries":
        handle_countries(token, chat, page=0, force_refresh=True)
        return

    if command == "/pools":
        send_message(token, chat, "Pilih pool Spider untuk proxy Anda:", build_keyboard("pools"))
        return

    if command == "/setcountry":
        if not args:
            send_message(token, chat, "Contoh: <code>/setcountry SG</code> atau <code>/setcountry off</code>")
            return
        handle_user_set_country(token, chat, env, user_id, args[0])
        return

    if command == "/setproxy":
        if not args:
            send_message(token, chat, "Contoh: <code>/setproxy residential</code> atau <code>/setproxy default</code>")
            return
        handle_user_set_proxy(token, chat, env, user_id, args[0])
        return

    if command == "/test":
        handle_test(token, chat, cfg)
        return

    if command == "/testurl":
        handle_test_url(token, chat, cfg, args)
        return

    if command in ADMIN_ONLY_COMMANDS:
        send_message(token, chat, "Perintah ini khusus admin.")
        return

    send_message(token, chat, "Perintah user tidak dikenal. Jalankan <code>/help</code>.")


def handle_message(token, update):
    env = load_env()
    message = update.get("message", {})
    text = message.get("text", "").strip()
    chat = chat_id(update)

    if not text:
        return

    parts = text.split()
    command = parts[0].split("@", 1)[0].lower()
    args = parts[1:]

    if command == "/whoami":
        send_message(token, chat, f"Telegram user ID Anda: <code>{escape(sender_id(update))}</code>")
        return

    if command == "/claim":
        claim_admin(token, update, env, args)
        return

    try:
        send_chat_action(token, chat)
        if is_admin(env, update):
            handle_admin_command(token, update, env, command, args)
            return

        users = read_users()
        user_id = str(sender_id(update))
        record = users.get(user_id)
        if is_user_active(record):
            try:
                set_commands_for_chat(token, user_id, USER_COMMANDS)
            except Exception as exc:
                log(f"Unable to refresh user commands for {user_id}: {exc}")
            handle_user_command(token, update, env, command, args, record)
            return

        reconcile_user_services(env, users, token)
        require_admin_or_reply(token, update, env)
    except Exception as exc:
        log(f"Command {command} failed: {exc}")
        try:
            send_message(token, chat, f"Perintah <code>{escape(command)}</code> gagal:\n<code>{escape(exc)}</code>")
        except Exception as send_exc:
            log(f"Unable to report command failure: {send_exc}")


def handle_callback(token, update):
    env = load_env()
    callback = update.get("callback_query", {})
    chat = chat_id(update)
    data = callback.get("data", "")
    user_id = str(sender_id(update))
    users = read_users()
    user_record = users.get(user_id)
    admin = is_admin(env, update)
    active_user = is_user_active(user_record)
    if not admin and not active_user:
        reconcile_user_services(env, users, token)
        answer_callback(token, callback.get("id", ""), "Access denied")
        require_admin_or_reply(token, update, env)
        return

    try:
        if data.startswith("noop:"):
            answer_callback(token, callback["id"], "")
            return

        if data.startswith("countries:"):
            page = int(data.split(":", 1)[1])
            answer_callback(token, callback["id"], "Membuka halaman country...")
            handle_countries(
                token,
                chat,
                page=page,
                force_refresh=False,
                edit_message_id=callback_message_id(update),
            )
            return

        if data.startswith("country:"):
            value = data.split(":", 1)[1]
            answer_callback(token, callback["id"], "Mengubah country...")
            if admin:
                handle_set_country(token, chat, env, value)
            else:
                handle_user_set_country(token, chat, env, user_id, value)
            return

        if data.startswith("proxy:"):
            value = data.split(":", 1)[1]
            answer_callback(token, callback["id"], "Mengubah pool...")
            if admin:
                handle_set_proxy(token, chat, env, value)
            else:
                handle_user_set_proxy(token, chat, env, user_id, value)
            return

        answer_callback(token, callback["id"], "Unknown action")
    except Exception as exc:
        answer_callback(token, callback.get("id", ""), "Error")
        send_message(token, chat, f"Callback error:\n<code>{escape(exc)}</code>")


def handle_update(token, update):
    if "message" in update:
        handle_message(token, update)
    elif "callback_query" in update:
        handle_callback(token, update)


def main():
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        log("TELEGRAM_BOT_TOKEN is not configured")
        return 1

    try:
        set_my_commands(token, env)
    except Exception as exc:
        log(f"Unable to set Telegram commands: {exc}")

    offset = read_update_offset()
    last_reconcile = 0
    while True:
        try:
            now = int(time.time())
            if now - last_reconcile >= 60:
                reconcile_user_services(load_env(), token=token)
                last_reconcile = now

            payload = {
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"],
            }
            if offset is not None:
                payload["offset"] = offset

            updates = telegram_api(token, "getUpdates", payload, timeout=40) or []
            for update in updates:
                offset = update["update_id"] + 1
                write_update_offset(offset)
                try:
                    handle_update(token, update)
                except Exception as exc:
                    log(f"Update handling failed: {exc}")
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            log(f"Polling failed: {exc}")
            time.sleep(5)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            log(f"Unexpected error: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
