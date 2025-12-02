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

# --- CONFIGURATION ---
USER_HOME = os.path.expanduser("~")
SDK_ROOT = os.path.join(USER_HOME, ".android_web_builder_sdk")
CMD_TOOLS_URL = "https://dl.google.com/android/repository/commandlinetools-linux-9477386_latest.zip"
BUNDLETOOL_URL = "https://github.com/google/bundletool/releases/download/1.15.6/bundletool-all-1.15.6.jar"
BUILD_TOOLS_VERSION = "33.0.0"
PLATFORM_VERSION = "android-33"

# Paths to tools (populated dynamically)
TOOLS = {
    "sdkmanager": "",
    "aapt2": "",
    "d8": "",
    "apksigner": "",
    "android_jar": "",
    "zipalign": "",
    "bundletool": ""
}

class SDKManager:
    """Manages the installation of the Android SDK, Java dependencies, and Bundletool."""
    
    def check_and_install(self):
        print(f"[*] Checking environment in: {SDK_ROOT}")
        
        # 1. Check for Java (JDK)
        self.ensure_java()

        # 2. Check for Android SDK
        if os.path.exists(os.path.join(SDK_ROOT, "platforms", PLATFORM_VERSION)):
            print("[OK] Android SDK found.")
        else:
            print("[!] SDK not found or incomplete. Starting first-time setup...")
            self.install_sdk()

        # 3. Check for Bundletool (for AAB)
        self.ensure_bundletool()
        
        self.resolve_tools()

    def ensure_java(self):
        if shutil.which("javac") and shutil.which("jarsigner"):
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

    def install_sdk(self):
        try:
            if os.path.exists(SDK_ROOT): shutil.rmtree(SDK_ROOT)
            os.makedirs(SDK_ROOT)

            # Download Command Line Tools
            cmd_tools_zip = os.path.join(SDK_ROOT, "cmdline-tools.zip")
            print(f"--> Downloading Command Line Tools...")
            urllib.request.urlretrieve(CMD_TOOLS_URL, cmd_tools_zip)

            with zipfile.ZipFile(cmd_tools_zip, 'r') as zip_ref:
                zip_ref.extractall(SDK_ROOT)
            os.remove(cmd_tools_zip)

            # Fix directory structure
            base_cmd = os.path.join(SDK_ROOT, "cmdline-tools")
            latest_temp = os.path.join(SDK_ROOT, "latest")
            os.rename(base_cmd, latest_temp)
            os.makedirs(base_cmd)
            shutil.move(latest_temp, os.path.join(base_cmd, "latest"))

            sdkmanager_bin = os.path.join(SDK_ROOT, "cmdline-tools", "latest", "bin", "sdkmanager")
            st = os.stat(sdkmanager_bin)
            os.chmod(sdkmanager_bin, st.st_mode | stat.S_IEXEC)

            # Install Packages
            print("--> Installing Build Tools & Platform...")
            yes_proc = subprocess.Popen(["yes"], stdout=subprocess.PIPE)
            install_cmd = [
                sdkmanager_bin, "--sdk_root=" + SDK_ROOT,
                f"build-tools;{BUILD_TOOLS_VERSION}",
                f"platforms;{PLATFORM_VERSION}",
                "platform-tools"
            ]
            subprocess.check_call(install_cmd, stdin=yes_proc.stdout, stdout=subprocess.DEVNULL)
            yes_proc.stdout.close()
            print("[OK] SDK Installation complete.")

        except Exception as e:
            print(f"\n[ERROR] Failed to set up SDK: {e}")
            sys.exit(1)

    def ensure_bundletool(self):
        dest = os.path.join(SDK_ROOT, "bundletool.jar")
        if not os.path.exists(dest):
            print("--> Downloading Bundletool (for AAB generation)...")
            try:
                urllib.request.urlretrieve(BUNDLETOOL_URL, dest)
                print("[OK] Bundletool downloaded.")
            except Exception as e:
                print(f"[ERROR] Failed to download bundletool: {e}")

    def resolve_tools(self):
        bt_path = os.path.join(SDK_ROOT, "build-tools", BUILD_TOOLS_VERSION)
        TOOLS["aapt2"] = os.path.join(bt_path, "aapt2")
        TOOLS["d8"] = os.path.join(bt_path, "d8")
        TOOLS["apksigner"] = os.path.join(bt_path, "apksigner")
        TOOLS["zipalign"] = os.path.join(bt_path, "zipalign")
        TOOLS["android_jar"] = os.path.join(SDK_ROOT, "platforms", PLATFORM_VERSION, "android.jar")
        TOOLS["bundletool"] = os.path.join(SDK_ROOT, "bundletool.jar")

class AppBuilder:
    def __init__(self, html_path, app_name, version, icon_path):
        self.html_path = os.path.abspath(html_path)
        self.app_name = app_name
        self.version = version
        self.icon_path = os.path.abspath(icon_path) if icon_path else None
        self.build_dir = os.path.abspath("build_temp_" + str(int(time.time())))
        
        # Derived paths
        self.package_name = f"com.example.{self.app_name.lower().replace(' ', '')}"
        self.keystore_path = os.path.join(SDK_ROOT, "debug.keystore")
    
    def prepare_directories(self):
        if os.path.exists(self.build_dir): shutil.rmtree(self.build_dir)
        os.makedirs(self.build_dir)
        
        self.src_dir = os.path.join(self.build_dir, "java", "com", "example", self.app_name.lower().replace(' ', ''))
        os.makedirs(self.src_dir)
        
        self.res_dir = os.path.join(self.build_dir, "res")
        os.makedirs(os.path.join(self.res_dir, "layout"))
        os.makedirs(os.path.join(self.res_dir, "values"))
        
        self.assets_dir = os.path.join(self.build_dir, "assets")
        os.makedirs(self.assets_dir)

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

    def generate_manifest(self):
        content = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{self.package_name}"
    android:versionCode="1"
    android:versionName="{self.version}">
    <uses-sdk android:minSdkVersion="21" android:targetSdkVersion="33" />
    <uses-permission android:name="android.permission.INTERNET" />
    <application 
        android:label="{self.app_name}" 
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
            "javac", "-source", "1.8", "-target", "1.8",
            "-bootclasspath", TOOLS["android_jar"],
            "-classpath", TOOLS["android_jar"],
            os.path.join(self.src_dir, "MainActivity.java"),
            r_java
        ], check=True)

        print("--> Converting to Dex (d8)...")
        class_files = [
            os.path.join(self.src_dir, "MainActivity.class"),
            r_java.replace(".java", ".class"),
        ]
        layout_class = r_java.replace(".java", "$layout.class")
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
            TOOLS["apksigner"], "sign",
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
            
            # Cleanup
            shutil.rmtree(self.build_dir)
            
        except subprocess.CalledProcessError as e:
            print(f"\n[ERROR] Command failed: {e.cmd}")
        except Exception as e:
            print(f"\n[ERROR] {e}")

def main():
    parser = argparse.ArgumentParser(description="Convert HTML to Android APK/AAB")
    parser.add_argument("html_file", help="Path to index.html")
    args = parser.parse_args()

    # Inputs
    app_name = input("APP NAME (e.g., My App): ").strip() or "MyWebApp"
    version = input("VERSION (e.g., 1.0): ").strip() or "1.0"
    icon_path = input("ICON PATH (Enter for default): ").strip()
    
    do_apk = True
    aab_input = input("Generate AAB (App Bundle) for Play Store? [y/N]: ").strip().lower()
    do_aab = aab_input == 'y'

    # Setup
    sdk_mgr = SDKManager()
    sdk_mgr.check_and_install()

    # Build
    builder = AppBuilder(args.html_file, app_name, version, icon_path)
    builder.run(build_apk=do_apk, build_aab=do_aab)

if __name__ == "__main__":
    main()
