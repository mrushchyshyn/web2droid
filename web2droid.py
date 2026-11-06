#!/usr/bin/env python3
import os
import sys
import shutil
import subprocess
import urllib.request
import zipfile
import stat
import time
import argparse
import glob
import re

# --- CONFIGURATION ---
USER_HOME = os.path.expanduser("~")
SDK_ROOT = os.path.join(USER_HOME, ".android_web_builder_sdk")
BUILD_TOOLS_VERSION = "33.0.0"
PLATFORM_VERSION = "android-33"
SDK_MIN_VERSION = "21"
JAVA_VERSION = "17"

# Paths to tools (populated dynamically)
TOOLS = {
    "android_cli": os.path.join(SDK_ROOT, "android"),
    "aapt2": "",
    "d8": "",
    "apksigner": "",
    "android_jar": "",
    "zipalign": "",
    "bundletool": ""
}

def nz_name(name):
    return re.sub(r"[^\w.]", "", name).replace('.', '_')

class SDKManager:
    """Manages the installation of the Android SDK, Java dependencies, and Bundletool."""

    def check_and_install(self, force=False):
        print(f"[*] Checking environment in: {SDK_ROOT}")

        # 1. Check for Java (JDK)
        self.ensure_java(force)
        global JAVA_VERSION
        JAVA_VERSION = subprocess.run([
            "javac", "--version"
        ], check=True, capture_output=True).stdout.decode('utf-8').split()[-1].split('.')[0]

        # 2. Check for Android SDK
        global PLATFORM_VERSION
        PLATFORM_VERSION = max(
            subprocess.run([
                TOOLS["android_cli"], "--sdk", os.path.join(SDK_ROOT, "sdk"),
                "sdk", "list", "--all",
                "platforms/*"
            ], check=True, capture_output=True).stdout.decode('utf-8').splitlines(),
            key=lambda value: (float(v) if not value=='' and re.match(r'^\d+(\.\d+)?$', v:=value.split()[-1]) else 0)
        ).split()[0].split('/')[-1]
        if os.path.exists(os.path.join(SDK_ROOT, "sdk", "platforms", PLATFORM_VERSION)):
            print("[OK] Android SDK found.")
        elif force:
            print("[*] Android SDK found. Updating...")
            self.install_sdk()
        else:
            print("[!] SDK not found or incomplete. Starting first-time setup...")
            self.install_sdk()

        # 3. Check for Bundletool (for AAB)
        self.ensure_bundletool(force)

        self.resolve_tools()

    def ensure_java(self, force=False):
        if shutil.which("javac") and shutil.which("jarsigner"):
            if force:
                print("--> Running: sudo apt update")
                subprocess.run(['sudo', 'apt', 'update'], check=True)
                print("--> Running: sudo apt update -y default-jdk")
                subprocess.run(['sudo', 'apt', 'upgrade', '-y', 'default-jdk'], check=True)
            return

        print("[!] Java JDK (javac/jarsigner) is missing.")
        print("[*] Attempting to install 'default-jdk'...")
        try:
            print("--> Running: sudo apt update")
            subprocess.check_call(['sudo', 'apt', 'update'])
            print("--> Running: sudo apt install -y default-jdk")
            subprocess.check_call(['sudo', 'apt', 'install', '-y', 'default-jdk'])

            if not shutil.which("javac"):
                raise Exception("Installation finished, but 'javac' is still not found.")
            print("[OK] Java installed successfully.")
        except Exception as e:
            print(f"\n[ERROR] Failed to install Java: {e}")
            sys.exit(1)

    def install_sdk(self, force=False):
        global BUILD_TOOLS_VERSION, PLATFORM_VERSION
        try:
            if os.path.exists(SDK_ROOT): shutil.rmtree(SDK_ROOT)
            os.makedirs(SDK_ROOT)

            # Download Android CLI
            print(f"--> Downloading Android CLI...")
            with urllib.request.urlopen('https://dl.google.com/android/cli/latest/linux_x86_64/android') as f:
                with open(TOOLS["android_cli"], 'wb') as bin:
                    bin.write(f.read())
            st = os.stat(TOOLS["android_cli"])
            os.chmod(TOOLS["android_cli"], st.st_mode | stat.S_IEXEC)

            # Install the Android CLI SDK
            CLI_SDK = os.path.join(SDK_ROOT, "sdk")
            print(f"--> Initialization of the Android CLI and SDK...")
            subprocess.run([
                TOOLS["android_cli"], "--sdk", CLI_SDK,
                "init"
            ], check=True)

            # Install SDK packages
            print("--> Installing Build Tools & Platform...")
            BUILD_TOOLS_VERSION = max(
                subprocess.run([
                    TOOLS["android_cli"], "--sdk", CLI_SDK,
                    "sdk", "list", "--all",
                    "build-tools/*"
                ], check=True, capture_output=True).stdout.decode('utf-8').splitlines(),
                key=lambda value: (float(v) if not value=='' and re.match(r'^\d+(\.\d+)?$', v:=value.split()[-1]) else 0)
            ).split()[0].split('/')[-1]
            subprocess.run([
                TOOLS["android_cli"], "--sdk", CLI_SDK,
                "sdk", "install",
                "cmdline-tools/latest",
                f"build-tools/{BUILD_TOOLS_VERSION}",
                f"platforms/{PLATFORM_VERSION}",
                "platform-tools"
            ], check=True)
            subprocess.run([
                TOOLS["android_cli"], "--sdk", CLI_SDK,
                "sdk", "update"
            ], check=True)
            print("[OK] SDK Installation complete.")

        except Exception as e:
            print(f"\n[ERROR] Failed to set up Android CLI SDK: {e}")
            sys.exit(1)

    def ensure_bundletool(self, force=False):
        dest = os.path.join(SDK_ROOT, "bundletool.jar")
        if force or not os.path.exists(dest):
            print(f"--> Fetcking latest Bundletool version...")
            # Check for the latest bundletool
            with urllib.request.urlopen('https://github.com/google/bundletool/releases') as f:
                html = f.read().decode('utf-8')
                if tool := re.search(r'/google/bundletool/releases/tag/(\d+\.\d+\.\d+).{0,200}href="/google/bundletool/releases/latest"', html, re.DOTALL):
                    tool_version = tool.group(1)
                    BUNDLETOOL_URL = f"https://github.com/google/bundletool/releases/download/{tool_version}/bundletool-all-{tool_version}.jar"
                del html

            print(f"--> Downloading Bundletool {tool_version} (for AAB generation)...")
            try:
                urllib.request.urlretrieve(BUNDLETOOL_URL, dest)
                print("[OK] Bundletool downloaded.")
            except Exception as e:
                print(f"[ERROR] Failed to download bundletool: {e}")

    def resolve_tools(self):
        global BUILD_TOOLS_VERSION, PLATFORM_VERSION
        CLI_SDK = os.path.join(SDK_ROOT, "sdk")
        BUILD_TOOLS_VERSION = re.search(
            r'\d+(?:\.\d)*',
            subprocess.run([
                TOOLS["android_cli"], "--sdk", CLI_SDK,
                "sdk", "list",
                "build-tools/*"
            ], check=True, capture_output=True).stdout.decode('utf-8')
        ).group(0)
        PLATFORM_VERSION = re.search(
            r'android-\d+(\.\d+)?',
            subprocess.run([
                TOOLS["android_cli"], "--sdk", CLI_SDK,
                "sdk", "list",
                "platforms/*"
            ], check=True, capture_output=True).stdout.decode('utf-8')
        ).group(0)
        bt_path = os.path.join(CLI_SDK, "build-tools", BUILD_TOOLS_VERSION)
        TOOLS["aapt2"] = os.path.join(bt_path, "aapt2")
        TOOLS["d8"] = os.path.join(bt_path, "d8")
        TOOLS["apksigner"] = os.path.join(bt_path, "apksigner")
        TOOLS["zipalign"] = os.path.join(bt_path, "zipalign")
        TOOLS["android_jar"] = os.path.join(CLI_SDK, "platforms", PLATFORM_VERSION, "android.jar")
        TOOLS["bundletool"] = os.path.join(SDK_ROOT, "bundletool.jar")

class AppBuilder:
    def __init__(self, html_path, app_name, version, icon_path, author=None, additional_assets=[], versionCode=None):
        self.html_path = os.path.abspath(html_path)
        self.app_name = app_name
        self.version = version
        self.author = author or 'example'
        self.icon_path = os.path.abspath(icon_path) if icon_path else None
        self.build_dir = os.path.abspath("build_temp_" + str(int(time.time())))
        self.additional_assets = additional_assets

        # Derived paths
        self.package_name = f"com.{nz_name(self.author)}.{nz_name(self.app_name)}"
        self.keystore_path = os.path.join(SDK_ROOT, "debug.keystore")

        # versionCode
        if not versionCode is None:
            self.versionCode = versionCode
        else:
            latest_apk = max(glob.glob(os.path.abspath(f"{self.app_name.replace(' ', '_')}*.apk")), key=os.path.getctime)
            if latest_apk is None:
                self.versionCode = 1
            else:
                xml = subprocess.run([
                    TOOLS["aapt2"], "dump",
                    "xmltree", latest_apk,
                    "--file", "AndroidManifest.xml"
                ], check=True, capture_output=True)
                if xml is None: return
                xml = xml.stdout.decode('utf-8')
                if not xml.startswith('N: android=http://schemas.android.com/apk'):
                    self.versionCode = 1
                else:
                    vc = re.search(r'android:versionCode[^=]*?=([0-9]+)', xml)
                    self.versionCode = 1 if vc is None else int(vc.group(1), 10)+1

    def prepare_directories(self):
        if os.path.exists(self.build_dir): shutil.rmtree(self.build_dir)
        os.makedirs(self.build_dir)

        self.src_dir = os.path.join(self.build_dir, "java", "com", nz_name(self.author), nz_name(self.app_name))
        os.makedirs(self.src_dir)

        self.res_dir = os.path.join(self.build_dir, "res")
        os.makedirs(os.path.join(self.res_dir, "layout"))
        os.makedirs(os.path.join(self.res_dir, "values"))

        self.assets_dir = os.path.join(self.build_dir, "assets")
        os.makedirs(self.assets_dir)

        for a in self.additional_assets:
            os.makedirs(os.path.join(self.assets_dir, a))

    def copy_assets(self):
        # HTML
        if not os.path.exists(self.html_path):
            raise Exception(f"File {self.html_path} not found!")
        shutil.copy(self.html_path, os.path.join(self.assets_dir, "index.html"))

        # Icon
        self.icon_res_name = "@android:drawable/sym_def_app_icon"
        if self.icon_path and os.path.exists(self.icon_path):
            mipmap_dir = os.path.join(self.res_dir, "mipmap-hdpi")
            os.makedirs(mipmap_dir, exist_ok=True)
            ext = os.path.splitext(self.icon_path)[1].lower()
            dest_icon = os.path.join(mipmap_dir, "ic_launcher" + ext)
            shutil.copy(self.icon_path, dest_icon)
            self.icon_res_name = "@mipmap/ic_launcher"

        # Assets
        root = os.path.split(self.html_path)[0]
        for a in self.additional_assets:
            rss_dir = os.path.join(root, a)
            if os.path.exists(rss_dir):
                asset_dir = os.path.join(self.assets_dir, a)
                shutil.copytree(rss_dir, asset_dir, ignore_dangling_symlinks=True, dirs_exist_ok=True)

    def generate_manifest(self):
        content = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{self.package_name}"
    android:versionCode="{self.versionCode}"
    android:versionName="{self.version}">
    <uses-sdk android:minSdkVersion="{SDK_MIN_VERSION}" android:targetSdkVersion="{BUILD_TOOLS_VERSION.split('.')[0]}" />
    <uses-permission android:name="android.permission.INTERNET" />
    <application
        android:label={self.app_name!r}
        android:icon="{self.icon_res_name}"
        android:theme="@android:style/Theme.NoTitleBar"
        android:usesCleartextTraffic="true">
        <activity android:name=".MainActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>"""
        with open(os.path.join(self.build_dir, "AndroidManifest.xml"), "w") as f:
            f.write(content)

    def generate_java(self):
        content = f"""package {self.package_name};
import android.app.Activity;
import android.os.Bundle;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

public class MainActivity extends Activity {{
    @Override
    protected void onCreate(Bundle savedInstanceState) {{
        super.onCreate(savedInstanceState);
        WebView webView = new WebView(this);
        WebSettings webSettings = webView.getSettings();
        webSettings.setJavaScriptEnabled(true);
        webSettings.setDomStorageEnabled(true);
        webView.setWebViewClient(new WebViewClient());
        webView.loadUrl("file:///android_asset/index.html");
        setContentView(webView);
    }}
}}"""
        with open(os.path.join(self.src_dir, "MainActivity.java"), "w") as f:
            f.write(content)

    def compile_common(self):
        """Compiles resources (base), Java, and DEX. Returns path to compiled resources zip."""
        print("--> Compiling resources (aapt2)...")
        compiled_res_zip = os.path.join(self.build_dir, "resources.zip")
        subprocess.run([
            TOOLS["aapt2"], "compile",
            "--dir", self.res_dir,
            "-o", compiled_res_zip
        ], check=True, stdout=subprocess.DEVNULL)

        # We need an R.java to compile the Java code.
        # We generate a temporary APK just to get R.java (Binary linking)
        print("--> Generating R.java...")
        subprocess.run([
            TOOLS["aapt2"], "link",
            "-I", TOOLS["android_jar"],
            "--manifest", os.path.join(self.build_dir, "AndroidManifest.xml"),
            "-o", os.path.join(self.build_dir, "temp_link.apk"), # Discarded later
            "--java", os.path.join(self.build_dir, "gen"),
            compiled_res_zip,
            "--auto-add-overlay"
        ], check=True, stdout=subprocess.DEVNULL)

        print("--> Compiling Java (javac)...")
        r_java = os.path.join(self.build_dir, "gen", *self.package_name.split("."), "R.java")
        subprocess.run([
            "javac", "--release", JAVA_VERSION,
            "-sourcepath", TOOLS["android_jar"],
            "-classpath", TOOLS["android_jar"],
            os.path.join(self.src_dir, "MainActivity.java"),
            r_java
        ], check=True)

        print("--> Converting to Dex (d8)...")
        class_files = [
            os.path.join(self.src_dir, "MainActivity.class"),
            re.sub(r"\.java$", ".class", r_java)
        ]
        mipmap_class = re.sub(r"\.java$", "$mipmap.class", r_java)
        if os.path.exists(mipmap_class): class_files.append(mipmap_class)
        layout_class = re.sub(r"\.java$", "$layout.class", r_java)
        if os.path.exists(layout_class): class_files.append(layout_class)

        subprocess.run([
            TOOLS["d8"], "--output", self.build_dir,
            "--lib", TOOLS["android_jar"],
            *class_files
        ], check=True)

        return compiled_res_zip

    def ensure_keystore(self):
        if not os.path.exists(self.keystore_path):
            print("--> Generating debug keystore...")
            subprocess.run([
                "keytool", "-genkey", "-v", "-keystore", self.keystore_path,
                "-alias", "androiddebugkey", "-keyalg", "RSA", "-keysize", "2048",
                "-validity", "10000", "-storepass", "android", "-keypass", "android",
                "-dname", "CN=Android Debug,O=Android,C=US"
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def build_apk(self, compiled_res_zip):
        print("\n[ Building APK ]")
        unaligned_apk = os.path.join(self.build_dir, "unaligned.apk")

        # Link (Binary)
        subprocess.run([
            TOOLS["aapt2"], "link",
            "-I", TOOLS["android_jar"],
            "--manifest", os.path.join(self.build_dir, "AndroidManifest.xml"),
            "-o", unaligned_apk,
            "-A", self.assets_dir,
            compiled_res_zip,
            "--auto-add-overlay"
        ], check=True, stdout=subprocess.DEVNULL)

        # Add Dex
        with zipfile.ZipFile(unaligned_apk, 'a') as zipf:
            zipf.write(os.path.join(self.build_dir, "classes.dex"), "classes.dex")

        # Sign
        final_apk = os.path.abspath(f"{self.app_name.replace(' ', '_')}.apk")
        subprocess.run([
            TOOLS["apksigner"], "-J-enable-native-access=ALL-UNNAMED", "sign",
            "--ks", self.keystore_path,
            "--ks-pass", "pass:android",
            "--out", final_apk,
            unaligned_apk
        ], check=True, stdout=subprocess.DEVNULL)

        print(f"[SUCCESS] APK Created: {final_apk}")

    def build_aab(self, compiled_res_zip):
        print("\n[ Building Android App Bundle (AAB) ]")

        # 1. Link in Proto Format
        proto_apk = os.path.join(self.build_dir, "proto.apk")
        subprocess.run([
            TOOLS["aapt2"], "link",
            "--proto-format",
            "-I", TOOLS["android_jar"],
            "--manifest", os.path.join(self.build_dir, "AndroidManifest.xml"),
            "-o", proto_apk,
            "-A", self.assets_dir,
            compiled_res_zip,
            "--auto-add-overlay"
        ], check=True, stdout=subprocess.DEVNULL)

        # 2. Extract and organize for Bundletool
        # Structure must be: base/manifest, base/dex, base/res, base/assets, base/root
        base_dir = os.path.join(self.build_dir, "base_module")
        os.makedirs(os.path.join(base_dir, "manifest"))
        os.makedirs(os.path.join(base_dir, "dex"))

        # Extract proto apk content
        with zipfile.ZipFile(proto_apk, 'r') as z:
            z.extractall(base_dir)

        # Move AndroidManifest.xml to manifest/
        shutil.move(os.path.join(base_dir, "AndroidManifest.xml"),
                    os.path.join(base_dir, "manifest", "AndroidManifest.xml"))

        # Copy classes.dex to dex/
        shutil.copy(os.path.join(self.build_dir, "classes.dex"),
                    os.path.join(base_dir, "dex", "classes.dex"))

        # Zip the module
        module_zip = os.path.join(self.build_dir, "base.zip")
        self._zip_dir(base_dir, module_zip)

        # 3. Build Bundle
        final_aab = os.path.abspath(f"{self.app_name.replace(' ', '_')}.aab")
        if os.path.exists(final_aab): os.remove(final_aab)

        subprocess.run([
            "java", "-jar", TOOLS["bundletool"],
            "build-bundle",
            f"--modules={module_zip}",
            f"--output={final_aab}"
        ], check=True)

        # 4. Sign Bundle (using jarsigner, not apksigner)
        subprocess.run([
            "jarsigner",
            "-keystore", self.keystore_path,
            "-storepass", "android",
            "-keypass", "android",
            final_aab,
            "androiddebugkey"
        ], check=True, stdout=subprocess.DEVNULL)

        print(f"[SUCCESS] AAB Created: {final_aab}")

    def _zip_dir(self, dir_path, zip_path):
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(dir_path):
                for file in files:
                    abs_path = os.path.join(root, file)
                    rel_path = os.path.relpath(abs_path, dir_path)
                    zipf.write(abs_path, rel_path)

    def run(self, build_apk=True, build_aab=False):
        try:
            print(f"\n[*] Starting build: {self.app_name} v{self.version}")
            self.prepare_directories()
            self.copy_assets()
            self.generate_manifest()
            self.generate_java()
            self.ensure_keystore()

            # Common Steps
            compiled_res = self.compile_common()

            # Forked Steps
            if build_apk: self.build_apk(compiled_res)
            if build_aab: self.build_aab(compiled_res)

        except subprocess.CalledProcessError as e:
            print(f"\n[ERROR] Command failed: {e.cmd}")
        except Exception as e:
            print(f"\n[ERROR] {e}")
        finally:
            # Cleanup
            shutil.rmtree(self.build_dir)

def main():
    parser = argparse.ArgumentParser(description="Convert HTML to Android APK/AAB")
    parser.add_argument("html_file", help="Path to index.html")
    parser.add_argument("asset_dirs", help="Specify additional directories for assets to add", nargs='*', action='extend')
    parser.add_argument("--vc", help="android:versionCode value (defaults is the most recent apk versionCode found incremented by 1 or 1 ; must be in range 1-65535)", type=int)
    parser.add_argument("--force-update", help="Force the tools be update before proceeding", action='store_true')
    args = parser.parse_args()

    # Inputs
    import selectors
    selector = selectors.DefaultSelector()
    selector.register(sys.stdin, selectors.EVENT_READ)
    def read(prompt):
        if len(selector.select(-1)) > 0:
            return sys.stdin.readline()
        else:
            return input(prompt)
    app_name = read("APP NAME (e.g., My App): ").strip() or "MyWebApp"
    version = read("VERSION (e.g., 1.0): ").strip() or "1.0"
    icon_path = read("ICON PATH (Enter for default): ").strip()
    author = read("AUTHOR / COMPANY / BRAND NAME (default is example): ").strip() or "example"

    do_apk = True
    aab_input = read("Generate AAB (App Bundle) for Play Store? [y/N]: ").strip().lower()
    do_aab = aab_input == 'y'

    # Setup
    sdk_mgr = SDKManager()
    sdk_mgr.check_and_install(args.force_update)

    # Build
    builder = AppBuilder(args.html_file, app_name, version, icon_path,
        author=author, versionCode=args.vc, additional_assets=args.asset_dirs or [])
    builder.run(build_apk=do_apk, build_aab=do_aab)

if __name__ == "__main__":
    main()
