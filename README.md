### Web2Droid

Convert HTML/CSS/JS projects to native Android APKs and Google Play–ready App Bundles. A single, zero-dependency Python script for **Windows, Linux and macOS**. You don't need Android Studio, Gradle or admin rights.

On first run it downloads a portable JDK (Eclipse Temurin), the Android command-line tools and bundletool into `~/.web2droid` (override with `W2D_HOME`). Every download goes over verified HTTPS and is checked against its published checksum.

---

### Installation

**Linux / macOS**

```bash
git clone https://github.com/mrushchyshyn/web2droid.git
cd web2droid
chmod +x web2droid.py
sudo ln -s "$PWD/web2droid.py" /usr/local/bin/web2droid
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/mrushchyshyn/web2droid.git
cd web2droid
echo '@python "%~dp0web2droid.py" %*' | Out-File -Encoding ascii web2droid.cmd
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
[Environment]::SetEnvironmentVariable("Path", "$UserPath;$PWD", "User")
```

Restart the terminal afterwards.

---

### Usage

```bash
cd my-web-project
web2droid index.html
```

The **whole folder** containing `index.html` (JS, CSS, images, fonts, sub-folders) is bundled. Pages are served from `https://appassets.androidplatform.net/`, so `fetch()`, ES modules and relative paths work as they do on a normal web server.

Missing settings are asked interactively the first time. They are then saved to `web2droid.json` next to your HTML, and every later build reuses them and **increments `versionCode` automatically**.

Non-interactive / CI example:

```bash
web2droid index.html -y --accept-licenses \
  --name "My App" --version 1.2.0 --package com.mycompany.myapp \
  --icon icon.png --aab --keystore upload.jks
```

| Option | Meaning |
|---|---|
| `--name`, `--version`, `--package` | App label, versionName, application id |
| `--version-code N` | Explicit versionCode (default: previous + 1) |
| `--icon FILE` | Launcher icon (.png / .webp / .jpg, 512×512 recommended) |
| `--aab` / `--no-apk` | Build an App Bundle / skip the APK |
| `--keystore FILE --ks-alias NAME` | Release signing. Passwords come from `W2D_KS_PASS` / `W2D_KEY_PASS` or a prompt |
| `--new-keystore FILE` | Create a release (upload) key with keytool and use it |
| `--web-root DIR` | Bundle a different folder than the HTML file's folder |
| `--exclude REGEX` | Leave matching file names out (repeatable) |
| `--ble` | Add the `window.AndroidBLE` Bluetooth LE bridge |
| `--allow-cleartext` | Allow plain `http://` requests (off by default) |
| `--out-dir DIR` | Where to write outputs |
| `-y`, `--accept-licenses`, `--update`, `--keep-build` | Automation / maintenance |

### Publishing on Google Play

1. Create an upload key once: `web2droid index.html --new-keystore upload.jks`. **Back it up**, because without it you cannot update the app.
2. Use a real package name (`com.example.*` is rejected by Play).
3. Build with `--aab` and upload the `.aab`.

Without `--keystore`, builds are signed with a local debug key. That's fine for sideloading and testing, but Play won't accept them.

### App behaviour

- Links to other sites open in the system browser, so only your own pages run inside the app.
- The Back button navigates WebView history.
- Content is laid out below the system bars (Android 15 edge-to-edge).
- Target SDK 35, min SDK 24.

### BLE bridge (`--ble`)

JavaScript API: `AndroidBLE.requestPermissions()`, `hasPermissions()`, `startScan()` (10 s), `stopScan()`, `connect(mac)`, `disconnect()`, `write(serviceUuid, charUuid, hex)`.
Callbacks: `onBlePermissions(granted)`, `onBleDeviceFound(name, mac)`, `onBleConnected(mac)`, `onBleDisconnected()`, `onBleLog(msg)`.

---

### Requirements

- Windows / Linux / macOS, x86_64 or arm64
- Python 3.8+
- ~1.5 GB disk for the toolchain

## Links

- ✉️ **Contact:** [markorushchyshyn@gmail.com](mailto:markorushchyshyn@gmail.com)
