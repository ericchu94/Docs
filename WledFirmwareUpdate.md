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

## Notes

- Always use the `ESP02` build, not the generic `ESP8266` build. The ESP02 module in the Athom LS4P contains an ESP8266, but the `ESP02` build is compiled with settings specific to that module.
- Skipping the minimal build step and flashing the full build directly will fail with an insufficient space error.
