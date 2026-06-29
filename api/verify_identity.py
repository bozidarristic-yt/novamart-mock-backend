from http.server import BaseHTTPRequestHandler
import json, os, urllib.request, urllib.parse, urllib.error

# --- Supabase connection (service key lives only in Vercel env vars) ---
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
if SUPABASE_URL.endswith("/rest/v1"):
    SUPABASE_URL = SUPABASE_URL[: -len("/rest/v1")]
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

MAX_ATTEMPTS = 3


def sb(method, path, body=None):
    """Call the Supabase REST (PostgREST) API with the service key."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("apikey", SERVICE_KEY)
    req.add_header("Authorization", f"Bearer {SERVICE_KEY}")
    req.add_header("Content-Type", "application/json")
    if body is not None:
        req.add_header("Prefer", "return=representation")
    try:
        with urllib.request.urlopen(req) as r:
            text = r.read().decode()
            return json.loads(text) if text else []
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Supabase {method} {path} -> {e.code}: {e.read().decode()}")


def process(params):
    """verify_identity(account_number, postcode, attempts?) -> {success, ...}.

    Stateless. The running failed-attempt count is carried in a per-conversation
    dynamic variable ({{verify_attempts}}), passed in as `attempts`. On a failed
    match the tool returns that count + 1 and a `locked` flag (>= MAX_ATTEMPTS);
    the workflow writes `attempts` back to the variable. Always HTTP 200 so the
    workflow branches on the semantic `success` field.
    """
    acc = params.get("account_number")
    pc = params.get("postcode")

    # current count carried in from {{verify_attempts}} (empty on the first call)
    try:
        prior = int(params.get("attempts") or 0)
    except (ValueError, TypeError):
        prior = 0

    # Missing input is not counted as a real attempt.
    if not acc or not pc:
        return 200, {"success": False, "attempts": prior, "locked": prior >= MAX_ATTEMPTS}

    q = urllib.parse.quote(str(acc))
    rows = sb("GET", f"identities?account_number=eq.{q}&select=account_number,postcode,customer_name")
    ok = bool(rows) and str(rows[0]["postcode"]) == str(pc)

    if ok:
        return 200, {
            "success": True,
            "customer_name": rows[0]["customer_name"],
            "account_number": str(acc),
            "attempts": prior,
            "locked": False,
        }

    new = prior + 1
    return 200, {"success": False, "attempts": new, "locked": new >= MAX_ATTEMPTS}


class handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("content-length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            params = json.loads(raw) if raw else {}
        except Exception:
            params = {}
        try:
            code, payload = process(params)
        except Exception as e:
            code, payload = 500, {"error": str(e)}
        self._send(code, payload)

    def do_GET(self):
        q = urllib.parse.urlparse(self.path).query
        params = {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}
        try:
            code, payload = process(params)
        except Exception as e:
            code, payload = 500, {"error": str(e)}
        self._send(code, payload)
