### Web2Droid

Convert HTML/CSS/JS projects to native Android APKs in seconds. A lightweight, zero-dependency Python CLI tool for Linux. No Android Studio required.

---

### ⚡ Quick Start (Linux)

### Installation

```bash
git clone https://github.com/mrushchyshyn/web2droid.git
cd web2droid
chmod +x web2droid.py
sudo cp web2droid.py /usr/local/bin/web2droid
```

---

### Usage

1. Navigate to your project folder.
2. Run the builder:
```bash
web2droid index.html
```
3. Follow the prompts for App Name, Version, Icon and Android App Bundle.
4. Find your signed .apk and .aab in the current directory.

---

### ⚡ Quick Start (Windows)

### Installation

Open PowerShell and run these commands one by one:

```bash
git clone https://github.com/mrushchyshyn/web2droid.git
cd web2droid
echo '@python "%~dp0web2droid_win.py" %*' | Out-File -Encoding ascii web2droid.cmd
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
[Environment]::SetEnvironmentVariable("Path", "$UserPath;$PWD", "User")
```

Restart your terminal (close and open PowerShell again) to apply changes.

---

### Usage

1. Navigate to your project folder.
2. Run the builder:
```bash
web2droid index.html
```
3. Follow the prompts for App Name, Version, Icon and Android App Bundle.
4. Find your signed .apk and .aab in the current directory.

---

### 📋 Requirements

- OS: Linux (x86_64) / Windows (x86_64)
- Python: 3.6+
- Permissions: sudo/admin access (only for initial setup).

---

### 📝 To-Do List

- Multi-file Project Support: Allow bundling of local JS, CSS, and image assets referenced in index.html.
- Cross-Platform Support: Add compatibility for macOS.

---

## 🔗 Links

- ✉️ **Contact:** [markorushchyshyn@gmail.com](mailto:markorushchyshyn@gmail.com)
