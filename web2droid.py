#!/usr/bin/env python3
"""web2droid - turn an HTML/CSS/JS project into a signed Android APK / AAB.

Single cross-platform script (Windows, Linux, macOS; x86_64 and arm64).
Uses only the Python standard library. Downloads a portable JDK, the Android
command-line tools and bundletool into ~/.web2droid (override with W2D_HOME),
verifying TLS and checksums for every download.
"""
import argparse
import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from urllib.parse import quote

__version__ = "2.0.0"

# --- CONFIGURATION ---------------------------------------------------------
W2D_HOME = os.path.abspath(os.environ.get("W2D_HOME") or os.path.join(os.path.expanduser("~"), ".web2droid"))
SDK_DIR = os.path.join(W2D_HOME, "sdk")
JDK_DIR = os.path.join(W2D_HOME, "jdk")
BUNDLETOOL_JAR = os.path.join(W2D_HOME, "bundletool.jar")
DEBUG_KEYSTORE = os.path.join(W2D_HOME, "debug.keystore")

JDK_MAJOR = 21
MIN_JDK_MAJOR = 17
BUILD_TOOLS_VERSION = "35.0.0"
PLATFORM_API = 35          # compileSdk / targetSdk (Google Play requirement)
MIN_SDK = 24

SDK_REPO_URL = "https://dl.google.com/android/repository/repository2-3.xml"
SDK_REPO_BASE = "https://dl.google.com/android/repository/"
ADOPTIUM_API = "https://api.adoptium.net/v3/assets/latest/{major}/hotspot?architecture={arch}&image_type=jdk&os={os}&vendor=eclipse"
BUNDLETOOL_API = "https://api.github.com/repos/google/bundletool/releases/latest"

ASSET_HOST = "appassets.androidplatform.net"
CONFIG_FILE = "web2droid.json"

EXCLUDED_DIRS = {".git", ".hg", ".svn", "__pycache__", ".idea", ".vscode", ".web2droid"}
EXCLUDED_FILE_PATTERNS = [r".*\.apk$", r".*\.aab$", r".*\.idsig$", r".*\.jks$", r".*\.keystore$",
                          r"^web2droid\.json$", r"^\.DS_Store$", r"^Thumbs\.db$"]

JAVA_KEYWORDS = set("""abstract assert boolean break byte case catch char class const continue default do
double else enum extends final finally float for goto if implements import instanceof int interface long
native new package private protected public return short static strictfp super switch synchronized this
throw throws transient try void volatile while true false null var record yield""".split())

IS_WINDOWS = os.name == "nt"
EXE = ".exe" if IS_WINDOWS else ""


class BuildError(Exception):
    """Fatal, user-facing error."""


def log(msg):
    print(msg, flush=True)


def run(cmd, env=None, quiet=False, input_bytes=None):
    """Run a command without a shell; raise BuildError with its output on failure."""
    try:
        res = subprocess.run(cmd, env=env, input=input_bytes,
                             stdout=subprocess.PIPE if quiet else None,
                             stderr=subprocess.STDOUT if quiet else None)
    except OSError as e:
        raise BuildError(f"Cannot run {cmd[0]}: {e}")
    if res.returncode != 0:
        out = res.stdout.decode("utf-8", "replace") if quiet and res.stdout else ""
        raise BuildError(f"Command failed ({res.returncode}): {os.path.basename(cmd[0])} {' '.join(cmd[1:3])} ...\n{out}".rstrip())
    return res


# --- DOWNLOADS (TLS verified + checksums) ----------------------------------
def _open(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"web2droid/{__version__}"})
    try:
        return urllib.request.urlopen(req, timeout=60)
    except urllib.error.URLError as e:
        hint = ""
        if "CERTIFICATE_VERIFY_FAILED" in str(e) and sys.platform == "darwin":
            hint = "\nOn macOS run 'Install Certificates.command' from your Python folder."
        raise BuildError(f"Download failed: {url}\n{e}{hint}")


def fetch_text(url):
    with _open(url) as r:
        return r.read().decode("utf-8")


def download(url, dest, algo=None, expected=None):
    log(f"--> Downloading {url.rsplit('/', 1)[-1]}")
    h = hashlib.new(algo) if algo else None
    tmp = dest + ".part"
    with _open(url) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            if h:
                h.update(chunk)
    if h and expected:
        if h.hexdigest().lower() != expected.lower():
            os.remove(tmp)
            raise BuildError(f"Checksum mismatch for {url} ({algo}). Download aborted.")
    elif not expected:
        log("    [!] No published checksum available; relying on HTTPS only.")
    os.replace(tmp, dest)


def extract_zip(path, dest):
    """Extract a zip keeping Unix executable bits (zipfile drops them)."""
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            out = z.extract(info, dest)
            mode = (info.external_attr >> 16) & 0o777
            if mode and not IS_WINDOWS:
                os.chmod(out, mode)


def extract_tar(path, dest):
    with tarfile.open(path) as t:
        if hasattr(tarfile, "data_filter"):
            t.extractall(dest, filter="data")
        else:
            for m in t.getmembers():
                target = os.path.realpath(os.path.join(dest, m.name))
                if not target.startswith(os.path.realpath(dest) + os.sep):
                    raise BuildError(f"Unsafe path in archive: {m.name}")
            t.extractall(dest)


def host_info():
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x64" if machine in ("x86_64", "amd64") else None
    if sys.platform.startswith("win"):
        osname, sdk_os = "windows", "windows"
    elif sys.platform == "darwin":
        osname, sdk_os = "mac", "macosx"
    elif sys.platform.startswith("linux"):
        osname, sdk_os = "linux", "linux"
    else:
        raise BuildError(f"Unsupported OS: {sys.platform}")
    if not arch:
        raise BuildError(f"Unsupported CPU architecture: {machine}")
    return osname, sdk_os, arch


# --- TOOLCHAIN -------------------------------------------------------------
class Toolchain:
    def __init__(self, update=False, accept_licenses=False):
        self.update = update
        self.accept_licenses = accept_licenses
        self.java_home = None

    # paths
    def jbin(self, name):
        return os.path.join(self.java_home, "bin", name + EXE)

    @property
    def build_tools(self):
        return os.path.join(SDK_DIR, "build-tools", BUILD_TOOLS_VERSION)

    @property
    def aapt2(self):
        return os.path.join(self.build_tools, "aapt2" + EXE)

    @property
    def zipalign(self):
        return os.path.join(self.build_tools, "zipalign" + EXE)

    @property
    def android_jar(self):
        return os.path.join(SDK_DIR, "platforms", f"android-{PLATFORM_API}", "android.jar")

    def env(self, extra=None):
        e = dict(os.environ)
        e["JAVA_HOME"] = self.java_home
        e["PATH"] = os.path.join(self.java_home, "bin") + os.pathsep + e.get("PATH", "")
        if extra:
            e.update(extra)
        return e

    def ensure(self):
        os.makedirs(W2D_HOME, exist_ok=True)
        log(f"[*] Toolchain directory: {W2D_HOME}")
        self.ensure_java()
        self.ensure_sdk()
        self.ensure_bundletool()

    # Java
    @staticmethod
    def _jdk_major(home):
        javac = os.path.join(home, "bin", "javac" + EXE)
        if not (os.path.isfile(javac) and os.path.isfile(os.path.join(home, "bin", "jarsigner" + EXE))):
            return 0
        try:
            out = subprocess.run([javac, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.decode()
        except OSError:
            return 0
        m = re.search(r"javac (\d+)", out)
        return int(m.group(1)) if m else 0

    def ensure_java(self):
        env_home = os.environ.get("JAVA_HOME")
        if env_home and not self.update and self._jdk_major(env_home) >= MIN_JDK_MAJOR:
            self.java_home = env_home
            log(f"[OK] Using JDK from JAVA_HOME: {env_home}")
            return
        if not self.update and self._jdk_major(JDK_DIR) >= MIN_JDK_MAJOR:
            self.java_home = JDK_DIR
            log("[OK] Portable JDK found.")
            return

        osname, _, arch = host_info()
        log(f"[*] Installing portable Eclipse Temurin JDK {JDK_MAJOR} (no admin rights needed)...")
        meta = json.loads(fetch_text(ADOPTIUM_API.format(major=JDK_MAJOR, arch=arch, os=osname)))
        if not meta:
            raise BuildError("No Temurin JDK build available for this platform.")
        pkg = meta[0]["binary"]["package"]
        archive = os.path.join(W2D_HOME, pkg["name"])
        download(pkg["link"], archive, "sha256", pkg["checksum"])

        staging = tempfile.mkdtemp(dir=W2D_HOME, prefix="jdk-")
        try:
            (extract_zip if archive.endswith(".zip") else extract_tar)(archive, staging)
            top = [d for d in os.listdir(staging) if os.path.isdir(os.path.join(staging, d))]
            if len(top) != 1:
                raise BuildError("Unexpected JDK archive layout.")
            home = os.path.join(staging, top[0])
            if os.path.isdir(os.path.join(home, "Contents", "Home")):  # macOS bundle
                home = os.path.join(home, "Contents", "Home")
            shutil.rmtree(JDK_DIR, ignore_errors=True)
            shutil.move(home, JDK_DIR)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            os.remove(archive)
        if self._jdk_major(JDK_DIR) < MIN_JDK_MAJOR:
            raise BuildError("JDK installation is broken.")
        self.java_home = JDK_DIR
        log("[OK] Portable JDK installed.")

    # Android SDK
    def sdkmanager(self):
        return os.path.join(SDK_DIR, "cmdline-tools", "latest", "bin", "sdkmanager" + (".bat" if IS_WINDOWS else ""))

    def install_cmdline_tools(self):
        _, sdk_os, _ = host_info()
        log("--> Resolving Android command-line tools from Google's repository index...")
        root = ET.fromstring(fetch_text(SDK_REPO_URL))
        archive = None
        for pkg in root.iter():
            if pkg.tag.split("}")[-1] == "remotePackage" and pkg.get("path") == "cmdline-tools;latest":
                for arc in pkg.iter():
                    if arc.tag.split("}")[-1] != "archive":
                        continue
                    host = next((c.text for c in arc.iter() if c.tag.split("}")[-1] == "host-os"), None)
                    if host == sdk_os:
                        url = next(c.text for c in arc.iter() if c.tag.split("}")[-1] == "url")
                        sha1 = next(c.text for c in arc.iter() if c.tag.split("}")[-1] == "checksum")
                        archive = (url, sha1)
        if not archive:
            raise BuildError("Could not find command-line tools for this OS in the repository index.")
        url, sha1 = archive
        zpath = os.path.join(W2D_HOME, "cmdline-tools.zip")
        download(SDK_REPO_BASE + url, zpath, "sha1", sha1)
        staging = tempfile.mkdtemp(dir=W2D_HOME, prefix="cmdline-")
        try:
            extract_zip(zpath, staging)
            dest = os.path.join(SDK_DIR, "cmdline-tools", "latest")
            shutil.rmtree(dest, ignore_errors=True)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(os.path.join(staging, "cmdline-tools"), dest)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            os.remove(zpath)

    def ensure_sdk(self):
        packages = [f"build-tools;{BUILD_TOOLS_VERSION}", f"platforms;android-{PLATFORM_API}"]
        if not self.update and os.path.isfile(self.aapt2) and os.path.isfile(self.android_jar):
            log("[OK] Android SDK found.")
            return
        if self.update or not os.path.isfile(self.sdkmanager()):
            self.install_cmdline_tools()

        android_cli = os.path.join(SDK_DIR, "cmdline-tools", "latest", "bin", "android" + EXE)
        if os.path.isfile(android_cli):
            # New Android CLI (replaces the deprecated sdkmanager; no separate license step).
            log("--> Installing build-tools and platform (Android CLI)...")
            pkgs = [p.replace(";", "/") for p in packages]
            run([android_cli, f"--sdk={SDK_DIR}", "sdk", "install"] + pkgs, env=self.env(), quiet=True)
            if not (os.path.isfile(self.aapt2) and os.path.isfile(self.android_jar)):
                raise BuildError("SDK installation incomplete.")
            log("[OK] Android SDK ready.")
            return

        sdkm = [self.sdkmanager(), f"--sdk_root={SDK_DIR}"]
        log("--> Android SDK licenses must be accepted to continue.")
        if self.accept_licenses:
            log("    (--accept-licenses given: accepting automatically)")
            run(sdkm + ["--licenses"], env=self.env(), quiet=True, input_bytes=b"y\n" * 100)
        elif sys.stdin.isatty():
            run(sdkm + ["--licenses"], env=self.env())
        else:
            raise BuildError("Non-interactive session: re-run with --accept-licenses to accept the Android SDK licenses.")

        log("--> Installing build-tools and platform...")
        run(sdkm + packages + (["--update"] if self.update else []), env=self.env(), quiet=True)
        if not (os.path.isfile(self.aapt2) and os.path.isfile(self.android_jar)):
            raise BuildError("SDK installation incomplete (licenses not accepted?).")
        log("[OK] Android SDK ready.")

    # bundletool
    def ensure_bundletool(self):
        if os.path.isfile(BUNDLETOOL_JAR) and not self.update:
            return
        log("--> Resolving latest bundletool release...")
        rel = json.loads(fetch_text(BUNDLETOOL_API))
        asset = next((a for a in rel.get("assets", []) if re.match(r"bundletool-all-.*\.jar$", a["name"])), None)
        if not asset:
            raise BuildError("bundletool jar not found in the latest release.")
        digest = asset.get("digest") or ""
        sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
        download(asset["browser_download_url"], BUNDLETOOL_JAR, "sha256", sha)


# --- NAMES / ESCAPING ------------------------------------------------------
PACKAGE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+\- ]{0,49}$")


def package_segment(text):
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9_]", "", s) or "app"
    if s[0].isdigit():
        s = "a" + s
    if s in JAVA_KEYWORDS:
        s += "_"
    return s


def validate_package(pkg):
    if not PACKAGE_RE.match(pkg):
        raise BuildError(f"Invalid package name '{pkg}'. Use e.g. com.yourname.myapp")
    bad = [p for p in pkg.split(".") if p in JAVA_KEYWORDS]
    if bad:
        raise BuildError(f"Package name segment(s) {bad} are Java keywords.")
    if pkg.startswith("com.example."):
        log("[!] Package starts with 'com.example' - Google Play rejects such apps. Use --package.")
    return pkg


def xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def aapt_string(s):
    """Escape a value for res/values/strings.xml (aapt2 rules + XML)."""
    s = " ".join(s.split())
    s = s.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
    if s[:1] in ("@", "?"):
        s = "\\" + s
    return xml_escape(s)


def safe_filename(s):
    return re.sub(r"[^\w.-]+", "_", s, flags=re.UNICODE).strip("._") or "app"


# --- CODE TEMPLATES --------------------------------------------------------
JAVA_TEMPLATE = r"""package __PACKAGE__;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;
__BLE_IMPORTS__

public class MainActivity extends Activity {
    static final String HOST = "__HOST__";
    static final String START_URL = "https://" + HOST + "/__START__";
    WebView webView;
    volatile boolean onAppOrigin = true;
__BLE_FIELDS__

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        webView = new WebView(this);
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        webView.setWebViewClient(new AppClient());
        webView.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() {
            @Override
            public WindowInsets onApplyWindowInsets(View v, WindowInsets insets) {
                if (Build.VERSION.SDK_INT >= 30) {
                    Insets i = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                    v.setPadding(i.left, i.top, i.right, i.bottom);
                } else {
                    v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                                 insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
                }
                return insets;
            }
        });
__BLE_INIT__
        setContentView(webView);
        if (savedInstanceState != null) webView.restoreState(savedInstanceState);
        else webView.loadUrl(START_URL);
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        webView.saveState(out);
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    static boolean isAppUrl(Uri u) {
        return u != null && "https".equals(u.getScheme()) && HOST.equals(u.getHost());
    }

    class AppClient extends WebViewClient {
        @Override
        public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
            onAppOrigin = isAppUrl(Uri.parse(url));
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
            Uri u = req.getUrl();
            if (isAppUrl(u)) return false;
            // Everything that is not part of the app opens outside the WebView.
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, u));
            } catch (ActivityNotFoundException e) { /* no handler */ }
            return true;
        }

        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
            Uri u = req.getUrl();
            if (!isAppUrl(u)) return null;
            String path = u.getPath();
            if (path == null || path.isEmpty()) path = "/";
            if (path.endsWith("/")) path += "index.html";
            path = path.substring(1);
            if (path.contains("..")) return notFound();
            try {
                InputStream in = getAssets().open("www/" + path);
                String mime = mimeType(path);
                boolean text = mime.startsWith("text/") || mime.endsWith("javascript") || mime.endsWith("json") || mime.endsWith("xml");
                return new WebResourceResponse(mime, text ? "UTF-8" : null, in);
            } catch (IOException e) {
                return notFound();
            }
        }
    }

    static WebResourceResponse notFound() {
        Map<String, String> h = new HashMap<>();
        return new WebResourceResponse("text/plain", "UTF-8", 404, "Not Found", h, new ByteArrayInputStream(new byte[0]));
    }

    static String mimeType(String path) {
        String p = path.toLowerCase();
        int dot = p.lastIndexOf('.');
        String ext = dot >= 0 ? p.substring(dot + 1) : "";
        switch (ext) {
            case "html": case "htm": return "text/html";
            case "css": return "text/css";
            case "js": case "mjs": return "text/javascript";
            case "json": case "map": return "application/json";
            case "webmanifest": return "application/manifest+json";
            case "xml": return "application/xml";
            case "txt": return "text/plain";
            case "svg": return "image/svg+xml";
            case "png": return "image/png";
            case "jpg": case "jpeg": return "image/jpeg";
            case "gif": return "image/gif";
            case "webp": return "image/webp";
            case "ico": return "image/x-icon";
            case "avif": return "image/avif";
            case "wasm": return "application/wasm";
            case "woff": return "font/woff";
            case "woff2": return "font/woff2";
            case "ttf": return "font/ttf";
            case "otf": return "font/otf";
            case "mp3": return "audio/mpeg";
            case "ogg": return "audio/ogg";
            case "wav": return "audio/wav";
            case "mp4": return "video/mp4";
            case "webm": return "video/webm";
            default: return "application/octet-stream";
        }
    }
__BLE_CODE__
}
"""

BLE_IMPORTS = r"""import android.Manifest;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanResult;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Handler;
import android.os.Looper;
import android.webkit.JavascriptInterface;
import java.util.UUID;
import org.json.JSONObject;"""

BLE_FIELDS = r"""    BluetoothAdapter bluetoothAdapter;
    BluetoothLeScanner scanner;
    BluetoothGatt gatt;
    boolean scanning = false;
    final Handler handler = new Handler(Looper.getMainLooper());"""

BLE_INIT = r"""        BluetoothManager bm = (BluetoothManager) getSystemService(Context.BLUETOOTH_SERVICE);
        if (bm != null) bluetoothAdapter = bm.getAdapter();
        webView.addJavascriptInterface(new BLEBridge(), "AndroidBLE");"""

BLE_CODE = r"""
    // ---- BLE bridge: window.AndroidBLE (only usable by the app's own pages) ----
    void callJs(final String fn, final String... args) {
        handler.post(new Runnable() {
            @Override
            public void run() {
                StringBuilder sb = new StringBuilder("if(window." + fn + ")window." + fn + "(");
                for (int i = 0; i < args.length; i++) {
                    if (i > 0) sb.append(',');
                    sb.append(JSONObject.quote(args[i]));
                }
                sb.append(");");
                webView.evaluateJavascript(sb.toString(), null);
            }
        });
    }

    void logToJS(String msg) { callJs("onBleLog", msg); }

    String[] blePermissions() {
        if (Build.VERSION.SDK_INT >= 31)
            return new String[]{Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT};
        return new String[]{Manifest.permission.ACCESS_FINE_LOCATION};
    }

    boolean hasBlePermissions() {
        for (String p : blePermissions())
            if (checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) return false;
        return true;
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        super.onRequestPermissionsResult(code, perms, results);
        callJs("onBlePermissions", String.valueOf(hasBlePermissions()));
    }

    public class BLEBridge {
        // Defence in depth: foreign pages never load in the WebView, but refuse anyway.
        boolean allowed() { return onAppOrigin; }

        @JavascriptInterface
        public boolean hasPermissions() { return allowed() && hasBlePermissions(); }

        @JavascriptInterface
        public void requestPermissions() {
            if (!allowed()) return;
            handler.post(new Runnable() {
                @Override
                public void run() { MainActivity.this.requestPermissions(blePermissions(), 1); }
            });
        }

        @JavascriptInterface
        public void startScan() {
            if (!allowed()) return;
            if (bluetoothAdapter == null || !bluetoothAdapter.isEnabled()) { logToJS("Error: Bluetooth is disabled or not supported."); return; }
            if (!hasBlePermissions()) { logToJS("Error: Missing BLE permissions. Call requestPermissions()."); return; }
            handler.post(new Runnable() {
                @Override
                public void run() {
                    if (scanning) return;
                    scanner = bluetoothAdapter.getBluetoothLeScanner();
                    if (scanner == null) { logToJS("Error: Failed to get BLE scanner."); return; }
                    try {
                        scanner.startScan(scanCallback);
                        scanning = true;
                        logToJS("Scan started.");
                        handler.postDelayed(new Runnable() {
                            @Override
                            public void run() { stopScanInternal(); }
                        }, 10000);
                    } catch (SecurityException e) { logToJS("Scan error: missing permissions."); }
                }
            });
        }

        @JavascriptInterface
        public void stopScan() {
            if (!allowed()) return;
            handler.post(new Runnable() {
                @Override
                public void run() { stopScanInternal(); }
            });
        }

        @JavascriptInterface
        public void connect(final String mac) {
            if (!allowed()) return;
            if (bluetoothAdapter == null || mac == null || !BluetoothAdapter.checkBluetoothAddress(mac)) { logToJS("Connection error: invalid MAC."); return; }
            handler.post(new Runnable() {
                @Override
                public void run() {
                    stopScanInternal();
                    closeGatt();
                    logToJS("Connecting to " + mac + "...");
                    try {
                        BluetoothDevice d = bluetoothAdapter.getRemoteDevice(mac);
                        gatt = d.connectGatt(MainActivity.this, false, gattCallback, BluetoothDevice.TRANSPORT_LE);
                    } catch (SecurityException e) { logToJS("Connection error: missing permissions."); }
                }
            });
        }

        @JavascriptInterface
        public void disconnect() {
            if (!allowed()) return;
            handler.post(new Runnable() {
                @Override
                public void run() {
                    if (gatt != null) { closeGatt(); logToJS("Disconnected."); callJs("onBleDisconnected"); }
                }
            });
        }

        @JavascriptInterface
        public void write(String serviceUuid, String charUuid, String hex) {
            if (!allowed()) return;
            if (gatt == null) { logToJS("Error: No active connection."); return; }
            try {
                byte[] data = hexToBytes(hex);
                BluetoothGattService svc = gatt.getService(UUID.fromString(serviceUuid));
                if (svc == null) { logToJS("Service not found."); return; }
                BluetoothGattCharacteristic c = svc.getCharacteristic(UUID.fromString(charUuid));
                if (c == null) { logToJS("Characteristic not found."); return; }
                boolean ok;
                if (Build.VERSION.SDK_INT >= 33) {
                    ok = gatt.writeCharacteristic(c, data, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) == 0;
                } else {
                    c.setValue(data);
                    ok = gatt.writeCharacteristic(c);
                }
                logToJS("Write " + hex + ": " + (ok ? "OK" : "failed"));
            } catch (SecurityException e) {
                logToJS("Write error: missing permissions.");
            } catch (IllegalArgumentException e) {
                logToJS("Write error: " + e.getMessage());
            }
        }
    }

    void stopScanInternal() {
        if (scanning && scanner != null) {
            try { scanner.stopScan(scanCallback); } catch (SecurityException e) { logToJS("Stop error: missing permissions."); }
            scanning = false;
            logToJS("Scan stopped.");
        }
    }

    void closeGatt() {
        if (gatt == null) return;
        try { gatt.disconnect(); gatt.close(); } catch (SecurityException e) { /* ignore */ }
        gatt = null;
    }

    @Override
    protected void onDestroy() {
        stopScanInternal();
        closeGatt();
        super.onDestroy();
    }

    final ScanCallback scanCallback = new ScanCallback() {
        @Override
        public void onScanResult(int type, ScanResult result) {
            try {
                BluetoothDevice d = result.getDevice();
                String name = d.getName();
                if (name == null || name.isEmpty()) name = "Unknown Device";
                callJs("onBleDeviceFound", name, d.getAddress());
            } catch (SecurityException e) { /* ignore */ }
        }
    };

    final BluetoothGattCallback gattCallback = new BluetoothGattCallback() {
        @Override
        public void onConnectionStateChange(BluetoothGatt g, int status, int newState) {
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                logToJS("Connected. Discovering services...");
                try { g.discoverServices(); } catch (SecurityException e) { /* ignore */ }
                callJs("onBleConnected", g.getDevice().getAddress());
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                logToJS("Disconnected from GATT server.");
                callJs("onBleDisconnected");
            }
        }

        @Override
        public void onServicesDiscovered(BluetoothGatt g, int status) {
            logToJS(status == BluetoothGatt.GATT_SUCCESS ? "Services discovered. Ready." : "Service discovery error: " + status);
        }
    };

    static byte[] hexToBytes(String s) {
        if (s == null) throw new IllegalArgumentException("empty data");
        s = s.replaceAll("\\s", "");
        if (s.length() % 2 != 0 || !s.matches("[0-9a-fA-F]*")) throw new IllegalArgumentException("invalid hex string");
        byte[] out = new byte[s.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        return out;
    }
"""

BLE_PERMISSIONS = """
    <uses-feature android:name="android.hardware.bluetooth_le" android:required="false" />
    <uses-permission android:name="android.permission.BLUETOOTH" android:maxSdkVersion="30" />
    <uses-permission android:name="android.permission.BLUETOOTH_ADMIN" android:maxSdkVersion="30" />
    <uses-permission android:name="android.permission.ACCESS_FINE_LOCATION" android:maxSdkVersion="30" />
    <uses-permission android:name="android.permission.BLUETOOTH_SCAN" android:usesPermissionFlags="neverForLocation" />
    <uses-permission android:name="android.permission.BLUETOOTH_CONNECT" />"""


# --- BUILDER ---------------------------------------------------------------
class AppBuilder:
    def __init__(self, tc, opts):
        self.tc = tc
        self.o = opts
        self.work = tempfile.mkdtemp(prefix="web2droid-")

    def p(self, *parts):
        return os.path.join(self.work, *parts)

    # sources
    def copy_web(self):
        src_root = self.o.web_root
        dest = self.p("assets", "www")
        excluded = [re.compile(x, re.IGNORECASE) for x in EXCLUDED_FILE_PATTERNS]
        out_dir = os.path.realpath(self.o.out_dir)
        total = 0
        for root, dirs, files in os.walk(src_root):
            dirs[:] = [d for d in dirs
                       if d not in EXCLUDED_DIRS and not d.startswith("build_temp_")
                       and os.path.realpath(os.path.join(root, d)) != out_dir]
            rel = os.path.relpath(root, src_root)
            for f in files:
                if any(rx.match(f) for rx in excluded) or any(re.fullmatch(g, f) for g in self.o.exclude):
                    continue
                s = os.path.join(root, f)
                d = os.path.normpath(os.path.join(dest, rel, f))
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
                total += os.path.getsize(s)
        log(f"--> Bundled web assets: {total / 1024:.0f} KiB from {src_root}")
        if total > 150 * 1024 * 1024:
            log("[!] Assets exceed 150 MB - Google Play base module limit. Use --exclude.")

    def copy_icon(self):
        self.icon_ref = "@android:drawable/sym_def_app_icon"
        if not self.o.icon:
            return
        ext = os.path.splitext(self.o.icon)[1].lower()
        if ext not in (".png", ".webp", ".jpg", ".jpeg"):
            raise BuildError("Icon must be a .png, .webp or .jpg file (512x512 recommended).")
        d = self.p("res", "mipmap-xxxhdpi")
        os.makedirs(d, exist_ok=True)
        shutil.copy(self.o.icon, os.path.join(d, "ic_launcher" + (".jpg" if ext == ".jpeg" else ext)))
        self.icon_ref = "@mipmap/ic_launcher"

    def write_resources(self):
        os.makedirs(self.p("res", "values"), exist_ok=True)
        with open(self.p("res", "values", "strings.xml"), "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
                    f'    <string name="app_name">{aapt_string(self.o.name)}</string>\n</resources>\n')

    def write_manifest(self):
        o = self.o
        content = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{o.package}"
    android:versionCode="{o.version_code}"
    android:versionName="{xml_escape(o.version)}">
    <uses-sdk android:minSdkVersion="{MIN_SDK}" android:targetSdkVersion="{PLATFORM_API}" />
    <uses-permission android:name="android.permission.INTERNET" />{BLE_PERMISSIONS if o.ble else ""}
    <application
        android:label="@string/app_name"
        android:icon="{self.icon_ref}"
        android:allowBackup="false"
        android:usesCleartextTraffic="{'true' if o.allow_cleartext else 'false'}"
        android:theme="@android:style/Theme.DeviceDefault.Light.NoActionBar">
        <activity android:name=".MainActivity"
            android:exported="true"
            android:configChanges="orientation|screenSize|screenLayout|smallestScreenSize|keyboardHidden|keyboard|uiMode">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""
        with open(self.p("AndroidManifest.xml"), "w", encoding="utf-8") as f:
            f.write(content)

    def write_java(self):
        start = quote(self.o.entry.replace(os.sep, "/"))
        java = (JAVA_TEMPLATE.replace("__PACKAGE__", self.o.package)
                .replace("__HOST__", ASSET_HOST)
                .replace("__START__", start)
                .replace("__BLE_IMPORTS__", BLE_IMPORTS if self.o.ble else "")
                .replace("__BLE_FIELDS__", BLE_FIELDS if self.o.ble else "")
                .replace("__BLE_INIT__", BLE_INIT if self.o.ble else "")
                .replace("__BLE_CODE__", BLE_CODE if self.o.ble else ""))
        d = self.p("java", *self.o.package.split("."))
        os.makedirs(d)
        self.java_file = os.path.join(d, "MainActivity.java")
        with open(self.java_file, "w", encoding="utf-8") as f:
            f.write(java)

    # compile
    def compile(self):
        tc = self.tc
        log("--> Compiling resources (aapt2)...")
        self.res_zip = self.p("resources.zip")
        run([tc.aapt2, "compile", "--dir", self.p("res"), "-o", self.res_zip], quiet=True)

        log("--> Generating R.java...")
        gen = self.p("gen")
        run([tc.aapt2, "link", "-I", tc.android_jar, "--manifest", self.p("AndroidManifest.xml"),
             "-o", self.p("r-only.apk"), "--java", gen, self.res_zip, "--auto-add-overlay"], quiet=True)
        r_java = os.path.join(gen, *self.o.package.split("."), "R.java")

        log("--> Compiling Java (javac)...")
        classes = self.p("classes")
        os.makedirs(classes)
        run([tc.jbin("javac"), "--release", "11", "-nowarn", "-encoding", "UTF-8",
             "-classpath", tc.android_jar, "-d", classes, self.java_file, r_java], env=tc.env(), quiet=True)

        log("--> Converting to DEX (d8)...")
        class_files = [os.path.join(r, f) for r, _, fs in os.walk(classes) for f in fs if f.endswith(".class")]
        d8_jar = os.path.join(tc.build_tools, "lib", "d8.jar")
        run([tc.jbin("java"), "-cp", d8_jar, "com.android.tools.r8.D8", "--release",
             "--min-api", str(MIN_SDK), "--lib", tc.android_jar, "--output", self.work] + class_files,
            env=tc.env(), quiet=True)

    # signing
    def signing(self):
        """Returns (keystore, alias, env) with passwords passed through env vars, never argv."""
        o = self.o
        if o.keystore:
            ks_pass = os.environ.get("W2D_KS_PASS") or getpass.getpass(f"Keystore password for {o.keystore}: ")
            key_pass = os.environ.get("W2D_KEY_PASS") or ks_pass
            return o.keystore, o.ks_alias, {"W2D_KS_PASS": ks_pass, "W2D_KEY_PASS": key_pass}
        if not os.path.isfile(DEBUG_KEYSTORE):
            log("--> Generating debug keystore...")
            run([self.tc.jbin("keytool"), "-genkeypair", "-keystore", DEBUG_KEYSTORE, "-alias", "androiddebugkey",
                 "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000", "-storepass", "android",
                 "-keypass", "android", "-dname", "CN=Android Debug,O=Android,C=US"], env=self.tc.env(), quiet=True)
        return DEBUG_KEYSTORE, "androiddebugkey", {"W2D_KS_PASS": "android", "W2D_KEY_PASS": "android"}

    def build_apk(self, out):
        tc = self.tc
        log("\n[ Building APK ]")
        unaligned = self.p("unaligned.apk")
        run([tc.aapt2, "link", "-I", tc.android_jar, "--manifest", self.p("AndroidManifest.xml"),
             "-o", unaligned, "-A", self.p("assets"), self.res_zip, "--auto-add-overlay"], quiet=True)
        with zipfile.ZipFile(unaligned, "a", zipfile.ZIP_DEFLATED) as z:
            z.write(self.p("classes.dex"), "classes.dex")
        aligned = self.p("aligned.apk")
        run([tc.zipalign, "-f", "-p", "4", unaligned, aligned], quiet=True)

        ks, alias, penv = self.signing()
        apksigner = os.path.join(tc.build_tools, "lib", "apksigner.jar")
        run([tc.jbin("java"), "-jar", apksigner, "sign", "--ks", ks, "--ks-key-alias", alias,
             "--ks-pass", "env:W2D_KS_PASS", "--key-pass", "env:W2D_KEY_PASS", "--out", out, aligned],
            env=tc.env(penv), quiet=True)
        run([tc.jbin("java"), "-jar", apksigner, "verify", out], env=tc.env(), quiet=True)
        idsig = out + ".idsig"
        if os.path.exists(idsig):
            os.remove(idsig)
        log(f"[SUCCESS] APK: {out}")

    def build_aab(self, out):
        tc = self.tc
        log("\n[ Building AAB ]")
        proto = self.p("proto.apk")
        run([tc.aapt2, "link", "--proto-format", "-I", tc.android_jar, "--manifest", self.p("AndroidManifest.xml"),
             "-o", proto, "-A", self.p("assets"), self.res_zip, "--auto-add-overlay"], quiet=True)
        base = self.p("base")
        with zipfile.ZipFile(proto) as z:
            z.extractall(base)
        os.makedirs(os.path.join(base, "manifest"))
        os.makedirs(os.path.join(base, "dex"))
        shutil.move(os.path.join(base, "AndroidManifest.xml"), os.path.join(base, "manifest", "AndroidManifest.xml"))
        shutil.copy(self.p("classes.dex"), os.path.join(base, "dex", "classes.dex"))
        module = self.p("base.zip")
        with zipfile.ZipFile(module, "w", zipfile.ZIP_DEFLATED) as z:
            for r, _, fs in os.walk(base):
                for f in fs:
                    full = os.path.join(r, f)
                    z.write(full, os.path.relpath(full, base).replace(os.sep, "/"))
        if os.path.exists(out):
            os.remove(out)
        run([tc.jbin("java"), "-jar", BUNDLETOOL_JAR, "build-bundle", f"--modules={module}", f"--output={out}"],
            env=tc.env(), quiet=True)

        ks, alias, penv = self.signing()
        run([tc.jbin("jarsigner"), "-keystore", ks, "-storepass:env", "W2D_KS_PASS", "-keypass:env", "W2D_KEY_PASS",
             "-sigalg", "SHA256withRSA", "-digestalg", "SHA-256", out, alias], env=tc.env(penv), quiet=True)
        log(f"[SUCCESS] AAB: {out}")
        if not self.o.keystore:
            log("[!] Signed with the DEBUG key: Google Play will reject it. Use --keystore (or --new-keystore).")

    def run(self):
        o = self.o
        log(f"\n[*] Building {o.name} {o.version} (versionCode {o.version_code}, {o.package})")
        try:
            self.copy_web()
            self.copy_icon()
            self.write_resources()
            self.write_manifest()
            self.write_java()
            self.compile()
            os.makedirs(o.out_dir, exist_ok=True)
            stem = os.path.join(o.out_dir, f"{safe_filename(o.name)}-{safe_filename(o.version)}")
            if not o.no_apk:
                self.build_apk(stem + ".apk")
            if o.aab:
                self.build_aab(stem + ".aab")
        finally:
            if o.keep_build:
                log(f"[*] Build directory kept: {self.work}")
            else:
                shutil.rmtree(self.work, ignore_errors=True)


# --- CLI -------------------------------------------------------------------
def load_config(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise BuildError(f"Cannot read {path}: {e}")


def ask(prompt, default, interactive):
    if not interactive:
        return default
    val = input(f"{prompt} [{default}]: " if default else f"{prompt}: ").strip()
    return val or default


def create_keystore(tc, path, alias):
    if os.path.exists(path):
        raise BuildError(f"{path} already exists - refusing to overwrite a signing key.")
    log(f"--> Creating release keystore {path} (keytool will ask for passwords and your details).")
    log("    BACK IT UP: losing this file means you can never update the app on Google Play.")
    run([tc.jbin("keytool"), "-genkeypair", "-keystore", path, "-alias", alias, "-keyalg", "RSA",
         "-keysize", "4096", "-validity", "10000"], env=tc.env())


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="web2droid", description="Convert an HTML/CSS/JS project into an Android APK/AAB.")
    ap.add_argument("html_file", help="Entry HTML file (e.g. index.html). Its folder is bundled unless --web-root is set.")
    ap.add_argument("--web-root", help="Folder to bundle (default: folder of html_file)")
    ap.add_argument("--name", help="App name")
    ap.add_argument("--version", dest="app_version", help="versionName, e.g. 1.2.0")
    ap.add_argument("--version-code", type=int, help="versionCode (default: previous build + 1, stored in web2droid.json)")
    ap.add_argument("--package", help="Application id, e.g. com.yourname.myapp")
    ap.add_argument("--author", help="Used to derive the package name if --package is not set")
    ap.add_argument("--icon", help="Launcher icon (.png/.webp/.jpg)")
    ap.add_argument("--aab", action="store_true", default=None, help="Also build an Android App Bundle for Google Play")
    ap.add_argument("--no-apk", action="store_true", help="Skip the APK")
    ap.add_argument("--ble", action="store_true", default=None, help="Add the window.AndroidBLE Bluetooth LE bridge")
    ap.add_argument("--allow-cleartext", action="store_true", default=None, help="Allow plain http:// requests")
    ap.add_argument("--keystore", help="Release keystore (.jks). Passwords: W2D_KS_PASS / W2D_KEY_PASS env or prompt")
    ap.add_argument("--ks-alias", help="Key alias in the keystore (default: upload)")
    ap.add_argument("--new-keystore", metavar="PATH", help="Create a release keystore at PATH and use it")
    ap.add_argument("--exclude", action="append", default=[], metavar="REGEX", help="File names to leave out (regex, repeatable)")
    ap.add_argument("--out-dir", default=".", help="Where to write the APK/AAB (default: current directory)")
    ap.add_argument("--yes", "-y", action="store_true", help="Never prompt; use flags, web2droid.json and defaults")
    ap.add_argument("--accept-licenses", action="store_true", help="Accept the Android SDK licenses non-interactively")
    ap.add_argument("--update", action="store_true", help="Re-download / update the toolchain")
    ap.add_argument("--keep-build", action="store_true", help="Keep the temporary build directory")
    ap.add_argument("-V", "--tool-version", action="version", version=f"web2droid {__version__}")
    return ap.parse_args(argv)


def resolve_options(a):
    html = os.path.abspath(a.html_file)
    if not os.path.isfile(html):
        raise BuildError(f"File not found: {a.html_file}")
    web_root = os.path.abspath(a.web_root or os.path.dirname(html))
    if not html.startswith(web_root + os.sep):
        raise BuildError("html_file must be inside --web-root.")
    cfg_path = os.path.join(web_root, CONFIG_FILE)
    cfg = load_config(cfg_path)
    interactive = sys.stdin.isatty() and not a.yes

    o = argparse.Namespace()
    o.cfg_path, o.cfg = cfg_path, cfg
    o.web_root = web_root
    o.entry = os.path.relpath(html, web_root)
    o.name = (a.name or cfg.get("name") or ask("App name", "My Web App", interactive)).strip()
    if not o.name:
        raise BuildError("App name must not be empty.")
    o.version = (a.app_version or cfg.get("version") or ask("Version", "1.0", interactive)).strip()
    if not VERSION_RE.match(o.version):
        raise BuildError(f"Invalid version '{o.version}'. Use letters, digits, '.', '-', '+'.")

    pkg = a.package or cfg.get("package")
    if not pkg:
        author = a.author or ask("Author / company (for the package name)", "me", interactive)
        pkg = ask("Package name", f"com.{package_segment(author)}.{package_segment(o.name)}", interactive)
    o.package = validate_package(pkg)

    if a.version_code is not None:
        o.version_code = a.version_code
    else:
        o.version_code = int(cfg.get("versionCode", 0)) + 1
    if not 1 <= o.version_code <= 2100000000:
        raise BuildError("versionCode must be between 1 and 2100000000.")

    icon = a.icon or cfg.get("icon")
    if icon is None and not cfg:
        icon = ask("Icon path (Enter for default)", "", interactive).strip('"') or None
    if icon:
        # CLI paths are relative to the current dir; config paths to the web root.
        icon = os.path.abspath(icon if a.icon else os.path.join(web_root, icon))
        if not os.path.isfile(icon):
            raise BuildError(f"Icon not found: {icon}")
    o.icon = icon

    o.aab = a.aab if a.aab is not None else cfg.get("aab")
    if o.aab is None:
        o.aab = ask("Also build AAB for Google Play? (y/N)", "n", interactive).lower().startswith("y")
    o.no_apk = a.no_apk
    o.ble = bool(a.ble if a.ble is not None else cfg.get("ble", False))
    o.allow_cleartext = bool(a.allow_cleartext if a.allow_cleartext is not None else cfg.get("allowCleartext", False))
    o.keystore = os.path.abspath(a.new_keystore or a.keystore) if (a.new_keystore or a.keystore) else (
        os.path.abspath(os.path.join(web_root, cfg["keystore"])) if cfg.get("keystore") else None)
    o.ks_alias = a.ks_alias or cfg.get("keystoreAlias") or "upload"
    o.new_keystore = bool(a.new_keystore)
    o.exclude = a.exclude + cfg.get("exclude", [])
    o.out_dir = os.path.abspath(a.out_dir)
    o.keep_build = a.keep_build
    if o.no_apk and not o.aab:
        raise BuildError("Nothing to build: --no-apk without --aab.")
    return o


def rel_or_abs(path, base):
    """Path relative to base when possible (portable config), absolute otherwise."""
    try:
        return os.path.relpath(path, base).replace(os.sep, "/")
    except ValueError:  # different drive on Windows
        return path


def save_config(o):
    cfg = dict(o.cfg)
    cfg.update({"name": o.name, "version": o.version, "versionCode": o.version_code, "package": o.package,
                "aab": o.aab, "ble": o.ble, "allowCleartext": o.allow_cleartext, "keystoreAlias": o.ks_alias})
    if o.icon:
        cfg["icon"] = rel_or_abs(o.icon, o.web_root)
    if o.keystore:
        cfg["keystore"] = rel_or_abs(o.keystore, o.web_root)
    with open(o.cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:  # never crash on non-ASCII app names in legacy Windows consoles
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    a = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        o = resolve_options(a)
        tc = Toolchain(update=a.update, accept_licenses=a.accept_licenses)
        tc.ensure()
        if o.new_keystore:
            create_keystore(tc, o.keystore, o.ks_alias)
        elif o.keystore and not os.path.isfile(o.keystore):
            raise BuildError(f"Keystore not found: {o.keystore}")
        AppBuilder(tc, o).run()
        save_config(o)
        log(f"[*] Settings saved to {o.cfg_path} (next build uses versionCode {o.version_code + 1}).")
        return 0
    except BuildError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[!] Cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
