from http.server import BaseHTTPRequestHandler
from datetime import datetime, timezone
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
    """update_shipping_address(account_number, order_id, new_address) -> confirmation.

    Verifies the order belongs to the account, updates the address, and
    writes an audit row. NOTE: a real production endpoint would accept POST
    only; GET is enabled here purely for easy browser testing of this mock.
    """
    acc = params.get("account_number")
    oid = params.get("order_id")
    new_addr = params.get("new_address")
    if not acc or not oid or not new_addr:
        return 400, {"error": "account_number, order_id, new_address required"}

    # 1. Confirm the order exists AND belongs to this account.
    qo = urllib.parse.quote(str(oid))
    qa = urllib.parse.quote(str(acc))
    rows = sb("GET", f"orders?order_id=eq.{qo}&account_number=eq.{qa}&select=order_id,shipping_address")
    if not rows:
        return 200, {"success": False, "reason": "order_not_found_for_account"}

    old_address = rows[0].get("shipping_address")

    # 2. Update the address (this really persists in the database).
    sb("PATCH", f"orders?order_id=eq.{qo}", {"shipping_address": new_addr})

    # 3. Write an audit record of the change.
    sb("POST", "address_changes", {
        "account_number": str(acc),
        "order_id": str(oid),
        "old_address": old_address,
        "new_address": new_addr,
    })

    return 200, {
        "success": True,
        "order_id": str(oid),
        "old_address": old_address,
        "new_address": new_addr,
        "updated_at": datetime.now(timezone.utc).isoformat(),
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
