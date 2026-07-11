# WLED: Updating Firmware (Athom LS4P / Neon Light)

## Device Info

- **Device:** Athom LS4P (Neon light)
- **Chip:** ESP8266 (ESP-02 module)
- **Build to use:** `ESP02` — the ESP02 build targets the ESP-02 module specifically, which contains an ESP8266. Prefer it over the generic `ESP8266` build because it is more specific to this hardware.

## Build Naming

Releases follow the pattern:

```
WLED_<version>_ESP02.bin.gz        # full build
WLED_<version>_ESP02_min.bin.gz    # minimal build (stripped-down, smaller)
```

Example for 16.0.0:

```
WLED_16.0.0_ESP02_min.bin.gz
WLED_16.0.0_ESP02.bin.gz
```

## Finding and Downloading Firmware

### Looking Up the Latest Version
* **Stable Releases:** The latest stable version of WLED can always be found on the [WLED GitHub Releases](https://github.com/wled/WLED/releases) page or directly via [Latest Release Link](https://github.com/wled/WLED/releases/latest).
* **GitHub API Lookup:** To check the version programmatically, query the GitHub API endpoint:
  ```bash
  curl -s https://api.github.com/repos/wled/WLED/releases/latest | grep tag_name
  ```

### Downloading Binaries
Under the **Assets** section of the desired release version on GitHub, download the two files matching your target version and the `ESP02` module:
1. `WLED_<version>_ESP02_min.bin.gz` (Minimal build)
2. `WLED_<version>_ESP02.bin.gz` (Full build)

*Example Download Links for v16.0.1:*
* Minimal: `https://github.com/wled/WLED/releases/download/v16.0.1/WLED_16.0.1_ESP02_min.bin.gz`
* Full: `https://github.com/wled/WLED/releases/download/v16.0.1/WLED_16.0.1_ESP02.bin.gz`

## Why a Two-Step Flash Is Required

The ESP-02 has limited flash storage. The full WLED build is too large to OTA-update directly from an older full build — there is not enough free space on the device to hold both the running firmware and the incoming image simultaneously.

The workaround is to flash the minimal build first, which is small enough to fit. Once the minimal build is running, there is sufficient free space to accept the full build.

Tracked in: https://github.com/wled/WLED/issues/5576

## Update Procedure

### Step 1 — Flash the minimal build

1. Open the WLED web UI for the device.
2. Go to **Config → Security & Updates → Manual OTA Update**.
3. Upload `WLED_<version>_ESP02_min.bin.gz`.
4. Wait for the device to reboot.

### Step 2 — Flash the full build

1. Once the device is back up, open the WLED web UI again.
2. Go to **Config → Security & Updates → Manual OTA Update**.
3. Upload `WLED_<version>_ESP02.bin.gz`.
4. Wait for the device to reboot.

### Step 3 — Verify

Confirm the version under **Config → Security & Updates** or in the top-right info panel of the WLED UI.

## Programmatic/Command-Line OTA Updates

An automated update script [update_wled.py](file:///home/ericchu/pg/Docs/update_wled.py) is available in this directory. It automatically handles binary downloads, sequential stage flashing, and bypasses the quirks detailed below.

If you want to automate or script the update process using `curl`, there are two critical quirks in the WLED web server implementation to keep in mind:

### 1. The `skipValidation` Form Field
Because the firmware release name changes between the steps (`ESP02` ➔ `ESP02_min` ➔ `ESP02`), you must bypass the release name validation gate.
* **Requirement:** Pass `skipValidation=1` as a **multipart form field** in the POST body (not as a URL query parameter).
* **Order matters:** The `skipValidation=1` field must appear **before** the firmware file in the multipart body so the server processes it first.
* **Note on query params:** Passing `?skipValidation=1` in the URL does not reliably work on this firmware version — the server reads the multipart body before the query string in its streaming upload handler.

### 2. Gzip Post-Validation Bug (False Failure Response)
When flashing gzipped firmware (`.bin.gz`), the WLED backend validates the decompressed metadata unconditionally at the end of the upload — **for both steps**.
* **Behavior:** The upload completes and the device reboots into the new firmware. However, the server always returns an HTTP `500 Internal Server Error` with the message `Firmware release name mismatch` when uploading `.gz` files.
* **This applies to both Step 1 and Step 2** — the 500 response is always a false-failure when flashing `.gz` files, not a real error.
* **Handling:** Never treat a `500` response as a definitive flash failure. Instead, wait for the device to reboot and verify the version via `http://<IP>/json/info`.

### Automated Curl Commands

**Step 1: Flash minimal build**
```bash
curl -i \
  -F "skipValidation=1" \
  -F "update=@WLED_<version>_ESP02_min.bin.gz" \
  "http://<IP>/update"
# Returns HTTP 500 (false-failure due to gzip validation bug).
# Device will reboot into minimal build (ESP02_min). Verify via /json/info.
```

**Step 2: Flash full build**
```bash
curl -i \
  -F "skipValidation=1" \
  -F "update=@WLED_<version>_ESP02.bin.gz" \
  "http://<IP>/update"
# Also returns HTTP 500 (same false-failure).
# Device will reboot into full build (ESP02). Verify via /json/info.
```

## Notes

- Always use the `ESP02` build, not the generic `ESP8266` build. The ESP02 module in the Athom LS4P contains an ESP8266, but the `ESP02` build is compiled with settings specific to that module.
- Skipping the minimal build step and flashing the full build directly will fail with an `Not Enough Space` error.
- Both `.gz` flashes always return HTTP `500` — this is expected and can be safely ignored. The real indicator of success is the device coming back online with the correct `release` field in `/json/info`.
- If the device is already running `ESP02_min` when you start the process (e.g. a previous update was interrupted after Step 1), Step 1 will be skipped automatically — you can proceed directly to Step 2.
