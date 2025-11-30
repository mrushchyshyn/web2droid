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
# The SDK is stored in the user's home directory to be shared across runs
USER_HOME = os.path.expanduser("~")
SDK_ROOT = os.path.join(USER_HOME, ".android_web_builder_sdk")
CMD_TOOLS_URL = "https://dl.google.com/android/repository/commandlinetools-linux-9477386_latest.zip"
BUILD_TOOLS_VERSION = "33.0.0"
PLATFORM_VERSION = "android-33"

# Paths to tools (populated dynamically)
TOOLS = {
    "sdkmanager": "",
    "aapt2": "",
    "d8": "",
    "apksigner": "",
    "android_jar": "",
    "zipalign": ""
}

class SDKManager:
    """Manages the installation of the Android SDK and Java dependencies."""
    
    def check_and_install(self):
        print(f"[*] Checking environment in: {SDK_ROOT}")
        
        # 1. Check for Java (JDK)
        self.ensure_java()

        # 2. Check for Android SDK
        # We look for platform-tools as a marker of a complete installation
        if os.path.exists(os.path.join(SDK_ROOT, "platforms", PLATFORM_VERSION)):
            print("[OK] Android SDK found.")
            self.resolve_tools()
            return

        print("[!] SDK not found or incomplete. Starting first-time setup...")
        self.install_sdk()
        self.resolve_tools()

    def ensure_java(self):
        """Checks for 'javac'. If missing, attempts to install default-jdk via apt."""
        if shutil.which("javac"):
            return # Java is present

        print("[!] Java JDK (javac) is missing.")
        print("[*] Attempting to install 'default-jdk' (Root password may be required)...")
        
        try:
            # Update apt cache
            print("--> Running: sudo apt update")
            subprocess.check_call(['sudo', 'apt', 'update'])
            
            # Install JDK
            print("--> Running: sudo apt install -y default-jdk")
            subprocess.check_call(['sudo', 'apt', 'install', '-y', 'default-jdk'])
            
            # Verify installation
            if not shutil.which("javac"):
                raise Exception("Installation finished, but 'javac' is still not found.")
                
            print("[OK] Java installed successfully.")
            
        except subprocess.CalledProcessError:
            print("\n[ERROR] Failed to install Java. Please run: sudo apt install default-jdk")
            sys.exit(1)
        except Exception as e:
            print(f"\n[ERROR] Unexpected error installing Java: {e}")
            sys.exit(1)

    def install_sdk(self):
        try:
            if os.path.exists(SDK_ROOT):
                shutil.rmtree(SDK_ROOT)
            os.makedirs(SDK_ROOT)

            # 1. Download Command Line Tools
            cmd_tools_zip = os.path.join(SDK_ROOT, "cmdline-tools.zip")
            print(f"--> Downloading Command Line Tools ({CMD_TOOLS_URL})...")
            urllib.request.urlretrieve(CMD_TOOLS_URL, cmd_tools_zip)

            print("--> Extracting SDK...")
            with zipfile.ZipFile(cmd_tools_zip, 'r') as zip_ref:
                zip_ref.extractall(SDK_ROOT)
            os.remove(cmd_tools_zip)

            # Fix directory structure for sdkmanager (it expects cmdline-tools/latest/bin)
            base_cmd = os.path.join(SDK_ROOT, "cmdline-tools")
            latest_temp = os.path.join(SDK_ROOT, "latest")
            os.rename(base_cmd, latest_temp)
            os.makedirs(base_cmd)
            shutil.move(latest_temp, os.path.join(base_cmd, "latest"))

            sdkmanager_bin = os.path.join(SDK_ROOT, "cmdline-tools", "latest", "bin", "sdkmanager")
            
            # Grant executable permissions
            st = os.stat(sdkmanager_bin)
            os.chmod(sdkmanager_bin, st.st_mode | stat.S_IEXEC)

            # 2. Install Packages
            print("--> Installing Build Tools & Platform (This may take a while)...")
            
            # Auto-accept licenses using 'yes'
            yes_proc = subprocess.Popen(["yes"], stdout=subprocess.PIPE)
            install_cmd = [
                sdkmanager_bin,
                "--sdk_root=" + SDK_ROOT,
                f"build-tools;{BUILD_TOOLS_VERSION}",
                f"platforms;{PLATFORM_VERSION}",
                "platform-tools"
            ]

            proc = subprocess.Popen(install_cmd, stdin=yes_proc.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            yes_proc.stdout.close()
            
            # Print progress dots
            while True:
                if proc.poll() is not None:
                    break
                print(".", end="", flush=True)
                time.sleep(1)
            print("\n[OK] SDK Installation complete.")

        except Exception as e:
            print(f"\n[ERROR] Failed to set up SDK: {e}")
            sys.exit(1)

    def resolve_tools(self):
        """Locates the binaries within the downloaded SDK."""
        bt_path = os.path.join(SDK_ROOT, "build-tools", BUILD_TOOLS_VERSION)
        TOOLS["aapt2"] = os.path.join(bt_path, "aapt2")
        TOOLS["d8"] = os.path.join(bt_path, "d8")
        TOOLS["apksigner"] = os.path.join(bt_path, "apksigner")
        TOOLS["zipalign"] = os.path.join(bt_path, "zipalign")
        TOOLS["android_jar"] = os.path.join(SDK_ROOT, "platforms", PLATFORM_VERSION, "android.jar")

class APKBuilder:
    def __init__(self, html_path, app_name, version, icon_path):
        self.html_path = os.path.abspath(html_path)
        self.app_name = app_name
        self.version = version
        self.icon_path = os.path.abspath(icon_path) if icon_path else None
        
        # Temporary build directory
        self.build_dir = os.path.abspath("build_temp_" + str(int(time.time())))
    
    def build(self):
        print(f"\n[*] Starting build: {self.app_name} v{self.version}")
        
        try:
            if os.path.exists(self.build_dir): shutil.rmtree(self.build_dir)
            os.makedirs(self.build_dir)

            package_name = f"com.example.{self.app_name.lower().replace(' ', '')}"
            
            # --- 1. Create Directory Structure ---
            src_dir = os.path.join(self.build_dir, "java", "com", "example", self.app_name.lower().replace(' ', ''))
            os.makedirs(src_dir)
            
            res_dir = os.path.join(self.build_dir, "res")
            os.makedirs(os.path.join(res_dir, "layout"))
            os.makedirs(os.path.join(res_dir, "values"))
            
            assets_dir = os.path.join(self.build_dir, "assets")
            os.makedirs(assets_dir)

            # Copy HTML
            if not os.path.exists(self.html_path):
                print(f"[ERROR] File {self.html_path} not found!")
                return
            shutil.copy(self.html_path, os.path.join(assets_dir, "index.html"))

            # Handle Icon
            icon_res_name = "@android:drawable/sym_def_app_icon"
            if self.icon_path and os.path.exists(self.icon_path):
                mipmap_dir = os.path.join(res_dir, "mipmap-hdpi")
                os.makedirs(mipmap_dir, exist_ok=True)
                ext = os.path.splitext(self.icon_path)[1].lower()
                dest_icon = os.path.join(mipmap_dir, "ic_launcher" + ext)
                shutil.copy(self.icon_path, dest_icon)
                icon_res_name = "@mipmap/ic_launcher"
            elif self.icon_path:
                print(f"[WARN] Icon path {self.icon_path} not found. Using default.")

            # --- 2. Generate AndroidManifest.xml ---
            manifest_content = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{package_name}"
    android:versionCode="1"
    android:versionName="{self.version}">
    <uses-sdk android:minSdkVersion="21" android:targetSdkVersion="33" />
    <uses-permission android:name="android.permission.INTERNET" />
    <application 
        android:label="{self.app_name}" 
        android:icon="{icon_res_name}"
        android:theme="@android:style/Theme.NoTitleBar"
        android:usesCleartextTraffic="true">
        <activity android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>"""
            with open(os.path.join(self.build_dir, "AndroidManifest.xml"), "w") as f:
                f.write(manifest_content)

            # --- 3. Generate Java Code ---
            java_content = f"""package {package_name};
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
            with open(os.path.join(src_dir, "MainActivity.java"), "w") as f:
                f.write(java_content)

            # --- 4. Compilation Steps ---
            print("1. Compiling resources (aapt2)...")
            subprocess.run([
                TOOLS["aapt2"], "compile", 
                "--dir", res_dir, 
                "-o", os.path.join(self.build_dir, "resources.zip")
            ], check=True, stdout=subprocess.DEVNULL)

            print("2. Linking resources...")
            subprocess.run([
                TOOLS["aapt2"], "link",
                "-I", TOOLS["android_jar"],
                "--manifest", os.path.join(self.build_dir, "AndroidManifest.xml"),
                "-o", os.path.join(self.build_dir, "unaligned.apk"),
                "-A", assets_dir,
                "--java", os.path.join(self.build_dir, "gen"),
                os.path.join(self.build_dir, "resources.zip"),
                "--auto-add-overlay"
            ], check=True, stdout=subprocess.DEVNULL)

            print("3. Compiling Java (javac)...")
            r_java = os.path.join(self.build_dir, "gen", *package_name.split("."), "R.java")
            subprocess.run([
                "javac", "-source", "1.8", "-target", "1.8",
                "-bootclasspath", TOOLS["android_jar"],
                "-classpath", TOOLS["android_jar"],
                os.path.join(src_dir, "MainActivity.java"),
                r_java
            ], check=True)

            print("4. Converting to Dex (d8)...")
            class_files = [
                os.path.join(src_dir, "MainActivity.class"),
                r_java.replace(".java", ".class"),
            ]
            # Include inner classes if they exist (R$layout.class etc)
            layout_class = r_java.replace(".java", "$layout.class")
            if os.path.exists(layout_class):
                class_files.append(layout_class)

            subprocess.run([
                TOOLS["d8"], "--output", self.build_dir,
                "--lib", TOOLS["android_jar"],
                *class_files
            ], check=True)

            print("5. Packaging APK...")
            with zipfile.ZipFile(os.path.join(self.build_dir, "unaligned.apk"), 'a') as zipf:
                zipf.write(os.path.join(self.build_dir, "classes.dex"), "classes.dex")

            print("6. Signing APK...")
            keystore = os.path.join(SDK_ROOT, "debug.keystore")
            if not os.path.exists(keystore):
                subprocess.run([
                    "keytool", "-genkey", "-v", "-keystore", keystore,
                    "-alias", "androiddebugkey", "-keyalg", "RSA", "-keysize", "2048",
                    "-validity", "10000", "-storepass", "android", "-keypass", "android",
                    "-dname", "CN=Android Debug,O=Android,C=US"
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            final_name = f"{self.app_name.replace(' ', '_')}.apk"
            final_path = os.path.abspath(final_name)
            
            subprocess.run([
                TOOLS["apksigner"], "sign",
                "--ks", keystore,
                "--ks-pass", "pass:android",
                "--out", final_path,
                os.path.join(self.build_dir, "unaligned.apk")
            ], check=True, stdout=subprocess.DEVNULL)

            print(f"\n[SUCCESS] APK Created Successfully: \n>> {final_path}")
            
            # Cleanup
            shutil.rmtree(self.build_dir)

        except subprocess.CalledProcessError as e:
            print(f"\n[ERROR] Build failed at command: {e.cmd}")
        except Exception as e:
            print(f"\n[ERROR] Unexpected error: {e}")

def main():
    parser = argparse.ArgumentParser(description="CLI Tool to convert HTML to Android APK")
    parser.add_argument("html_file", help="Path to the index.html file")
    args = parser.parse_args()

    print(f"Selected file: {args.html_file}")
    print("Enter App Details:")
    
    try:
        app_name = input("APP NAME (e.g., My App): ").strip()
        if not app_name: app_name = "MyWebApp"
        
        version = input("VERSION (e.g., 1.0): ").strip()
        if not version: version = "1.0"
        
        icon_path = input("ICON PATH (Enter for default): ").strip()
    except KeyboardInterrupt:
        print("\nCancelled by user.")
        sys.exit(0)

    # 1. Setup Environment (Install Java if missing, Download SDK)
    sdk_mgr = SDKManager()
    sdk_mgr.check_and_install()

    # 2. Build APK
    builder = APKBuilder(args.html_file, app_name, version, icon_path)
    builder.build()

if __name__ == "__main__":
    main()
