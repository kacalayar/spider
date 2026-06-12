#!/usr/bin/env python3
import html
import json
import os
import re
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
COUNTRY_SOURCE_URL = "https://spider.cloud/proxy-locations"
COUNTRY_CACHE_TTL_SECONDS = 24 * 60 * 60
COUNTRY_PAGE_SIZE = 40
HTTPS_IP_CHECK_URL = "https://api.ipify.org?format=json"
HTTP_IP_CHECK_URL = "http://api.ipify.org?format=json"

PROXY_TYPES = [
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
/setproxy residential - ubah pool Spider
/setupstream https - ubah upstream Spider ke http/https
/showproxy - tampilkan format ip:port:user:pass
/test - test proxy lokal via Spider
/apply - tulis ulang config dan restart Squid
/setlocalpass PASSWORD - ubah password proxy lokal
/setlocaluser USER - ubah user proxy lokal
/setport 3128 - ubah port proxy lokal
/whoami - lihat Telegram user ID
/addadmin USER_ID - tambah admin
/deladmin USER_ID - hapus admin
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


def send_message(token, chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    chunks = [text[i : i + 3900] for i in range(0, len(text), 3900)] or [""]
    for chunk in chunks:
        payload["text"] = chunk
        telegram_api(token, "sendMessage", payload)


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


def valid_upstream_scheme(value):
    return value in {"http", "https"}


def default_upstream_port(scheme):
    return "8889" if scheme == "https" else "8888"


def upstream_scheme(env):
    scheme = env.get("SPIDER_UPSTREAM_SCHEME", "https").lower()
    return scheme if valid_upstream_scheme(scheme) else "https"


def upstream_port(env):
    return env.get("SPIDER_UPSTREAM_PORT") or default_upstream_port(upstream_scheme(env))


def upstream_endpoint(env):
    scheme = upstream_scheme(env)
    host = env.get("SPIDER_UPSTREAM_HOST", "proxy.spider.cloud")
    return f"{scheme}://{host}:{upstream_port(env)}"


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
        message += "\nCek di VPS: journalctl -u squid -n 80 --no-pager"
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
        return {"ok": False, "ip": "", "raw": "", "error": str(exc)}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": True, "ip": "", "raw": body.strip(), "error": "response is not JSON"}

    ip = str(payload.get("ip", "")).strip()
    return {"ok": True, "ip": ip, "raw": body.strip(), "error": ""}


def proxy_live_check(env, include_direct=True):
    proxy_handler = urllib.request.ProxyHandler(
        {
            "http": local_proxy_url(env),
            "https": local_proxy_url(env),
        }
    )
    proxy_opener = urllib.request.build_opener(proxy_handler)
    https = fetch_ip(proxy_opener, HTTPS_IP_CHECK_URL, timeout=25)
    http = fetch_ip(proxy_opener, HTTP_IP_CHECK_URL, timeout=25)

    direct = None
    if include_direct:
        direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        direct = fetch_ip(direct_opener, HTTPS_IP_CHECK_URL, timeout=12)

    return {"https": https, "http": http, "direct": direct}


def format_single_check(label, result):
    if result["ok"] and result["ip"]:
        return [
            f"{label}: <code>OK</code>",
            f"{label} exit IP: <code>{escape(result['ip'])}</code>",
        ]
    if result["ok"]:
        return [
            f"{label}: <code>UNKNOWN</code>",
            f"{label} response: <code>{escape(result['raw'] or result['error'] or 'empty response')}</code>",
        ]
    return [
        f"{label}: <code>FAIL</code>",
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

    if not https["ok"] and "403" in https["error"] and upstream_scheme(env) == "http":
        lines.append("Hint: <code>HTTPS CONNECT ditolak saat upstream masih http/8888. Coba /setupstream https.</code>")

    return "\n".join(lines)


def status_text(env):
    country = env.get("SPIDER_COUNTRY_CODE") or "default"
    check = proxy_live_check(env, include_direct=True)
    return f"""<b>Spider Bridge Status</b>

Squid: <code>{escape(service_state("squid"))}</code>
Bot: <code>{escape(service_state("spider-bridge-bot"))}</code>

{format_live_check(check, env)}

Local proxy: <code>{escape((env.get("VPS_PUBLIC_IP") or "<VPS_IP>") + ":" + env.get("LOCAL_PROXY_PORT", "3128"))}</code>
Local user: <code>{escape(env.get("LOCAL_PROXY_USER", ""))}</code>
Spider pool: <code>{escape(env.get("SPIDER_PROXY_TYPE", "residential"))}</code>
Spider country: <code>{escape(country)}</code>
Spider country param: <code>{escape(env.get("SPIDER_COUNTRY_PARAM", "country_code"))}</code>
Spider upstream: <code>{escape(upstream_endpoint(env))}</code>
"""


def set_my_commands(token):
    commands = [
        {"command": "status", "description": "Show bridge status"},
        {"command": "countries", "description": "Choose Spider location"},
        {"command": "refreshcountries", "description": "Refresh Spider locations"},
        {"command": "pools", "description": "Choose Spider proxy pool"},
        {"command": "setcountry", "description": "Set country code"},
        {"command": "setcountryparam", "description": "Set country or country_code param"},
        {"command": "setproxy", "description": "Set Spider pool"},
        {"command": "setupstream", "description": "Set Spider upstream http/https"},
        {"command": "showproxy", "description": "Show ip:port:user:pass"},
        {"command": "test", "description": "Test local proxy"},
        {"command": "apply", "description": "Restart proxy with current config"},
        {"command": "whoami", "description": "Show your Telegram user ID"},
    ]
    telegram_api(token, "setMyCommands", {"commands": commands}, timeout=10)


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


def handle_set_upstream(token, chat, env, args):
    if not args:
        send_message(token, chat, "Contoh: <code>/setupstream https</code> atau <code>/setupstream http 8888</code>")
        return

    scheme = args[0].lower()
    if not valid_upstream_scheme(scheme):
        send_message(token, chat, "Upstream scheme harus <code>http</code> atau <code>https</code>.")
        return

    port = args[1] if len(args) > 1 else default_upstream_port(scheme)
    if not valid_port(port):
        send_message(token, chat, "Port upstream harus angka 1-65535.")
        return

    env["SPIDER_UPSTREAM_SCHEME"] = scheme
    env["SPIDER_UPSTREAM_PORT"] = port
    send_message(
        token,
        chat,
        f"Menerapkan upstream Spider ke <code>{escape(upstream_endpoint(env))}</code>...",
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


def handle_admin_command(token, update, env, command, args):
    chat = chat_id(update)

    if command in {"/start", "/help"}:
        send_message(token, chat, HELP_TEXT)
        return

    if command == "/status":
        send_message(token, chat, status_text(env))
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
            send_message(token, chat, "Contoh: <code>/setproxy residential</code>")
            return
        handle_set_proxy(token, chat, env, args[0])
        return

    if command == "/setupstream":
        handle_set_upstream(token, chat, env, args)
        return

    if command == "/showproxy":
        send_message(token, chat, f"<code>{escape(proxy_line(env))}</code>")
        return

    if command == "/test":
        handle_test(token, chat, env)
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
        ids = parse_admin_ids(env.get("TELEGRAM_ADMIN_IDS", ""))
        ids.add(int(args[0]))
        env["TELEGRAM_ADMIN_IDS"] = ",".join(str(item) for item in sorted(ids))
        save_env(env)
        send_message(token, chat, "Admin ditambahkan.")
        return

    if command == "/deladmin":
        if not args or not args[0].isdigit():
            send_message(token, chat, "Contoh: <code>/deladmin 123456789</code>")
            return
        ids = parse_admin_ids(env.get("TELEGRAM_ADMIN_IDS", ""))
        ids.discard(int(args[0]))
        env["TELEGRAM_ADMIN_IDS"] = ",".join(str(item) for item in sorted(ids))
        save_env(env)
        send_message(token, chat, "Admin dihapus.")
        return

    send_message(token, chat, "Perintah tidak dikenal. Jalankan <code>/help</code>.")


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

    if not require_admin_or_reply(token, update, env):
        return

    try:
        send_chat_action(token, chat)
        handle_admin_command(token, update, env, command, args)
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

    if not require_admin_or_reply(token, update, env):
        answer_callback(token, callback.get("id", ""), "Access denied")
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
            handle_set_country(token, chat, env, value)
            return

        if data.startswith("proxy:"):
            value = data.split(":", 1)[1]
            answer_callback(token, callback["id"], "Mengubah pool...")
            handle_set_proxy(token, chat, env, value)
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
        set_my_commands(token)
    except Exception as exc:
        log(f"Unable to set Telegram commands: {exc}")

    offset = None
    while True:
        try:
            payload = {
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"],
            }
            if offset is not None:
                payload["offset"] = offset

            updates = telegram_api(token, "getUpdates", payload, timeout=40) or []
            for update in updates:
                offset = update["update_id"] + 1
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
