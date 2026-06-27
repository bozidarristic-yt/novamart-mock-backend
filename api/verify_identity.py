from http.server import BaseHTTPRequestHandler
import json, os, urllib.request, urllib.parse, urllib.error

# --- Supabase connection (service-role key lives only in Vercel env vars) ---
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
if SUPABASE_URL.endswith("/rest/v1"):
    SUPABASE_URL = SUPABASE_URL[: -len("/rest/v1")]
    
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def sb(method, path, body=None):
    """Call the Supabase REST (PostgREST) API with the service-role key."""
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
    """verify_identity(account_number, postcode) -> {success, customer_name?}.

    Returns HTTP 200 in BOTH cases so the workflow can branch on the
    semantic result (success true/false), not on an HTTP error code.
    """
    acc = params.get("account_number")
    pc = params.get("postcode")
    if not acc or not pc:
        return 200, {"success": False}

    q = urllib.parse.quote(str(acc))
    rows = sb("GET", f"identities?account_number=eq.{q}&select=account_number,postcode,customer_name")

    if not rows or str(rows[0]["postcode"]) != str(pc):
        return 200, {"success": False}

    return 200, {
        "success": True,
        "customer_name": rows[0]["customer_name"],
        "account_number": str(acc),
    }


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
