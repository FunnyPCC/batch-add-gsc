#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-api-python-client>=2.0",
#     "google-auth-oauthlib>=1.0",
#     "requests>=2.28",
# ]
# ///
"""
Batch add domains to Google Search Console.

Flow per domain:
  1. Site Verification API → get TXT token
  2. Cloudflare API → write DNS TXT record
  3. Wait for DNS propagation
  4. Site Verification API → verify ownership
  5. Search Console API → add as property

Usage:
  uv run batch_add_gsc.py

Credentials:
  - Google OAuth: reads from OAUTH_JSON path (browser auth on first run, cached after)
  - Cloudflare: configure via get_cf_credentials() — supports 1Password op CLI,
    environment variables, or direct assignment
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Configuration — EDIT THESE ────────────────────────
SCRIPT_DIR = Path(__file__).parent
OAUTH_JSON = SCRIPT_DIR / "OAuth.json"         # Path to Google OAuth client credentials JSON
DOMAIN_FILE = SCRIPT_DIR / "domains.txt"        # One domain per line
TOKEN_FILE = SCRIPT_DIR / ".gsc_token.json"     # Cached OAuth token (auto-generated)
DNS_WAIT_SECONDS = 10                           # Seconds to wait for DNS propagation
OAUTH_PORT = 8099                               # Local port for OAuth redirect

# Google OAuth — Method B: direct Client ID + Secret (if no JSON file)
OAUTH_CLIENT_ID = ""       # e.g. "123456-xxx.apps.googleusercontent.com"
OAUTH_CLIENT_SECRET = ""   # e.g. "GOCSPX-xxx"

# Google OAuth — Method C: 1Password (store refresh_token + client credentials)
# Item fields: "client_id", "client_secret", "refresh_token"
OP_GOOGLE_ITEM = ""        # 1Password item UUID or name for Google OAuth

# Cloudflare credentials — choose ONE method in get_cf_credentials() below
# Method A: 1Password op CLI (recommended)
OP_ACCOUNT = ""    # 1Password account ID
OP_ITEM = ""       # Item UUID or name (Cloudflare)
OP_VAULT = ""      # Vault name

# Method B: Environment variables
# Set CF_EMAIL and CF_API_KEY before running

# Method C: Direct (not recommended for shared scripts)
# CF_EMAIL_DIRECT = ""
# CF_API_KEY_DIRECT = ""
# ──────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/siteverification",
    "https://www.googleapis.com/auth/webmasters",
]

CF_API = "https://api.cloudflare.com/client/v4"


def get_cf_credentials():
    """
    Get Cloudflare email and Global API Key.
    Uncomment the method that matches your setup.
    """
    # ── Method A: 1Password ──
    if OP_ACCOUNT and OP_ITEM:
        print("  Reading Cloudflare credentials from 1Password...")
        try:
            op_base = ["op", "item", "get", OP_ITEM, "--account", OP_ACCOUNT, "--reveal"]
            if OP_VAULT:
                op_base += ["--vault", OP_VAULT]
            email = subprocess.check_output(op_base + ["--fields", "username"], text=True).strip()
            api_key = subprocess.check_output(op_base + ["--fields", "API key"], text=True).strip()
            return email, api_key
        except subprocess.CalledProcessError as e:
            print(f"  1Password error: {e}")
            print("  Make sure you've run: op signin --account " + OP_ACCOUNT)
            sys.exit(1)

    # ── Method B: Environment variables ──
    email = os.environ.get("CF_EMAIL")
    api_key = os.environ.get("CF_API_KEY")
    if email and api_key:
        return email, api_key

    # ── Method C: Direct (uncomment and fill in above) ──
    # if CF_EMAIL_DIRECT and CF_API_KEY_DIRECT:
    #     return CF_EMAIL_DIRECT, CF_API_KEY_DIRECT

    print("ERROR: No Cloudflare credentials configured.")
    print("Edit get_cf_credentials() in this script — see comments for options.")
    sys.exit(1)


def _build_oauth_flow():
    """Build OAuth flow from JSON file or direct Client ID/Secret."""
    if OAUTH_JSON.exists():
        return InstalledAppFlow.from_client_secrets_file(str(OAUTH_JSON), SCOPES)

    if OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET:
        client_config = {
            "installed": {
                "client_id": OAUTH_CLIENT_ID,
                "client_secret": OAUTH_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
        return InstalledAppFlow.from_client_config(client_config, SCOPES)

    print("ERROR: No Google OAuth credentials configured.")
    print("Either set OAUTH_JSON path or fill in OAUTH_CLIENT_ID + OAUTH_CLIENT_SECRET.")
    sys.exit(1)


def _creds_from_1password():
    """Build Credentials from 1Password stored refresh_token + client credentials."""
    if not (OP_GOOGLE_ITEM and OP_ACCOUNT):
        return None
    print("  Reading Google OAuth credentials from 1Password...")
    try:
        op_base = ["op", "item", "get", OP_GOOGLE_ITEM, "--account", OP_ACCOUNT, "--reveal"]
        if OP_VAULT:
            op_base += ["--vault", OP_VAULT]
        client_id = subprocess.check_output(op_base + ["--fields", "client_id"], text=True).strip()
        client_secret = subprocess.check_output(op_base + ["--fields", "client_secret"], text=True).strip()
        refresh_token = subprocess.check_output(op_base + ["--fields", "refresh_token"], text=True).strip()
    except subprocess.CalledProcessError as e:
        print(f"  1Password error: {e}")
        return None

    from google.auth.transport.requests import Request
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())
    except Exception as e:
        print(f"  Failed to refresh token from 1Password: {e}")
        return None
    return creds


def google_auth():
    """Google OAuth 2.0 — tries cached token → 1Password → browser flow."""
    creds = None

    # 1) Try cached token file
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            pass

    # 2) Try 1Password (refresh_token + client credentials)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        else:
            creds = _creds_from_1password()

    # 3) Fall back to browser OAuth flow
    if not creds or not creds.valid:
        print("  Opening browser for Google authorization...")
        flow = _build_oauth_flow()
        creds = flow.run_local_server(port=OAUTH_PORT)

    # Cache token for next run
    TOKEN_FILE.write_text(creds.to_json())
    print("  Google auth ready")

    return creds


def cf_headers(email, api_key):
    return {
        "X-Auth-Email": email,
        "X-Auth-Key": api_key,
        "Content-Type": "application/json",
    }


def get_verification_token(sv_service, domain):
    """Get the DNS TXT verification token for a domain."""
    resp = sv_service.webResource().getToken(body={
        "site": {"type": "INET_DOMAIN", "identifier": domain},
        "verificationMethod": "DNS_TXT",
    }).execute()
    return resp["token"]


def get_cf_zone_id(domain, email, api_key):
    """Look up the Cloudflare Zone ID for a domain."""
    resp = requests.get(
        f"{CF_API}/zones",
        headers=cf_headers(email, api_key),
        params={"name": domain, "status": "active"},
    )
    data = resp.json()
    if not data.get("success") or not data.get("result"):
        return None
    return data["result"][0]["id"]


def write_txt_record(zone_id, domain, token, email, api_key):
    """Add or update the google-site-verification TXT record."""
    headers = cf_headers(email, api_key)

    # Check for existing google-site-verification record
    resp = requests.get(
        f"{CF_API}/zones/{zone_id}/dns_records",
        headers=headers,
        params={"type": "TXT", "name": domain},
    )
    for rec in resp.json().get("result", []):
        if "google-site-verification" in rec.get("content", ""):
            requests.put(
                f"{CF_API}/zones/{zone_id}/dns_records/{rec['id']}",
                headers=headers,
                json={"type": "TXT", "name": domain, "content": token, "ttl": 120},
            )
            return "updated"

    # Create new record
    resp = requests.post(
        f"{CF_API}/zones/{zone_id}/dns_records",
        headers=headers,
        json={"type": "TXT", "name": domain, "content": token, "ttl": 120},
    )
    return "created" if resp.json().get("success") else f"failed: {resp.json().get('errors')}"


def verify_domain(sv_service, domain):
    """Complete domain ownership verification."""
    return sv_service.webResource().insert(
        body={
            "site": {"type": "INET_DOMAIN", "identifier": domain},
            "verificationMethod": "DNS_TXT",
        },
        verificationMethod="DNS_TXT",
    ).execute()


def add_to_search_console(sc_service, domain):
    """Register domain as a Search Console property."""
    try:
        sc_service.sites().add(siteUrl=f"sc-domain:{domain}").execute()
        return True
    except Exception as e:
        return "already exists" in str(e).lower()


def main():
    # Validate config
    has_oauth = (
        OAUTH_JSON.exists()
        or (OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET)
        or (OP_GOOGLE_ITEM and OP_ACCOUNT)
        or TOKEN_FILE.exists()
    )
    if not has_oauth:
        print("ERROR: No Google OAuth credentials configured.")
        print("Set OAUTH_JSON, OAUTH_CLIENT_ID+SECRET, or OP_GOOGLE_ITEM.")
        sys.exit(1)
    if not DOMAIN_FILE.exists():
        print(f"ERROR: Domain file not found: {DOMAIN_FILE}")
        sys.exit(1)

    domains = [l.strip() for l in DOMAIN_FILE.read_text().splitlines()
               if l.strip() and not l.strip().startswith("#")]
    if not domains:
        print("ERROR: Domain file is empty")
        sys.exit(1)

    print(f"=== Batch Add to Google Search Console ===")
    print(f"    {len(domains)} domains to process\n")

    # Credentials
    print("[1/2] Getting credentials...")
    cf_email, cf_api_key = get_cf_credentials()
    creds = google_auth()

    # Build API services
    print("[2/2] Connecting to Google APIs...\n")
    sv_service = build("siteVerification", "v1", credentials=creds)
    sc_service = build("searchconsole", "v1", credentials=creds)

    ok, failed, no_zone = [], [], []

    for i, domain in enumerate(domains, 1):
        print(f"[{i}/{len(domains)}] {domain}")

        zone_id = get_cf_zone_id(domain, cf_email, cf_api_key)
        if not zone_id:
            print(f"  SKIP — not found in Cloudflare")
            no_zone.append(domain)
            continue

        try:
            token = get_verification_token(sv_service, domain)
            print(f"  Token: {token[:50]}...")
        except Exception as e:
            print(f"  FAIL — get token: {e}")
            failed.append((domain, str(e)))
            continue

        status = write_txt_record(zone_id, domain, token, cf_email, cf_api_key)
        print(f"  DNS TXT: {status}")

        print(f"  Waiting {DNS_WAIT_SECONDS}s for DNS propagation...")
        time.sleep(DNS_WAIT_SECONDS)

        try:
            verify_domain(sv_service, domain)
            print(f"  Verified!")
        except Exception as e:
            if "already verified" in str(e).lower():
                print(f"  Already verified")
            else:
                print(f"  FAIL — verify: {e}")
                failed.append((domain, str(e)))
                continue

        if add_to_search_console(sc_service, domain):
            print(f"  Added to Search Console")
            ok.append(domain)
        else:
            print(f"  WARN — could not add to Search Console")
            failed.append((domain, "add to SC failed"))

    # Summary
    print(f"\n{'='*50}")
    print(f"RESULTS: {len(ok)} ok / {len(failed)} failed / {len(no_zone)} no zone")
    if failed:
        print("\nFailed:")
        for d, r in failed:
            print(f"  {d}: {r}")
    if no_zone:
        print("\nNot in Cloudflare:")
        for d in no_zone:
            print(f"  {d}")


if __name__ == "__main__":
    main()
