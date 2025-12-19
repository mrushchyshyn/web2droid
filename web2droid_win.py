import os
import sys
import shutil
import subprocess
import urllib.request
import zipfile
import time
import argparse
import platform
import ssl 

try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

# --- CONFIGURATION ---
USER_HOME = os.path.expanduser("~")
SDK_ROOT = os.path.join(USER_HOME, ".android_web_builder_sdk")

# URLs
CMD_TOOLS_URL = "https://dl.google.com/android/repository/commandlinetools-win-10406996_latest.zip"
BUNDLETOOL_URL = "https://github.com/google/bundletool/releases/download/1.15.6/bundletool-all-1.15.6.jar"
# Portable Java (Eclipse Temurin JDK 17)
JAVA_URL = "https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.10%2B7/OpenJDK17U-jdk_x64_windows_hotspot_17.0.10_7.zip"

BUILD_TOOLS_VERSION = "34.0.0"
PLATFORM_VERSION = "android-34"

# Tool paths (populated dynamically)
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
    """Manages the installation of Java, Android SDK, and Bundletool."""
    
    def __init__(self):
        self.java_home = os.path.join(SDK_ROOT, "jdk")
        self.java_bin = os.path.join(self.java_home, "bin")
        
    def check_and_install(self):
        if not os.path.exists(SDK_ROOT):
            os.makedirs(SDK_ROOT)
            
        print(f"[*] Checking environment in: {SDK_ROOT}")
        
        # 1. Setup Java
        self.ensure_java()
        
        # Update environment variables for this process only
        os.environ["JAVA_HOME"] = self.java_home
        os.environ["PATH"] = self.java_bin + os.pathsep + os.environ["PATH"]

        # 2. Check Android SDK
        if os.path.exists(os.path.join(SDK_ROOT, "platforms", PLATFORM_VERSION)):
            print("[OK] Android SDK found.")
        else:
            print("[!] SDK not found or incomplete. Starting setup...")
            self.install_sdk()

        # 3. Check Bundletool
        self.ensure_bundletool()
        
        self.resolve_tools()

    def ensure_java(self):
        local_java_exe = os.path.join(self.java_bin, "java.exe")
        if os.path.exists(local_java_exe):
            return

        print("[!] Java not found locally. Downloading Portable JDK 17...")
        try:
            java_zip = os.path.join(SDK_ROOT, "jdk.zip")
            print(f"--> Downloading JDK...")
            urllib.request.urlretrieve(JAVA_URL, java_zip)
            
            print("--> Extracting Java...")
            with zipfile.ZipFile(java_zip, 'r') as zip_ref:
                root_folder = zip_ref.namelist()[0].split('/')[0]
                zip_ref.extractall(SDK_ROOT)
            
            extracted_path = os.path.join(SDK_ROOT, root_folder)
            if os.path.exists(self.java_home):
                try: shutil.rmtree(self.java_home)
                except: pass
            
            time.sleep(1)
            os.rename(extracted_path, self.java_home)
            
            os.remove(java_zip)
            print("[OK] Portable Java installed.")
            
        except Exception as e:
            print(f"\n[ERROR] Failed to install Java: {e}")
            sys.exit(1)

    def install_sdk(self):
        try:
            cmd_tools_zip = os.path.join(SDK_ROOT, "cmdline-tools.zip")
            if not os.path.exists(os.path.join(SDK_ROOT, "cmdline-tools")):
                print(f"--> Downloading Command Line Tools...")
                urllib.request.urlretrieve(CMD_TOOLS_URL, cmd_tools_zip)

                with zipfile.ZipFile(cmd_tools_zip, 'r') as zip_ref:
                    zip_ref.extractall(SDK_ROOT)
                os.remove(cmd_tools_zip)

                # Fix directory structure for sdkmanager
                base_cmd = os.path.join(SDK_ROOT, "cmdline-tools")
                temp_rename = os.path.join(SDK_ROOT, "temp_tools_rename")
                
                if os.path.exists(temp_rename): shutil.rmtree(temp_rename)
                
                os.rename(base_cmd, temp_rename)
                os.makedirs(base_cmd)
                shutil.move(temp_rename, os.path.join(base_cmd, "latest"))

            sdkmanager_bin = os.path.join(SDK_ROOT, "cmdline-tools", "latest", "bin", "sdkmanager.bat")
            
            print("--> Installing Build Tools & Platform (Accepting licenses)...")
            
            install_cmd = [
                sdkmanager_bin, 
                "--sdk_root=" + SDK_ROOT,
                f"build-tools;{BUILD_TOOLS_VERSION}",
                f"platforms;{PLATFORM_VERSION}",
                "platform-tools"
            ]

            process = subprocess.Popen(
                install_cmd, 
                stdin=subprocess.PIPE, 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.PIPE,
                shell=True,
                env=os.environ
            )
            
            # Auto-accept licenses
            yes_input = (b'y\r\n' * 50)
            stdout, stderr = process.communicate(input=yes_input)

            if process.returncode != 0:
                print(f"[ERROR] sdkmanager failed: {stderr.decode('cp866', errors='ignore')}")
                sys.exit(1)
                
            print("[OK] SDK Installation complete.")

        except Exception as e:
            print(f"\n[ERROR] Failed to set up SDK: {e}")
            sys.exit(1)

    def ensure_bundletool(self):
        dest = os.path.join(SDK_ROOT, "bundletool.jar")
        if not os.path.exists(dest):
            print("--> Downloading Bundletool...")
            try:
                urllib.request.urlretrieve(BUNDLETOOL_URL, dest)
            except Exception as e:
                print(f"[ERROR] Failed to download bundletool: {e}")

    def resolve_tools(self):
        bt_path = os.path.join(SDK_ROOT, "build-tools", BUILD_TOOLS_VERSION)
        
        TOOLS["aapt2"] = os.path.join(bt_path, "aapt2.exe")
        TOOLS["d8"] = os.path.join(bt_path, "d8.bat")
        TOOLS["apksigner"] = os.path.join(bt_path, "apksigner.bat")
        TOOLS["zipalign"] = os.path.join(bt_path, "zipalign.exe")
        
        TOOLS["android_jar"] = os.path.join(SDK_ROOT, "platforms", PLATFORM_VERSION, "android.jar")
        TOOLS["bundletool"] = os.path.join(SDK_ROOT, "bundletool.jar")

class AppBuilder:
    def __init__(self, html_path, app_name, version, icon_path):
        self.html_path = os.path.abspath(html_path)
        self.app_name = app_name
        self.version = version
        self.icon_path = os.path.abspath(icon_path) if icon_path else None
        self.build_dir = os.path.abspath("build_temp_" + str(int(time.time())))
        
        self.package_name = f"com.example.{self.app_name.lower().replace(' ', '')}"
        self.keystore_path = os.path.join(SDK_ROOT, "debug.keystore")
    
    def prepare_directories(self):
        if os.path.exists(self.build_dir): 
            try: shutil.rmtree(self.build_dir)
            except: pass
        os.makedirs(self.build_dir)
        
        path_parts = ["java", "com", "example", self.app_name.lower().replace(' ', '')]
        self.src_dir = os.path.join(self.build_dir, *path_parts)
        os.makedirs(self.src_dir)
        
        self.res_dir = os.path.join(self.build_dir, "res")
        os.makedirs(os.path.join(self.res_dir, "layout"))
        os.makedirs(os.path.join(self.res_dir, "values"))
        
        self.assets_dir = os.path.join(self.build_dir, "assets")
        os.makedirs(self.assets_dir)

    def copy_assets(self):
        if not os.path.exists(self.html_path):
            raise Exception(f"File {self.html_path} not found!")
        shutil.copy(self.html_path, os.path.join(self.assets_dir, "index.html"))

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
    <uses-sdk android:minSdkVersion="24" android:targetSdkVersion="34" />
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
        with open(os.path.join(self.build_dir, "AndroidManifest.xml"), "w", encoding='utf-8') as f:
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
        with open(os.path.join(self.src_dir, "MainActivity.java"), "w", encoding='utf-8') as f:
            f.write(content)

    def compile_common(self):
        print("--> Compiling resources (aapt2)...")
        compiled_res_zip = os.path.join(self.build_dir, "resources.zip")
        subprocess.run([
            TOOLS["aapt2"], "compile", 
            "--dir", self.res_dir, 
            "-o", compiled_res_zip
        ], check=True, stdout=subprocess.DEVNULL)

        print("--> Generating R.java...")
        subprocess.run([
            TOOLS["aapt2"], "link",
            "-I", TOOLS["android_jar"],
            "--manifest", os.path.join(self.build_dir, "AndroidManifest.xml"),
            "-o", os.path.join(self.build_dir, "temp_link.apk"),
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

        cmd = [TOOLS["d8"]] + ["--output", self.build_dir, "--lib", TOOLS["android_jar"]] + class_files
        subprocess.run(cmd, check=True, shell=True)
        
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
        
        subprocess.run([
            TOOLS["aapt2"], "link",
            "-I", TOOLS["android_jar"],
            "--manifest", os.path.join(self.build_dir, "AndroidManifest.xml"),
            "-o", unaligned_apk,
            "-A", self.assets_dir,
            compiled_res_zip,
            "--auto-add-overlay"
        ], check=True, stdout=subprocess.DEVNULL)

        with zipfile.ZipFile(unaligned_apk, 'a') as zipf:
            zipf.write(os.path.join(self.build_dir, "classes.dex"), "classes.dex")

        final_apk = os.path.abspath(f"{self.app_name.replace(' ', '_')}.apk")
        
        subprocess.run([
            TOOLS["apksigner"], "sign",
            "--ks", self.keystore_path,
            "--ks-pass", "pass:android",
            "--out", final_apk,
            unaligned_apk
        ], check=True, stdout=subprocess.DEVNULL, shell=True)
        
        print(f"[SUCCESS] APK Created: {final_apk}")

    def build_aab(self, compiled_res_zip):
        print("\n[ Building AAB (App Bundle) ]")
        
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

        base_dir = os.path.join(self.build_dir, "base_module")
        os.makedirs(os.path.join(base_dir, "manifest"))
        os.makedirs(os.path.join(base_dir, "dex"))
        
        with zipfile.ZipFile(proto_apk, 'r') as z:
            z.extractall(base_dir)

        shutil.move(os.path.join(base_dir, "AndroidManifest.xml"), 
                    os.path.join(base_dir, "manifest", "AndroidManifest.xml"))
        
        shutil.copy(os.path.join(self.build_dir, "classes.dex"), 
                    os.path.join(base_dir, "dex", "classes.dex"))

        module_zip = os.path.join(self.build_dir, "base.zip")
        self._zip_dir(base_dir, module_zip)

        final_aab = os.path.abspath(f"{self.app_name.replace(' ', '_')}.aab")
        if os.path.exists(final_aab): os.remove(final_aab)
        
        subprocess.run([
            "java", "-jar", TOOLS["bundletool"],
            "build-bundle",
            f"--modules={module_zip}",
            f"--output={final_aab}"
        ], check=True)

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
            
            compiled_res = self.compile_common()
            
            if build_apk: self.build_apk(compiled_res)
            if build_aab: self.build_aab(compiled_res)
            
            time.sleep(1)
            try: shutil.rmtree(self.build_dir)
            except: pass
            
        except subprocess.CalledProcessError as e:
            print(f"\n[ERROR] Command failed with code {e.returncode}")
        except Exception as e:
            print(f"\n[ERROR] {e}")

def main():
    parser = argparse.ArgumentParser(description="HTML to Android Converter (Windows Auto-Install)")
    parser.add_argument("html_file", help="Path to index.html")
    args = parser.parse_args()

    app_name = input("App Name (e.g., My App): ").strip() or "MyWebApp"
    version = input("Version (e.g., 1.0): ").strip() or "1.0"
    icon_path = input("Icon Path (Enter for default): ").strip().replace('"', '')
    
    do_apk = True
    aab_input = input("Generate AAB for Google Play? [y/N]: ").strip().lower()
    do_aab = aab_input == 'y'

    sdk_mgr = SDKManager()
    sdk_mgr.check_and_install()

    builder = AppBuilder(args.html_file, app_name, version, icon_path)
    builder.run(build_apk=do_apk, build_aab=do_aab)

if __name__ == "__main__":
    main()
