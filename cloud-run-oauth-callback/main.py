"""SAP OAuth Callback Proxy for Cloud Run.

Receives SAP OAuth redirect callbacks, stores authorization codes
in Secret Manager for Agent Engine to consume automatically.
Uses Google One Tap to identify the user's Google account.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

import requests as http_requests
from flask import Flask, request, jsonify
from markupsafe import escape

from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import secretmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    raise RuntimeError("GOOGLE_CLOUD_PROJECT environment variable is required")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
PENDING_SECRET_PREFIX = "sap-oauth-pending"


def _sanitize_state_for_secret_id(state: str) -> str:
    """Create a Secret Manager-safe ID from the first 16 chars of state."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", state[:16])
    return f"{PENDING_SECRET_PREFIX}-{safe}"


def _get_sm_client():
    return secretmanager.SecretManagerServiceClient()


def _ensure_secret(client, secret_id: str) -> str:
    """Create secret if it doesn't exist. Returns full secret path."""
    parent = f"projects/{PROJECT_ID}"
    secret_path = f"{parent}/secrets/{secret_id}"
    try:
        client.get_secret(request={"name": secret_path})
    except NotFound:
        try:
            client.create_secret(request={
                "parent": parent,
                "secret_id": secret_id,
                "secret": {"replication": {"automatic": {}}},
            })
            logger.info("Created secret: %s", secret_id)
        except AlreadyExists:
            pass
    return secret_path


def _verify_google_id_token(id_token: str) -> dict | None:
    """Verify a Google ID token and return the payload.

    Uses Google's tokeninfo endpoint for verification.
    Returns the decoded payload (with 'email', 'sub', etc.) or None.
    """
    try:
        resp = http_requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": id_token},
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning("Google ID token verification failed: %s", resp.text)
            return None

        payload = resp.json()

        # Verify the token is for our application
        if payload.get("aud") != GOOGLE_CLIENT_ID:
            logger.warning(
                "Google ID token audience mismatch: got %s, expected %s",
                payload.get("aud"), GOOGLE_CLIENT_ID,
            )
            return None

        return payload

    except Exception as e:
        logger.error("Error verifying Google ID token: %s", e)
        return None


@app.route("/callback")
def oauth_callback():
    """Receive SAP OAuth redirect and store code in Secret Manager."""
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        logger.warning("OAuth error: %s", error)
        return _error_page(f"SAP login failed: {escape(error)}"), 400

    if not code or not state:
        logger.warning("Missing code or state in callback")
        return _error_page("Invalid callback: missing code or state"), 400

    try:
        client = _get_sm_client()
        secret_id = _sanitize_state_for_secret_id(state)
        secret_path = _ensure_secret(client, secret_id)

        payload_data = {
            "code": code,
            "state": state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = json.dumps(payload_data)

        client.add_secret_version(request={
            "parent": secret_path,
            "payload": {"data": payload.encode("UTF-8")},
        })

        logger.info(
            "Stored pending OAuth code: secret=%s, state=%.8s...",
            secret_id, state,
        )
        return _success_page(secret_id), 200

    except Exception as e:
        logger.error("Failed to store OAuth code: %s", e)
        return _error_page("Internal error. Please try again."), 500


@app.route("/identify", methods=["POST"])
def identify_user():
    """Receive Google ID token from One Tap and update pending secret."""
    data = request.get_json(silent=True) or {}
    credential = data.get("credential")
    secret_id = data.get("secret_id")

    if not credential or not secret_id:
        return jsonify({"error": "missing credential or secret_id"}), 400

    # Verify the Google ID token
    token_payload = _verify_google_id_token(credential)
    if not token_payload:
        return jsonify({"error": "invalid Google ID token"}), 401

    email = token_payload.get("email")
    if not email:
        return jsonify({"error": "no email in token"}), 400

    logger.info("Identified user: email=%s, secret=%s", email, secret_id)

    # Read the existing secret, add google_user_email, write new version
    try:
        client = _get_sm_client()
        secret_path = f"projects/{PROJECT_ID}/secrets/{secret_id}"
        version_path = f"{secret_path}/versions/latest"

        resp = client.access_secret_version(request={"name": version_path})
        existing = json.loads(resp.payload.data.decode("UTF-8"))
        existing["google_user_email"] = email

        client.add_secret_version(request={
            "parent": secret_path,
            "payload": {"data": json.dumps(existing).encode("UTF-8")},
        })

        logger.info(
            "Updated pending secret with Google identity: "
            "secret=%s, email=%s",
            secret_id, email,
        )
        return jsonify({"success": True, "email": email}), 200

    except NotFound:
        logger.warning("Secret not found for identify: %s", secret_id)
        return jsonify({"error": "secret not found"}), 404
    except Exception as e:
        logger.error("Failed to update secret with identity: %s", e)
        return jsonify({"error": "internal error"}), 500


@app.route("/health")
def health():
    return {"status": "ok"}


def _success_page(secret_id: str) -> str:
    """Render success page with Google One Tap for user identification."""
    safe_secret_id = escape(secret_id)
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>SAP Login Complete</title>
<script src="https://accounts.google.com/gsi/client" async defer></script>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         display: flex; justify-content: center; align-items: center;
         min-height: 100vh; background: #f5f5f5; margin: 0; }}
  .card {{ background: #fff; border-radius: 12px; padding: 40px;
          box-shadow: 0 2px 16px rgba(0,0,0,0.1); text-align: center;
          max-width: 420px; }}
  .check {{ width: 64px; height: 64px; background: #e8f5e9; border-radius: 50%;
           display: flex; align-items: center; justify-content: center;
           margin: 0 auto 20px; font-size: 32px; color: #2e7d32; }}
  h2 {{ color: #1a73e8; margin-bottom: 12px; font-size: 22px; }}
  p {{ color: #666; line-height: 1.6; font-size: 15px; }}
  .status {{ margin-top: 16px; padding: 8px 16px; border-radius: 8px;
            font-size: 13px; }}
  .status.pending {{ background: #fff3e0; color: #e65100; }}
  .status.done {{ background: #e8f5e9; color: #2e7d32; }}
  .status.error {{ background: #fce4ec; color: #c62828; }}
</style></head>
<body><div class="card">
  <div class="check">&#10003;</div>
  <h2>SAP Login Complete</h2>
  <p>You can now close this tab and return to the chat.<br>
     The agent will automatically detect your login.</p>
  <div id="identity-status" class="status pending">
    Linking your Google account...
  </div>
</div>

<script>
  const SECRET_ID = "{safe_secret_id}";

  function handleCredentialResponse(response) {{
    fetch("/identify", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{
        credential: response.credential,
        secret_id: SECRET_ID
      }})
    }})
    .then(r => r.json())
    .then(data => {{
      const el = document.getElementById("identity-status");
      if (data.success) {{
        el.className = "status done";
        el.textContent = "Google account linked: " + data.email;
      }} else {{
        el.className = "status error";
        el.textContent = "Could not link account: " + (data.error || "unknown");
      }}
    }})
    .catch(err => {{
      const el = document.getElementById("identity-status");
      el.className = "status error";
      el.textContent = "Network error. Your SAP login is still valid.";
    }});
  }}

  window.onload = function() {{
    google.accounts.id.initialize({{
      client_id: "{GOOGLE_CLIENT_ID}",
      callback: handleCredentialResponse,
      auto_select: true
    }});
    google.accounts.id.prompt(function(notification) {{
      if (notification.isNotDisplayed() || notification.isSkippedMoment()) {{
        const el = document.getElementById("identity-status");
        el.className = "status error";
        el.textContent = "Could not detect Google account. SAP login is still valid.";
        console.log("One Tap not displayed:", notification.getNotDisplayedReason(),
                    notification.getSkippedReason());
      }}
    }});
  }};
</script>
</body></html>"""


def _error_page(message: str) -> str:
    safe_message = escape(message)
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>SAP Login Error</title>
<style>
  body {{ font-family: -apple-system, sans-serif; display: flex;
         justify-content: center; align-items: center;
         min-height: 100vh; background: #f5f5f5; margin: 0; }}
  .card {{ background: #fff; border-radius: 12px; padding: 40px;
          box-shadow: 0 2px 16px rgba(0,0,0,0.1); text-align: center;
          max-width: 420px; }}
  .icon {{ width: 64px; height: 64px; background: #fce4ec; border-radius: 50%;
          display: flex; align-items: center; justify-content: center;
          margin: 0 auto 20px; font-size: 32px; color: #c62828; }}
  h2 {{ color: #c62828; margin-bottom: 12px; }}
  p {{ color: #666; line-height: 1.6; }}
</style></head>
<body><div class="card">
  <div class="icon">&#10007;</div>
  <h2>Login Error</h2>
  <p>{safe_message}</p>
</div></body></html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
