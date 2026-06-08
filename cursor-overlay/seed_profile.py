"""
One-time setup: clone your real Chrome profile into the BashIn automation profile
so the Playwright-driven Chrome is already logged into Swiggy / Google.

Chrome 136+ disables remote-debugging on the *default* profile, so the automation
Chrome must use its own user-data-dir. This copies your logins (cookies, Local
State encryption keys, Local Storage) into that dir. Re-run anytime to refresh.

Usage (Chrome will be closed automatically):
    python seed_profile.py            # clones the "Default" profile
    python seed_profile.py "Profile 3"
"""
import os, sys, shutil, subprocess, time

LOCALAPPDATA = os.environ["LOCALAPPDATA"]
SRC_UDD  = os.path.join(LOCALAPPDATA, "Google", "Chrome", "User Data")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DST_UDD  = os.path.join(BASE_DIR, "chrome_bashin_profile")

# Skip cache-like dirs (huge, not needed for login) at any depth
EXCLUDE_DIRS = {
    "Cache", "Code Cache", "GPUCache", "DawnCache", "GraphiteDawnCache",
    "GrShaderCache", "ShaderCache", "Service Worker", "Crashpad",
    "Crash Reports", "Component CRX Cache", "extensions_crx_cache",
    "optimization_guide_model_store", "segmentation_platform",
    "blob_storage", "Download Service",
}
EXCLUDE_FILES = {"SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"}


def kill_chrome():
    print("Closing Chrome...")
    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True)
    time.sleep(2.5)


def copy_filtered(src, dst):
    os.makedirs(dst, exist_ok=True)
    for e in os.scandir(src):
        if e.is_dir():
            if e.name in EXCLUDE_DIRS:
                continue
            copy_filtered(e.path, os.path.join(dst, e.name))
        elif e.name not in EXCLUDE_FILES:
            try:
                shutil.copy2(e.path, os.path.join(dst, e.name))
            except Exception as ex:
                print("  skip", e.name, "-", ex)


def main():
    profile = sys.argv[1] if len(sys.argv) > 1 else "Default"
    src_profile = os.path.join(SRC_UDD, profile)
    if not os.path.isdir(src_profile):
        print("Profile not found:", src_profile)
        sys.exit(1)

    kill_chrome()

    if os.path.isdir(DST_UDD):
        print("Removing old automation profile...")
        shutil.rmtree(DST_UDD, ignore_errors=True)
    os.makedirs(DST_UDD, exist_ok=True)

    # Local State holds the OS-bound cookie encryption keys — required for decrypt
    shutil.copy2(os.path.join(SRC_UDD, "Local State"),
                 os.path.join(DST_UDD, "Local State"))

    # The chosen profile becomes "Default" inside the automation user-data-dir
    print(f"Cloning {profile!r} -> Default (excluding caches)...")
    copy_filtered(src_profile, os.path.join(DST_UDD, "Default"))

    print("Seeded:", DST_UDD)


if __name__ == "__main__":
    main()
