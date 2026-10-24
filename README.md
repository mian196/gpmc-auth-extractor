# GPMC Auth Data Extractor

A utility to generate and extract complete, validated `auth_data` strings for [GPMC (Google Photos Mobile Client)](../gpmc/README.md) from MicroG / MicroG RE without needing root access, modifying apps, or repeatedly deleting accounts.

---

## Background & Anatomy of `auth_data`

GPMC requires an `auth_data` URL-encoded string to communicate with Google Photos APIs:

```text
androidId=<DEVICE_HEX_ID>&app=com.google.android.apps.photos&client_sig=24bb24c05e47e0aefa68a58a766179d9b613a600&callerPkg=com.google.android.apps.photos&callerSig=24bb24c05e47e0aefa68a58a766179d9b613a600&device_country=us&Email=<ENCODED_EMAIL>&google_play_services_version=255070225&lang=en_US&oauth2_foreground=1&operatorCountry=us&sdk_version=36&service=oauth2%3Aopenid%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fmobileapps.native%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fphotos.native&source=android&system_partition=1&Token=<ENCODED_MASTER_TOKEN>
```

### Components

| Parameter | Source | Notes |
| :--- | :--- | :--- |
| `androidId` | MicroG Checkin / Device Registration | Unique to your device/MicroG installation. |
| `Token` | MicroG Account Password | Google's master token (`aas_et/...`). |
| `Email` | MicroG Account | Target account email address. |
| `client_sig` / `callerSig` | Morphe / ReVanced Signature | `24bb24c05e47e0aefa68a58a766179d9b613a600` (Defined in [GmsCoreSupportPatch.kt](https://github.com/RookieEnough/De-Vanced/blob/main/patches/src/main/kotlin/app/morphe/patches/googlephotos/misc/gms/GmsCoreSupportPatch.kt#L36)). |
| `app` / `callerPkg` | Package Name | `com.google.android.apps.photos` (constant). |
| `service` | Google OAuth Scopes | `oauth2:openid ... photos.native` (constant). |

---

## Standard Workflow (Anytime You Add/Need Accounts)

This guide uses the [MicroG RE](https://github.com/MorpheApp/MicroG-RE) app to back up accounts from MicroG RE on your phone.

> [!NOTE]
> **How to get your `androidId`:**
>
> * **From the MicroG RE app itself:** Open **MicroG RE** on your phone > tap **Accounts** > tap **Device registration** > copy the **Android ID** shown there.
> * **Automatic via ADB:** Connect your phone with USB debugging enabled, and the extractor will fetch it automatically.
> * **From existing tokens:** If you already extracted an auth token on this device previously, the script automatically reads the `androidId` from any existing `auth*.txt` in the folder.

### Step 1: Back up Accounts from MicroG RE on Phone

1. Open the **MicroG RE** app on your phone.
2. Click on **Accounts**.
3. Click on **Back up accounts**.
4. Save the generated `microg-accounts-YYYYMMDD-HHMM.json` file and copy it into this project folder.

### Step 2: Run the Extractor (Interactive TUI Menu)

Place the exported JSON file in the same directory as `extract_gpmc_auth.py` and run:

```bash
python extract_gpmc_auth.py
```

### Step 3: 1-Time Account Activation in Google Photos (Crucial!)

> [!IMPORTANT]
> **Why this is required:**
> Google's photo backup infrastructure will **reject / timeout** server-side photo upload requests (`HTTPSConnectionPool: Read timed out`) if an account has never initiated backup through the mobile client.
> **For any newly added account:**
>
> 1. Open the patched **Google Photos (Morphe)** app on your device.
> 2. Switch to the newly added account from the top-right profile icon.
> 3. Click **Turn on backup** (or confirm backup settings) and allow it to sync/load for **~30 seconds**.
> 4. Once Google's servers register the device handshake, your extracted `auth_data` token will work seamlessly with `gpmc` without timeouts or errors!

```bash
python extract_gpmc_auth.py
```

This launches the interactive terminal menu:

```text
====================================================================
          GPMC AUTH DATA EXTRACTOR - INTERACTIVE MENU
====================================================================
[Device Status] (Android Device)
  Android ID:   0123456789abcdef
  SDK Version:  36
  GMS Version:  255070225
  Source:       Cached in .device_cache.json
  [!] WARNING:  If switching to a new device, choose Option 6 to clear cache!
--------------------------------------------------------------------
[Accounts Status] (2 detected in folder)
  [1] user1@example.com (Master token ready)
  [2] user2@example.com (Master token ready)
====================================================================
 [1] Generate GPMC Auth Tokens for All Accounts
 [2] Save All Auth Tokens to File (gpmc_auth_tokens.txt)
 [3] Generate Token for a Specific Account
 [4] Fetch / Refresh Device Info from Connected Phone (via ADB)
 [5] Manually Enter Device Info (Android ID, SDK, GMS)
 [6] Clear Cached Device Info (.device_cache.json)
 [7] Capture Live Auth Token via ADB Logcat (Google Photos)
 [0] Exit
====================================================================
```

### Automatic Device Caching (`.device_cache.json`)

* Once device info (`androidId`, `sdk_version`, `gms_version`) is fetched via ADB or manual entry, it is automatically cached in `.device_cache.json` so you do not need to re-query your phone in future sessions.
* **Important:** If you switch to a different phone or perform a fresh install, select **Option [6] Clear Cached Device Info** in the menu (or delete `.device_cache.json`) to fetch fresh properties from your new phone.

---

### Non-Interactive CLI Mode

To run headlessly without the interactive menu (e.g. in automated scripts):

```bash
python extract_gpmc_auth.py --cli --out gpmc_auth_tokens.txt
```

---

## Scenarios & Advanced Usage

### 1. Existing Device (Default)

When running on the same device where MicroG was already backed up, the script uses your existing `androidId` automatically:

```bash
python extract_gpmc_auth.py
```

### 2. New Device or Fresh MicroG Reinstallation

When MicroG is reinstalled or set up on a new phone, Google assigns a new `androidId`. Choose one of the following methods:

#### Method A: Automatic ADB Detection (Recommended)

Connect your device to your PC with USB debugging enabled, then run:

```bash
python extract_gpmc_auth.py
```

The script automatically detects the device over ADB and queries:

1. **Device Android ID** from MicroG's GServices Content Provider:

   ```bash
   adb shell content query --uri content://app.revanced.android.gsf.gservices/prefix/android_id
   ```

2. **Android SDK Version**:

   ```bash
   adb shell getprop ro.build.version.sdk
   ```

3. **MicroG Version Code**:

   ```bash
   adb shell dumpsys package app.revanced.android.gms
   ```

#### Method B: Copy `androidId` Directly from the MicroG RE App (No ADB Needed)

You can view and copy your Android ID straight from the MicroG RE app:

1. Open the **MicroG RE** app on your phone.
2. Tap on **Accounts**.
3. Under Accounts, tap **Device registration**.
4. You will see your 16-character hexadecimal **Android ID** (e.g. `0123456789abcdef`).
5. You can enter this into the interactive menu (**Option [5] Manually Enter Device Info**)—which saves it permanently to `.device_cache.json`—or pass it via CLI:

```bash
python extract_gpmc_auth.py --android-id <YOUR_HEX_ANDROID_ID>
```

#### Method C: Provide a Fresh MicroG Backup Folder

If you backed up MicroG via your OEM's backup tool (e.g. Xiaomi, Huawei, OnePlus):

```bash
python extract_gpmc_auth.py --dump-dir path/to/app.revanced.android.gms
```

---

### 3. Real-Time Live Capture via ADB Logcat (Alternative)

If you prefer capturing the live token directly from Google Photos while the device is connected:

```bash
python extract_gpmc_auth.py --adb
```

Open Google Photos on your device. The script will catch the outgoing auth request, extract the token and `androidId`, and print the complete `auth_data` string.

---

### 4. Filter by Specific Account

To generate the auth string for a single account among multiple registered accounts:

```bash
python extract_gpmc_auth.py --email "user@example.com"
```

---

## CLI Reference

```text
usage: extract_gpmc_auth.py [-h] [--dump-dir DUMP_DIR] [--android-id ANDROID_ID]
                            [--accounts-db ACCOUNTS_DB] [--token TOKEN]
                            [--email EMAIL] [--adb] [--out OUT]

options:
  -h, --help            Show help message and exit
  --dump-dir DUMP_DIR   Path to MicroG dump folder (default: app.revanced.android.gms)
  --android-id ANDROID_ID
                        Specify device Android ID in hex
  --accounts-db ACCOUNTS_DB
                        Path to system accounts_ce.db (if dumped)
  --token TOKEN         Supply master token (aas_et/...) manually
  --email EMAIL         Filter output for a specific email
  --adb                 Listen live via ADB logcat to capture auth requests
  --out OUT             Save generated auth_data to a file
```

---

## Verifying in GPMC

Once you obtain your `auth_data` string:

### CLI Upload

```bash
gpmc "/path/to/media.jpg" --progress --auth_data "androidId=...&Token=..."
```

### Environment Variable

```bash
export GP_AUTH_DATA="androidId=...&Token=..."
gpmc "/path/to/media.jpg" --progress
```

### Python API

```python
from gpmc import Client

auth_data = "androidId=...&Token=..."
client = Client(auth_data=auth_data)
output = client.upload(target="/path/to/media.jpg", show_progress=True)
print(output)
```
