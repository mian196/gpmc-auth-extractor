"""
GPMC Auth Data Extractor (Interactive TUI & CLI)
Generates and extracts full authentication strings for GPMC from MicroG RE account backups.
"""

import os
import re
import sys
import time
import json
import argparse
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
import sqlite3
from pathlib import Path

# ==============================================================================
# Protocol Constants (Required by Google's Auth API)
# ==============================================================================
# Official Google Photos APK Release Certificate SHA-1 hash.
# Google's auth servers verify that callerPkg matches this signature.
# Morphe / ReVanced's GmsCoreSupportPatch spoofs this exact hash.
# Reference file:
#   De-Vanced/patches/src/main/kotlin/app/morphe/patches/googlephotos/misc/gms/GmsCoreSupportPatch.kt
# Upstream repository source:
#   https://gitlab.com/ReVanced/revanced-patches/-/blob/main/patches/src/main/kotlin/app/revanced/patches/googlephotos/misc/gms/GmsCoreSupportPatch.kt
DEFAULT_CLIENT_SIG = "24bb24c05e47e0aefa68a58a766179d9b613a600"

# Target OAuth scopes required by Google Photos mobile endpoints.
DEFAULT_SERVICE = "oauth2:openid https://www.googleapis.com/auth/mobileapps.native https://www.googleapis.com/auth/photos.native"

# Google Photos mobile package name.
DEFAULT_APP = "com.google.android.apps.photos"

DEFAULT_LANG = "en_US"
DEFAULT_COUNTRY = "us"
CACHE_FILE = Path(".device_cache.json")

ACTIVATION_NOTICE = (
    "\n" + "-" * 68 + "\n"
    " [!] IMPORTANT 1-TIME ACTIVATION STEP (FOR NEWLY ADDED ACCOUNTS):\n"
    "     If you just added this account, Google's upload servers will timeout\n"
    "     until you perform the initial device handshake from the mobile app:\n"
    "       1. Open Google Photos (Morphe) on your device.\n"
    "       2. Switch to this account.\n"
    "       3. Tap 'Turn on backup' (or confirm backup) and let it load for ~30s.\n"
    "     Once done, this auth_data will upload with GPMC without any timeout!\n"
    + "-" * 68
)


# ==============================================================================
# Cache Management
# ==============================================================================
def load_device_cache() -> dict[str, str]:
    if CACHE_FILE.is_file():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_device_cache(info: dict[str, str]):
    try:
        CACHE_FILE.write_text(json.dumps(info, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[-] Warning saving cache: {e}", file=sys.stderr)

def clear_device_cache():
    if CACHE_FILE.is_file():
        try:
            CACHE_FILE.unlink()
            print("[+] Cleared device cache (.device_cache.json).")
        except Exception as e:
            print(f"[-] Error deleting cache file: {e}", file=sys.stderr)
    else:
        print("[*] No cache file found to clear.")


# ==============================================================================
# Hardware & Environment Extraction
# ==============================================================================
def extract_android_id(dump_dir: Path | None) -> str | None:
    # 1. Try sp/checkin.xml
    if dump_dir and dump_dir.is_dir():
        checkin_path = dump_dir / "sp" / "checkin.xml"
        if checkin_path.is_file():
            try:
                tree = ET.parse(checkin_path)
                root = tree.getroot()
                for elem in root.findall("long"):
                    if elem.get("name") == "androidId":
                        val = int(elem.get("value"))
                        return f"{val:016x}"
            except Exception as e:
                print(f"[-] Warning parsing checkin.xml: {e}", file=sys.stderr)

        # 2. Try gservices.db
        gservices_path = dump_dir / "db" / "gservices.db"
        if gservices_path.is_file():
            try:
                conn = sqlite3.connect(gservices_path)
                c = conn.cursor()
                c.execute("SELECT value FROM main WHERE name = 'android_id'")
                row = c.fetchone()
                if row and row[0]:
                    val = int(row[0])
                    return f"{val:016x}"
            except Exception:
                pass

    # 3. Try extracting from an existing auth text file in current folder (no traverse)
    for txt_path in Path(".").glob("*.txt"):
        if "auth" in txt_path.name.lower() and txt_path.is_file():
            try:
                m = re.search(r"androidId=([a-f0-9]{16})", txt_path.read_text(encoding="utf-8"))
                if m:
                    return m.group(1)
            except Exception:
                pass

    return None

def get_device_info_via_adb() -> dict[str, str]:
    """
    Queries connected Android device over ADB for:
    - androidId (from MicroG GServices provider)
    - sdk_version (ro.build.version.sdk)
    - google_play_services_version (versionCode of installed MicroG)
    - device_model (ro.product.model)
    """
    info = {}

    # Check if any device is connected
    try:
        devices_res = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=3)
        lines = [l.strip() for l in devices_res.stdout.splitlines() if l.strip() and not l.startswith("List of devices")]
        if not lines:
            return info
    except Exception:
        return info

    # Device Model
    try:
        res = subprocess.run(["adb", "shell", "getprop", "ro.product.model"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            info["device_model"] = res.stdout.strip()
    except Exception:
        pass

    # Android ID via GServices Content Provider
    for provider in ["app.revanced.android.gsf.gservices", "com.google.android.gsf.gservices"]:
        try:
            cmd = ["adb", "shell", "content", "query", "--uri", f"content://{provider}/prefix/android_id"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
            if res.returncode == 0 and "value=" in res.stdout:
                m = re.search(r"value=([0-9]+)", res.stdout)
                if m:
                    val = int(m.group(1))
                    info["android_id"] = f"{val:016x}"
                    break
        except Exception:
            pass

    # Android ID fallback for Morphe MicroG-RE: query via Checkin UI deep link
    if "android_id" not in info:
        try:
            # Wake screen briefly
            subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_WAKEUP"], capture_output=True, timeout=2)
            # Open Checkin screen via deep link
            subprocess.run(
                ["adb", "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", "x-gms-settings://checkin", "app.revanced.android.gms"],
                capture_output=True, timeout=4
            )
            time.sleep(1.5)
            # Dump UI hierarchy
            dump_res = subprocess.run(["adb", "shell", "uiautomator", "dump", "/sdcard/ui_checkin.xml"], capture_output=True, timeout=6)
            if dump_res.returncode == 0:
                cat_res = subprocess.run(
                    ["adb", "shell", "cat", "/sdcard/ui_checkin.xml"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=5
                )
                if cat_res.returncode == 0 and "Android ID" in cat_res.stdout:
                    try:
                        tree = ET.fromstring(cat_res.stdout)
                        found_title = False
                        for node in tree.iter("node"):
                            if node.attrib.get("text") == "Android ID":
                                found_title = True
                                continue
                            if found_title:
                                txt = node.attrib.get("text", "").strip().lower()
                                if re.match(r"^[0-9a-f]{16}$", txt):
                                    info["android_id"] = txt
                                    break
                    except Exception:
                        pass
                subprocess.run(["adb", "shell", "rm", "-f", "/sdcard/ui_checkin.xml"], capture_output=True, timeout=2)
            # Return phone to previous state
            subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_BACK"], capture_output=True, timeout=2)
        except Exception:
            pass

    # Android SDK Version
    try:
        res = subprocess.run(["adb", "shell", "getprop", "ro.build.version.sdk"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip().isdigit():
            info["sdk_version"] = res.stdout.strip()
    except Exception:
        pass

    # Google Play Services / MicroG Package Version Code
    for pkg in ["app.revanced.android.gms", "com.google.android.gms"]:
        try:
            res = subprocess.run(["adb", "shell", "dumpsys", "package", pkg], capture_output=True, text=True, timeout=4)
            if res.returncode == 0:
                m = re.search(r"versionCode=([0-9]+)", res.stdout)
                if m:
                    info["gms_version"] = m.group(1)
                    break
        except Exception:
            pass

    return info

def extract_manifest_info(dump_dir: Path | None) -> tuple[str | None, str | None]:
    if not dump_dir or not dump_dir.is_dir():
        return None, None

    gms_version = None
    sdk_version = None

    manifest_path = dump_dir / "_manifest"
    if manifest_path.is_file():
        try:
            lines = [l.strip() for l in manifest_path.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
            if len(lines) >= 4:
                if lines[2].isdigit():
                    gms_version = lines[2]
                if lines[3].isdigit():
                    sdk_version = lines[3]
        except Exception:
            pass

    return gms_version, sdk_version

def extract_accounts(dump_dir: Path | None) -> list[dict]:
    accounts = []
    seen = set()

    if dump_dir and dump_dir.is_dir():
        pluscontacts_path = dump_dir / "db" / "pluscontacts.db"
        if pluscontacts_path.is_file():
            try:
                conn = sqlite3.connect(pluscontacts_path)
                c = conn.cursor()
                c.execute("SELECT account_name, display_name, gaia_id FROM owners")
                for row in c.fetchall():
                    email = row[0]
                    if email and email not in seen:
                        seen.add(email)
                        accounts.append({
                            "email": email,
                            "display_name": row[1] or "",
                            "gaia_id": row[2] or "",
                            "token": None
                        })
            except Exception as e:
                print(f"[-] Warning parsing pluscontacts.db: {e}", file=sys.stderr)

    return accounts

def search_for_tokens(dump_dir: Path | None, accounts_db_path: Path | None = None) -> dict[str, str]:
    tokens = {}

    # Check for exported JSON files in current folder only (no traverse)
    for jf in Path(".").glob("*.json"):
        if jf.name == CACHE_FILE.name:
            continue
        if jf.is_file() and "account" in jf.name.lower():
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for entry in data:
                        if isinstance(entry, dict) and "name" in entry and "password" in entry:
                            tokens[entry["name"]] = entry["password"]
            except Exception as e:
                print(f"[-] Warning reading {jf}: {e}", file=sys.stderr)

    dbs_to_check = []
    if accounts_db_path and accounts_db_path.is_file():
        dbs_to_check.append(accounts_db_path)

    # Check for accounts.db in current folder only (no traverse)
    for cand in Path(".").glob("accounts*.db"):
        if cand.is_file() and cand not in dbs_to_check:
            dbs_to_check.append(cand)

    for db_file in dbs_to_check:
        try:
            conn = sqlite3.connect(db_file)
            c = conn.cursor()
            c.execute("SELECT name, password FROM accounts")
            for name, pwd in c.fetchall():
                if pwd and ("aas_et/" in pwd or "oauth2" in pwd or len(pwd) > 50):
                    tokens[name] = pwd
        except Exception:
            pass

    if dump_dir and dump_dir.is_dir():
        aas_pattern = re.compile(r"Token=(aas_et%2F[A-Za-z0-9_\-%=]+|aas_et/[A-Za-z0-9_\-%=]+)")
        email_pattern = re.compile(r"Email=([A-Za-z0-9_.%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]+)")
        for root, _, files in os.walk(dump_dir):
            for f in files:
                if f.endswith((".log", ".txt", ".json")):
                    p = Path(root) / f
                    try:
                        text = p.read_text(encoding="utf-8", errors="ignore")
                        for line in text.splitlines():
                            if "aas_et" in line:
                                t_match = aas_pattern.search(line)
                                e_match = email_pattern.search(line)
                                if t_match:
                                    token_val = urllib.parse.unquote(t_match.group(1))
                                    if e_match:
                                        email_val = urllib.parse.unquote(e_match.group(1))
                                        tokens[email_val] = token_val
                                    else:
                                        tokens["_unknown"] = token_val
                    except Exception:
                        pass

    return tokens

def capture_token_from_adb(timeout_sec: int = 45) -> str | None:
    print(f"[*] Starting ADB logcat listener (timeout: {timeout_sec}s)...")
    print("[*] Please open Google Photos on your device now...")
    try:
        proc = subprocess.Popen(
            ["adb", "logcat", "-v", "raw", "-s", "GmsHttpFormClient:D"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )
    except FileNotFoundError:
        print("[-] 'adb' command not found in PATH!", file=sys.stderr)
        return None

    start_time = time.time()
    try:
        while time.time() - start_time < timeout_sec:
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.1)
                continue
            if "auth%2Fphotos.native" in line or "photos.native" in line:
                if "Token=" in line:
                    proc.kill()
                    return line.strip()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            proc.kill()
        except Exception:
            pass

    return None

def build_auth_data(
    android_id: str,
    email: str,
    token: str,
    gms_version: str,
    sdk_version: str,
    client_sig: str = DEFAULT_CLIENT_SIG,
    country: str = DEFAULT_COUNTRY,
    lang: str = DEFAULT_LANG
) -> str:
    encoded_email = urllib.parse.quote(email, safe="")
    encoded_service = urllib.parse.quote(DEFAULT_SERVICE, safe="")
    encoded_token = urllib.parse.quote(token, safe="")

    params = [
        f"androidId={android_id}",
        f"app={DEFAULT_APP}",
        f"client_sig={client_sig}",
        f"callerPkg={DEFAULT_APP}",
        f"callerSig={client_sig}",
        f"device_country={country}",
        f"Email={encoded_email}",
        f"google_play_services_version={gms_version}",
        f"lang={lang}",
        f"oauth2_foreground=1",
        f"operatorCountry={country}",
        f"sdk_version={sdk_version}",
        f"service={encoded_service}",
        f"source=android",
        f"system_partition=1",
        f"Token={encoded_token}"
    ]
    return "&".join(params)


# ==============================================================================
# Device Context Loader
# ==============================================================================
def resolve_device_context(args=None, force_adb_refresh=False) -> dict[str, str]:
    """
    Resolves android_id, sdk_version, gms_version from:
    1. CLI args
    2. Cached file (.device_cache.json)
    3. Live ADB
    4. Dump directory
    """
    ctx = {
        "android_id": None,
        "sdk_version": None,
        "gms_version": None,
        "device_model": None,
        "source": "none"
    }

    # 1. Load cache first if not forced refresh
    cached = load_device_cache() if not force_adb_refresh else {}
    if cached:
        ctx.update(cached)
        ctx["source"] = "cache"

    # 2. Try ADB if fields are missing or refresh requested
    needs_adb = force_adb_refresh or not (ctx.get("android_id") and ctx.get("sdk_version") and ctx.get("gms_version"))
    if needs_adb:
        adb_info = get_device_info_via_adb()
        if adb_info:
            for k, v in adb_info.items():
                if v:
                    ctx[k] = v
            ctx["source"] = "adb"
            # Save newly fetched ADB info to cache
            save_device_cache(ctx)

    # 3. Check dump directory if still missing
    dump_dir = Path(args.dump_dir) if (args and hasattr(args, "dump_dir") and args.dump_dir) else Path("app.revanced.android.gms")
    if dump_dir.is_dir():
        if not ctx.get("android_id"):
            did = extract_android_id(dump_dir)
            if did:
                ctx["android_id"] = did
                ctx["source"] = "dump"
        dgms, dsdk = extract_manifest_info(dump_dir)
        if not ctx.get("gms_version") and dgms:
            ctx["gms_version"] = dgms
        if not ctx.get("sdk_version") and dsdk:
            ctx["sdk_version"] = dsdk

    # 4. CLI overrides
    if args:
        if getattr(args, "android_id", None):
            ctx["android_id"] = args.android_id
            ctx["source"] = "cli"
        if getattr(args, "sdk_version", None):
            ctx["sdk_version"] = args.sdk_version
        if getattr(args, "gms_version", None):
            ctx["gms_version"] = args.gms_version

    return ctx

def resolve_accounts(args=None, dump_dir: Path | None = None) -> list[dict]:
    dump_dir = dump_dir if (dump_dir and dump_dir.is_dir()) else None
    accounts = extract_accounts(dump_dir)
    existing_emails = {a["email"] for a in accounts}

    tokens = search_for_tokens(dump_dir, Path(args.accounts_db) if (args and args.accounts_db) else None)
    for email, tok in tokens.items():
        if email != "_unknown" and email not in existing_emails:
            accounts.append({
                "email": email,
                "display_name": "",
                "gaia_id": "",
                "token": tok
            })

    if args and getattr(args, "token", None):
        for acc in accounts:
            acc["token"] = args.token
    else:
        for acc in accounts:
            if acc["email"] in tokens:
                acc["token"] = tokens[acc["email"]]
            elif "_unknown" in tokens:
                acc["token"] = tokens["_unknown"]

    return accounts


# ==============================================================================
# Interactive Terminal User Interface (TUI)
# ==============================================================================
def run_tui():
    while True:
        ctx = resolve_device_context()
        dump_dir = Path("app.revanced.android.gms") if Path("app.revanced.android.gms").is_dir() else None
        accounts = resolve_accounts(dump_dir=dump_dir)

        print("\n" + "=" * 68)
        print("          GPMC AUTH DATA EXTRACTOR - INTERACTIVE MENU")
        print("=" * 68)

        # Device info header
        android_id = ctx.get("android_id") or "<Not Detected>"
        sdk_version = ctx.get("sdk_version") or "<Not Detected>"
        gms_version = ctx.get("gms_version") or "<Not Detected>"
        device_model = ctx.get("device_model") or "Android Device"
        source = ctx.get("source", "none")

        print(f"[Device Status] ({device_model})")
        print(f"  Android ID:   {android_id}")
        print(f"  SDK Version:  {sdk_version}")
        print(f"  GMS Version:  {gms_version}")

        if source == "cache":
            print("  Source:       Cached in .device_cache.json")
            print("  [!] WARNING:  If switching to a new device, choose Option 6 to clear cache!")
        elif source == "adb":
            print("  Source:       Live ADB (Saved to .device_cache.json)")
        elif source == "dump":
            print("  Source:       MicroG Backup Dump")
        else:
            print("  Source:       Not detected (Connect ADB or enter manually)")

        print("-" * 68)
        print(f"[Accounts Status] ({len(accounts)} detected in folder)")
        if accounts:
            for idx, acc in enumerate(accounts, 1):
                status = "Master token ready" if acc.get("token") else "Missing token"
                print(f"  [{idx}] {acc['email']} ({status})")
        else:
            print("  No microg-accounts-*.json found in current folder.")
            print("  -> Back up accounts from MicroG RE app and place JSON in this folder.")

        print("=" * 68)
        print(" [1] Generate GPMC Auth Tokens for All Accounts")
        print(" [2] Save All Auth Tokens to File (gpmc_auth_tokens.txt)")
        print(" [3] Generate Token for a Specific Account")
        print(" [4] Fetch / Refresh Device Info from Connected Phone (via ADB)")
        print(" [5] Manually Enter Device Info (Android ID, SDK, GMS)")
        print(" [6] Clear Cached Device Info (.device_cache.json)")
        print(" [7] Capture Live Auth Token via ADB Logcat (Google Photos)")
        print(" [0] Exit")
        print("=" * 68)

        choice = input("Enter choice [1-7, 0]: ").strip()

        if choice == "0":
            print("\nGoodbye!")
            break

        elif choice == "1":
            print("\n--- GENERATED GPMC AUTH TOKENS ---")
            if not accounts:
                print("[-] No accounts found! Please place your microg-accounts-*.json in this folder.")
                input("\nPress Enter to continue...")
                continue

            has_valid = False
            for acc in accounts:
                email = acc["email"]
                token = acc.get("token") or "<MISSING_TOKEN>"
                auth_data = build_auth_data(
                    android_id=android_id,
                    email=email,
                    token=token,
                    gms_version=gms_version,
                    sdk_version=sdk_version
                )
                print(f"\nAccount: {email}")
                if "<MISSING_" in auth_data:
                    print("[-] Incomplete parameters in auth_data:")
                    print(auth_data)
                    print("[!] Please fetch device info with Option 4 (ADB) or Option 5 (Manual).")
                else:
                    has_valid = True
                    print("[+] auth_data:")
                    print(auth_data)
                    print(f'\n[+] Example CLI command:\ngpmc /path/to/media.jpg --auth_data "{auth_data}"')
            
            if has_valid:
                print(ACTIVATION_NOTICE)
            input("\nPress Enter to continue...")

        elif choice == "2":
            out_file = Path("gpmc_auth_tokens.txt")
            valid_results = []
            for acc in accounts:
                if acc.get("token") and not "<MISSING_" in f"{android_id}{sdk_version}{gms_version}":
                    ad = build_auth_data(
                        android_id=android_id,
                        email=acc["email"],
                        token=acc["token"],
                        gms_version=gms_version,
                        sdk_version=sdk_version
                    )
                    valid_results.append((acc["email"], ad))

            if valid_results:
                with open(out_file, "w", encoding="utf-8") as fp:
                    fp.write(
                        "# ==============================================================================\n"
                        "# GPMC AUTH TOKENS\n"
                        "# NOTE FOR NEW ACCOUNTS: Open Google Photos (Morphe) on your phone once,\n"
                        "# switch to the account, enable backup, and let it load for ~30 seconds\n"
                        "# before uploading with GPMC, otherwise Google will timeout upload requests.\n"
                        "# ==============================================================================\n\n"
                    )
                    for email, ad in valid_results:
                        fp.write(f"# {email}\n{ad}\n\n")
                print(f"\n[+] Successfully saved {len(valid_results)} token(s) to {out_file.name}")
                print(ACTIVATION_NOTICE)
            else:
                print("\n[-] Cannot save file: Either accounts are missing tokens, or device info is not resolved.")
                print("    Please resolve device info via ADB or manual entry first.")
            input("\nPress Enter to continue...")

        elif choice == "3":
            if not accounts:
                print("[-] No accounts loaded.")
                input("\nPress Enter to continue...")
                continue
            print("\nSelect an account:")
            for idx, acc in enumerate(accounts, 1):
                print(f"  [{idx}] {acc['email']}")
            sel = input(f"Choose account [1-{len(accounts)}]: ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(accounts):
                acc = accounts[int(sel) - 1]
                token = acc.get("token") or "<MISSING_TOKEN>"
                ad = build_auth_data(
                    android_id=android_id,
                    email=acc["email"],
                    token=token,
                    gms_version=gms_version,
                    sdk_version=sdk_version
                )
                print(f"\n[+] auth_data for {acc['email']}:")
                print(ad)
                print(f'\n[+] Command:\ngpmc /path/to/media.jpg --auth_data "{ad}"')
                if not "<MISSING_" in ad:
                    print(ACTIVATION_NOTICE)
            else:
                print("[-] Invalid selection.")
            input("\nPress Enter to continue...")

        elif choice == "4":
            print("\n[*] Querying connected device via ADB...")
            ctx = resolve_device_context(force_adb_refresh=True)
            if ctx.get("android_id"):
                print(f"[+] Retrieved live Android ID: {ctx['android_id']}")
                print(f"[+] Retrieved live SDK Version: {ctx.get('sdk_version')}")
                print(f"[+] Retrieved live GMS Version: {ctx.get('gms_version')}")
                print("[+] Saved to .device_cache.json for future runs!")
            else:
                print("[-] Could not detect device over ADB. Please ensure USB debugging is enabled and authorized.")
            input("\nPress Enter to continue...")

        elif choice == "5":
            print("\n--- MANUAL DEVICE INFO ENTRY ---")
            print("[*] Tip: You can find your Android ID in MicroG RE app: Accounts > Device registration.")
            val_id = input(f"Enter Android ID (hex) [{ctx.get('android_id') or ''}]: ").strip()
            if val_id:
                ctx["android_id"] = val_id

            val_sdk = input(f"Enter Android SDK Version (e.g. 34, 35, 36) [{ctx.get('sdk_version') or ''}]: ").strip()
            if val_sdk:
                ctx["sdk_version"] = val_sdk

            val_gms = input(f"Enter MicroG Version Code [{ctx.get('gms_version') or '255070225'}]: ").strip()
            if val_gms:
                ctx["gms_version"] = val_gms

            ctx["source"] = "manual"
            save_device_cache(ctx)
            print("[+] Manual device configuration saved to .device_cache.json!")
            input("\nPress Enter to continue...")

        elif choice == "6":
            clear_device_cache()
            ctx = resolve_device_context(force_adb_refresh=True)
            input("\nPress Enter to continue...")

        elif choice == "7":
            print("\n[*] Starting ADB logcat listener for Google Photos auth...")
            line = capture_token_from_adb()
            if line:
                print("[+] Captured request from ADB logcat!")
                m = re.search(r"Token=(aas_et%2F[A-Za-z0-9_\-%=]+|aas_et/[A-Za-z0-9_\-%=]+)", line)
                if m:
                    token_val = urllib.parse.unquote(m.group(1))
                    print(f"[+] Extracted Token: {token_val[:30]}...")
            else:
                print("[-] No auth request captured. Did you open Google Photos and trigger an action?")
            input("\nPress Enter to continue...")


# ==============================================================================
# Non-Interactive CLI Mode
# ==============================================================================
def run_cli(args):
    ctx = resolve_device_context(args)
    android_id = ctx.get("android_id") or "<MISSING_ANDROID_ID>"
    sdk_version = ctx.get("sdk_version") or "<MISSING_SDK_VERSION>"
    gms_version = ctx.get("gms_version") or "<MISSING_GMS_VERSION>"

    dump_dir = Path(args.dump_dir) if args.dump_dir else None
    accounts = resolve_accounts(args, dump_dir)

    if args.email:
        accounts = [a for a in accounts if a["email"].lower() == args.email.lower()]
        if not accounts:
            accounts = [{"email": args.email, "display_name": "", "gaia_id": "", "token": args.token}]

    results = []
    for acc in accounts:
        email = acc["email"]
        token = acc.get("token") or "<MISSING_TOKEN>"
        auth_data = build_auth_data(
            android_id=android_id,
            email=email,
            token=token,
            gms_version=gms_version,
            sdk_version=sdk_version
        )
        print(f"\n--- Account: {email} ---")
        if "<MISSING_" in auth_data:
            print("[-] Incomplete parameters:")
            print(auth_data)
        else:
            print("[+] Generated auth_data string:")
            print(auth_data)
            results.append((email, auth_data))

    if args.out and results:
        with open(Path(args.out), "w", encoding="utf-8") as fp:
            fp.write(
                "# ==============================================================================\n"
                "# GPMC AUTH TOKENS\n"
                "# NOTE FOR NEW ACCOUNTS: Open Google Photos (Morphe) on your phone once,\n"
                "# switch to the account, enable backup, and let it load for ~30 seconds\n"
                "# before uploading with GPMC, otherwise Google will timeout upload requests.\n"
                "# ==============================================================================\n\n"
            )
            for email, ad in results:
                fp.write(f"# {email}\n{ad}\n\n")
        print(f"\n[+] Saved auth_data to {args.out}")

    if results:
        print(ACTIVATION_NOTICE)


def main():
    parser = argparse.ArgumentParser(description="Parse MicroG dump and extract GPMC auth entries")
    parser.add_argument("--dump-dir", default=None, help="Path to MicroG dump folder")
    parser.add_argument("--android-id", default=None, help="Device Android ID (hex).")
    parser.add_argument("--sdk-version", default=None, help="Android SDK version.")
    parser.add_argument("--gms-version", default=None, help="MicroG versionCode.")
    parser.add_argument("--accounts-db", default=None, help="Path to accounts_ce.db.")
    parser.add_argument("--token", default=None, help="Master Token (aas_et/...).")
    parser.add_argument("--email", default=None, help="Filter for specific email.")
    parser.add_argument("--adb", action="store_true", help="Listen live via ADB logcat to capture Token.")
    parser.add_argument("--out", default=None, help="Save generated auth_data to file.")
    parser.add_argument("--cli", action="store_true", help="Force non-interactive CLI mode.")

    args = parser.parse_args()

    # If user provided specific flags, run in CLI mode. If run without args, launch TUI.
    has_cli_flags = any([
        args.cli, args.token, args.email, args.out, args.adb,
        args.android_id, args.sdk_version, args.gms_version, args.dump_dir
    ])

    if has_cli_flags:
        run_cli(args)
    else:
        run_tui()

if __name__ == "__main__":
    main()
