#!/usr/bin/env python3
import os
import sys
import time
import urllib.request
import subprocess
import json
import argparse

def check_curl():
    try:
        subprocess.run(["curl", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        print("Error: 'curl' is not installed or not in PATH. This script requires 'curl' to handle multipart uploads.", file=sys.stderr)
        sys.exit(1)

def get_device_info(ip):
    url = f"http://{ip}/json/info"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                return data
    except Exception:
        pass
    return None

def wait_for_device(ip, timeout=90, expected_online=True):
    print(f"Waiting for device to be {'online' if expected_online else 'offline'} (timeout: {timeout}s)...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        info = get_device_info(ip)
        is_online = info is not None
        if is_online == expected_online:
            if is_online:
                print(f"Device is online. Version: {info.get('ver')}, Release: {info.get('release')}")
            else:
                print("Device went offline.")
            return info
        time.sleep(2)
    return None

def download_file(url, filepath):
    if os.path.exists(filepath):
        print(f"File {os.path.basename(filepath)} already exists locally. Skipping download.")
        return filepath
    
    print(f"Downloading {url} to {filepath}...")
    try:
        # Use a browser-like user agent to avoid potential blocks
        opener = urllib.request.build_opener()
        opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
        urllib.request.install_opener(opener)
        urllib.request.urlretrieve(url, filepath)
        print("Download completed successfully.")
        return filepath
    except Exception as e:
        print(f"Failed to download from {url}: {e}", file=sys.stderr)
        sys.exit(1)

def flash_firmware(ip, filepath):
    print(f"Flashing firmware file: {os.path.basename(filepath)}...")
    # Use a multipart form field to skip firmware validation (required for older WLED builds)
    cmd = [
        "curl",
        "-s",
        "-i",
        "-F", "skipValidation=1",
        "-F", f"update=@{filepath}",
        f"http://{ip}/update"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Curl failed with return code {e.returncode}: {e.stderr}", file=sys.stderr)
        return None

def get_latest_version():
    url = "https://api.github.com/repos/wled/WLED/releases/latest"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                tag = data.get("tag_name", "")
                return tag[1:] if tag.startswith("v") else tag
    except Exception as e:
        print(f"Warning: Failed to fetch latest version from GitHub: {e}", file=sys.stderr)
    return None

def main():
    check_curl()
    
    parser = argparse.ArgumentParser(description="WLED Firmware Update Automator for Athom LS4P/ESP-02")
    parser.add_argument("--ip", default="10.0.0.7", help="IP address of the WLED device (default: 10.0.0.7)")
    parser.add_argument("--version", default=None, help="Target WLED version (default: look up latest from GitHub)")
    parser.add_argument("--cache-dir", default="/tmp", help="Directory to save downloaded binaries (default: /tmp)")
    parser.add_argument("--force", action="store_true", help="Force re-flash even if device already at target version")
    args = parser.parse_args()
    
    ip = args.ip
    version = args.version
    if not version:
        print("Looking up latest version from GitHub...")
        version = get_latest_version()
        if not version:
            version = "16.0.1"
            print(f"Fallback to default version {version} (lookup failed).")
        else:
            print(f"Found latest version: {version}")
    cache_dir = args.cache_dir
    os.makedirs(cache_dir, exist_ok=True)
    
    min_file = f"WLED_{version}_ESP02_min.bin.gz"
    full_file = f"WLED_{version}_ESP02.bin.gz"
    min_url = f"https://github.com/wled/WLED/releases/download/v{version}/{min_file}"
    full_url = f"https://github.com/wled/WLED/releases/download/v{version}/{full_file}"
    
    min_path = os.path.join(cache_dir, min_file)
    full_path = os.path.join(cache_dir, full_file)
    
    print("Checking initial device state...")
    info = get_device_info(ip)
    if info is None:
        print(f"Error: Device at {ip} is not reachable on HTTP. Ensure it is powered on and connected.", file=sys.stderr)
        sys.exit(1)
        
    current_ver = info.get("ver")
    current_release = info.get("release")
    print(f"Connected to '{info.get('name')}' (Version: {current_ver}, Release: {current_release})")
    
    if current_ver == version and current_release == "ESP02":
        print(f"Device is already running the target version {version} ({current_release}).")
        if args.force:
            print("Force flag provided – proceeding with re-flash.")
        else:
            confirm = input("Do you want to re-flash anyway? (y/N): ").strip().lower()
            if confirm != 'y':
                print("Aborting update.")
                sys.exit(0)
            
    # Download the required files
    print("\n--- Downloading Firmware Binaries ---")
    download_file(min_url, min_path)
    download_file(full_url, full_path)
    
    # Step 1: Flash minimal build
    print("\n--- STEP 1: Flashing Minimal Build ---")
    if current_release == "ESP02_min" and current_ver == version:
        print("Device is already running the minimal build for the target version. Skipping Step 1.")
    else:
        response = flash_firmware(ip, min_path)
        if not response:
            print("Failed to run curl flash command.", file=sys.stderr)
            sys.exit(1)
            
        if "Update successful" in response or "200 OK" in response:
            print("Minimal build flash initiated successfully.")
        elif "Firmware release name mismatch" in response and "500 Internal Server Error" in response:
            print("Flashing succeeded (detected false-failure 500 mismatch response from gzip post-validation bug).")
        else:
            print(f"Minimal flash failed! Response:\n{response}", file=sys.stderr)
            sys.exit(1)
            
        print("Waiting for reboot...")
        time.sleep(5)
        wait_for_device(ip, timeout=20, expected_online=False)
        
        # Verify device came back online with the minimal release
        min_info = wait_for_device(ip, timeout=90, expected_online=True)
        if min_info is None:
            print("Error: Device did not come back online after minimal flash.", file=sys.stderr)
            sys.exit(1)
            
        if min_info.get("release") != "ESP02_min":
            print(f"Warning: Device is online but release is '{min_info.get('release')}', expected 'ESP02_min'.", file=sys.stderr)
            
    # Step 2: Flash full build
    print("\n--- STEP 2: Flashing Full Build ---")
    response = flash_firmware(ip, full_path)
    if not response:
        print("Failed to run curl flash command.", file=sys.stderr)
        sys.exit(1)
        
    # Check for success or the known gzip post-validation 500 mismatch error bug
    if "Update successful" in response or "200 OK" in response:
        print("Full build flash initiated successfully.")
    elif "Firmware release name mismatch" in response and "500 Internal Server Error" in response:
        print("Flashing succeeded (detected false-failure 500 mismatch response from gzip post-validation bug).")
    else:
        print(f"Full flash failed! Response:\n{response}", file=sys.stderr)
        sys.exit(1)
        
    print("Waiting for reboot...")
    time.sleep(5)
    wait_for_device(ip, timeout=20, expected_online=False)
    
    # Verify device came back online with the full release
    final_info = wait_for_device(ip, timeout=90, expected_online=True)
    if final_info is None:
        print("Error: Device did not come back online after full flash.", file=sys.stderr)
        sys.exit(1)
        
    final_ver = final_info.get("ver")
    final_release = final_info.get("release")
    print(f"\n--- Update Complete ---")
    print(f"Device Name: {final_info.get('name')}")
    print(f"Updated from: {current_ver} ({current_release}) ➔ {final_ver} ({final_release})")
    
    if final_ver == version and final_release == "ESP02":
        print("WLED has been successfully updated to the full build!")
    else:
        print(f"Warning: Final state ({final_ver}, {final_release}) does not match target ({version}, ESP02).", file=sys.stderr)

if __name__ == "__main__":
    main()
