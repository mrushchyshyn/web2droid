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
JAVA_URL = "https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.10%2B7/OpenJDK17U-jdk_x64_windows_hotspot_17.0.10_7.zip"

BUILD_TOOLS_VERSION = "34.0.0"
PLATFORM_VERSION = "android-34"

# Tool paths
TOOLS = {
    "sdkmanager": "", "aapt2": "", "d8": "", "apksigner": "", 
    "android_jar": "", "zipalign": "", "bundletool": ""
}

class SDKManager:
    def __init__(self):
        self.java_home = os.path.join(SDK_ROOT, "jdk")
        self.java_bin = os.path.join(self.java_home, "bin")
        
    def check_and_install(self):
        if not os.path.exists(SDK_ROOT):
            os.makedirs(SDK_ROOT)
        print(f"[*] Checking environment in: {SDK_ROOT}")
        self.ensure_java()
        os.environ["JAVA_HOME"] = self.java_home
        os.environ["PATH"] = self.java_bin + os.pathsep + os.environ["PATH"]

        if os.path.exists(os.path.join(SDK_ROOT, "platforms", PLATFORM_VERSION)):
            print("[OK] Android SDK found.")
        else:
            print("[!] SDK not found. Starting download...")
            self.install_sdk()

        self.ensure_bundletool()
        self.resolve_tools()

    def ensure_java(self):
        local_java_exe = os.path.join(self.java_bin, "java.exe")
        if os.path.exists(local_java_exe): return

        print("[!] Java not found. Downloading Portable JDK 17...")
        try:
            java_zip = os.path.join(SDK_ROOT, "jdk.zip")
            urllib.request.urlretrieve(JAVA_URL, java_zip)
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
            print(f"\n[ERROR] Java installation failed: {e}")
            sys.exit(1)

    def install_sdk(self):
        try:
            cmd_tools_zip = os.path.join(SDK_ROOT, "cmdline-tools.zip")
            if not os.path.exists(os.path.join(SDK_ROOT, "cmdline-tools")):
                urllib.request.urlretrieve(CMD_TOOLS_URL, cmd_tools_zip)
                with zipfile.ZipFile(cmd_tools_zip, 'r') as zip_ref:
                    zip_ref.extractall(SDK_ROOT)
                os.remove(cmd_tools_zip)

                base_cmd = os.path.join(SDK_ROOT, "cmdline-tools")
                temp_rename = os.path.join(SDK_ROOT, "temp_tools_rename")
                if os.path.exists(temp_rename): shutil.rmtree(temp_rename)
                os.rename(base_cmd, temp_rename)
                os.makedirs(base_cmd)
                shutil.move(temp_rename, os.path.join(base_cmd, "latest"))

            sdkmanager_bin = os.path.join(SDK_ROOT, "cmdline-tools", "latest", "bin", "sdkmanager.bat")
            install_cmd = [
                sdkmanager_bin, 
                "--sdk_root=" + SDK_ROOT,
                f"build-tools;{BUILD_TOOLS_VERSION}",
                f"platforms;{PLATFORM_VERSION}",
                "platform-tools"
            ]
            process = subprocess.Popen(
                install_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, 
                stderr=subprocess.PIPE, shell=True, env=os.environ
            )
            yes_input = (b'y\r\n' * 50)
            stdout, stderr = process.communicate(input=yes_input)
            if process.returncode != 0:
                print(f"[ERROR] sdkmanager failed: {stderr.decode('cp866', errors='ignore')}")
                sys.exit(1)
            print("[OK] SDK installed.")
        except Exception as e:
            print(f"\n[ERROR] SDK setup failed: {e}")
            sys.exit(1)

    def ensure_bundletool(self):
        dest = os.path.join(SDK_ROOT, "bundletool.jar")
        if not os.path.exists(dest):
            try: urllib.request.urlretrieve(BUNDLETOOL_URL, dest)
            except Exception as e: print(f"[ERROR] Failed to download bundletool: {e}")

    def resolve_tools(self):
        bt_path = os.path.join(SDK_ROOT, "build-tools", BUILD_TOOLS_VERSION)
        TOOLS["aapt2"] = os.path.join(bt_path, "aapt2.exe")
        TOOLS["d8"] = os.path.join(bt_path, "d8.bat")
        TOOLS["apksigner"] = os.path.join(bt_path, "apksigner.bat")
        TOOLS["zipalign"] = os.path.join(bt_path, "zipalign.exe")
        TOOLS["android_jar"] = os.path.join(SDK_ROOT, "platforms", PLATFORM_VERSION, "android.jar")
        TOOLS["bundletool"] = os.path.join(SDK_ROOT, "bundletool.jar")

class AppBuilder:
    def __init__(self, html_path, app_name, version, icon_path, use_ble):
        self.html_path = os.path.abspath(html_path)
        self.app_name = app_name
        self.version = version
        self.icon_path = os.path.abspath(icon_path) if icon_path else None
        self.use_ble = use_ble
        self.build_dir = os.path.abspath("build_temp_" + str(int(time.time())))
        self.package_name = f"com.example.{self.app_name.lower().replace(' ', '')}"
        self.keystore_path = os.path.join(SDK_ROOT, "debug.keystore")
    
    def prepare_directories(self):
        if os.path.exists(self.build_dir): 
            try: shutil.rmtree(self.build_dir)
            except: pass
        os.makedirs(self.build_dir)
        path_parts = ["java"] + self.package_name.split(".")
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
        ble_permissions = """
    <!-- BLE Permissions -->
    <uses-permission android:name="android.permission.BLUETOOTH" />
    <uses-permission android:name="android.permission.BLUETOOTH_ADMIN" />
    <uses-permission android:name="android.permission.ACCESS_FINE_LOCATION" />
    <uses-permission android:name="android.permission.ACCESS_COARSE_LOCATION" />
    <uses-permission android:name="android.permission.BLUETOOTH_SCAN" />
    <uses-permission android:name="android.permission.BLUETOOTH_CONNECT" />
""" if self.use_ble else ""

        content = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{self.package_name}"
    android:versionCode="1"
    android:versionName="{self.version}">
    <uses-sdk android:minSdkVersion="24" android:targetSdkVersion="34" />
    <uses-permission android:name="android.permission.INTERNET" />
    {ble_permissions}
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
        if self.use_ble:
            java_template = """package {PACKAGE_NAME};

import android.Manifest;
import android.app.Activity;
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
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import java.util.UUID;

public class MainActivity extends Activity {
    private WebView webView;
    private BluetoothAdapter bluetoothAdapter;
    private BluetoothLeScanner bluetoothLeScanner;
    private BluetoothGatt bluetoothGatt;
    private boolean isScanning = false;
    private Handler handler = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        webView = new WebView(this);
        WebSettings webSettings = webView.getSettings();
        webSettings.setJavaScriptEnabled(true);
        webSettings.setDomStorageEnabled(true);
        webView.setWebViewClient(new WebViewClient());
        
        webView.addJavascriptInterface(new BLEBridge(), "AndroidBLE");
        webView.loadUrl("file:///android_asset/index.html");
        setContentView(webView);

        BluetoothManager bluetoothManager = (BluetoothManager) getSystemService(Context.BLUETOOTH_SERVICE);
        if (bluetoothManager != null) {
            bluetoothAdapter = bluetoothManager.getAdapter();
        }
    }

    private void logToJS(final String message) {
        handler.post(new Runnable() {
            @Override
            public void run() {
                String safeMsg = message.replace("'", "\\\\'");
                webView.evaluateJavascript("if(window.onBleLog) window.onBleLog('" + safeMsg + "');", null);
            }
        });
    }

    public class BLEBridge {
        @JavascriptInterface
        public void requestPermissions() {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                MainActivity.this.requestPermissions(new String[]{
                    Manifest.permission.BLUETOOTH_SCAN,
                    Manifest.permission.BLUETOOTH_CONNECT,
                    Manifest.permission.ACCESS_FINE_LOCATION
                }, 1);
            } else {
                MainActivity.this.requestPermissions(new String[]{
                    Manifest.permission.ACCESS_FINE_LOCATION
                }, 1);
            }
            logToJS("Permissions requested.");
        }

        @JavascriptInterface
        public void startScan() {
            if (bluetoothAdapter == null || !bluetoothAdapter.isEnabled()) {
                logToJS("Error: Bluetooth is disabled or not supported.");
                return;
            }
            if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
                logToJS("Error: Missing Location/BLE permissions. Call requestPermissions().");
                return;
            }

            bluetoothLeScanner = bluetoothAdapter.getBluetoothLeScanner();
            if (bluetoothLeScanner == null) {
                logToJS("Error: Failed to get BLE Scanner.");
                return;
            }

            if (!isScanning) {
                handler.post(new Runnable() {
                    @Override
                    public void run() {
                        bluetoothLeScanner.startScan(scanCallback);
                        isScanning = true;
                        logToJS("Scan started.");
                        // Stop scanning after 10 seconds
                        handler.postDelayed(new Runnable() {
                            @Override
                            public void run() {
                                stopScan();
                            }
                        }, 10000);
                    }
                });
            }
        }

        @JavascriptInterface
        public void stopScan() {
            if (isScanning && bluetoothLeScanner != null) {
                try {
                    bluetoothLeScanner.stopScan(scanCallback);
                } catch (SecurityException e) {
                    logToJS("Stop error: Missing permissions.");
                }
                isScanning = false;
                logToJS("Scan stopped.");
            }
        }

        @JavascriptInterface
        public void connect(String macAddress) {
            stopScan();
            logToJS("Connecting to " + macAddress + "...");
            try {
                BluetoothDevice device = bluetoothAdapter.getRemoteDevice(macAddress);
                bluetoothGatt = device.connectGatt(MainActivity.this, false, gattCallback);
            } catch (SecurityException e) {
                logToJS("Connection error: Missing permissions.");
            } catch (IllegalArgumentException e) {
                logToJS("Connection error: Invalid MAC.");
            }
        }

        @JavascriptInterface
        public void disconnect() {
            if (bluetoothGatt != null) {
                try {
                    bluetoothGatt.disconnect();
                    bluetoothGatt.close();
                    bluetoothGatt = null;
                    logToJS("Disconnected.");
                    handler.post(new Runnable() {
                        @Override
                        public void run() {
                            webView.evaluateJavascript("if(window.onBleDisconnected) window.onBleDisconnected();", null);
                        }
                    });
                } catch (SecurityException e) {}
            }
        }

        @JavascriptInterface
        public void write(String serviceUuidStr, String charUuidStr, String hexData) {
            if (bluetoothGatt == null) {
                logToJS("Error: No active connection.");
                return;
            }
            try {
                BluetoothGattService service = bluetoothGatt.getService(UUID.fromString(serviceUuidStr));
                if (service == null) { logToJS("Service not found."); return; }
                
                BluetoothGattCharacteristic characteristic = service.getCharacteristic(UUID.fromString(charUuidStr));
                if (characteristic == null) { logToJS("Characteristic not found."); return; }

                byte[] data = hexStringToByteArray(hexData);
                characteristic.setValue(data);
                boolean success = bluetoothGatt.writeCharacteristic(characteristic);
                logToJS("Sending data (" + hexData + "): " + (success ? "Success" : "Error"));
            } catch (Exception e) {
                logToJS("Write error: " + e.getMessage());
            }
        }
    }

    private ScanCallback scanCallback = new ScanCallback() {
        @Override
        public void onScanResult(int callbackType, ScanResult result) {
            super.onScanResult(callbackType, result);
            try {
                BluetoothDevice device = result.getDevice();
                String name = device.getName();
                final String address = device.getAddress();
                if (name == null || name.isEmpty()) name = "Unknown Device";
                
                final String finalName = name;
                handler.post(new Runnable() {
                    @Override
                    public void run() {
                        String js = String.format("if(window.onBleDeviceFound) window.onBleDeviceFound('%s', '%s');", 
                            finalName.replace("'", "\\\\'"), address);
                        webView.evaluateJavascript(js, null);
                    }
                });
            } catch (SecurityException e) {}
        }
    };

    private BluetoothGattCallback gattCallback = new BluetoothGattCallback() {
        @Override
        public void onConnectionStateChange(final BluetoothGatt gatt, int status, int newState) {
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                logToJS("Connected to GATT server. Discovering services...");
                try {
                    bluetoothGatt.discoverServices();
                    handler.post(new Runnable() {
                        @Override
                        public void run() {
                            webView.evaluateJavascript("if(window.onBleConnected) window.onBleConnected('" + gatt.getDevice().getAddress() + "');", null);
                        }
                    });
                } catch (SecurityException e) {}
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                logToJS("Disconnected from GATT server.");
                handler.post(new Runnable() {
                    @Override
                    public void run() {
                        webView.evaluateJavascript("if(window.onBleDisconnected) window.onBleDisconnected();", null);
                    }
                });
            }
        }

        @Override
        public void onServicesDiscovered(BluetoothGatt gatt, int status) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                logToJS("Services discovered. Ready.");
            } else {
                logToJS("Service discovery error: " + status);
            }
        }
    };

    // Utility to convert HEX string "00FF" to byte[]
    private byte[] hexStringToByteArray(String s) {
        int len = s.length();
        byte[] data = new byte[len / 2];
        for (int i = 0; i < len; i += 2) {
            data[i / 2] = (byte) ((Character.digit(s.charAt(i), 16) << 4)
                                 + Character.digit(s.charAt(i+1), 16));
        }
        return data;
    }
}
"""
        else:
            # Спрощений шаблон без BLE
            java_template = """package {PACKAGE_NAME};

import android.app.Activity;
import android.os.Bundle;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

public class MainActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        WebView webView = new WebView(this);
        WebSettings webSettings = webView.getSettings();
        webSettings.setJavaScriptEnabled(true);
        webSettings.setDomStorageEnabled(true);
        webView.setWebViewClient(new WebViewClient());
        webView.loadUrl("file:///android_asset/index.html");
        setContentView(webView);
    }
}
"""

        content = java_template.replace("{PACKAGE_NAME}", self.package_name)
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
        
        java_files = []
        for root, _, files in os.walk(self.src_dir):
            for file in files:
                if file.endswith(".java"):
                    java_files.append(os.path.join(root, file))
        java_files.append(r_java)

        subprocess.run([
            "javac", "-source", "1.8", "-target", "1.8",
            "-encoding", "utf-8",  # Added UTF-8 encoding flag
            "-bootclasspath", TOOLS["android_jar"],
            "-classpath", TOOLS["android_jar"]
        ] + java_files, check=True)

        print("--> Converting to Dex (d8)...")
        class_files = []
        for root, _, files in os.walk(self.src_dir):
            for file in files:
                if file.endswith(".class"):
                    class_files.append(os.path.join(root, file))
        
        r_class_dir = os.path.join(self.build_dir, "gen", *self.package_name.split("."))
        for file in os.listdir(r_class_dir):
            if file.endswith(".class"):
                class_files.append(os.path.join(r_class_dir, file))

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
        if os.path.exists(final_apk): os.remove(final_apk)
        
        subprocess.run([
            TOOLS["apksigner"], "sign",
            "--ks", self.keystore_path,
            "--ks-pass", "pass:android",
            "--out", final_apk,
            unaligned_apk
        ], check=True, stdout=subprocess.DEVNULL, shell=True)
        
        print(f"[SUCCESS] APK Created: {final_apk}")

    def _zip_dir(self, dir_path, zip_path):
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(dir_path):
                for file in files:
                    abs_path = os.path.join(root, file)
                    rel_path = os.path.relpath(abs_path, dir_path)
                    zipf.write(abs_path, rel_path)

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
    parser = argparse.ArgumentParser(description="HTML to Android APK/AAB Builder")
    parser.add_argument("html_file", help="Path to index.html")
    args = parser.parse_args()

    app_name = input("App Name (e.g., My App): ").strip() or "WebApp"
    version = input("Version (e.g., 1.0): ").strip() or "1.0"
    icon_path = input("Icon Path (Enter for default): ").strip().replace('"', '')
    
    ble_input = input("Include BLE (Bluetooth) support? [y/N]: ").strip().lower()
    use_ble = ble_input == 'y'

    do_apk = True
    aab_input = input("Generate AAB for Google Play? [y/N]: ").strip().lower()
    do_aab = aab_input == 'y'
    
    sdk_mgr = SDKManager()
    sdk_mgr.check_and_install()

    builder = AppBuilder(args.html_file, app_name, version, icon_path, use_ble)
    builder.run(build_apk=do_apk, build_aab=do_aab)

if __name__ == "__main__":
    main()
