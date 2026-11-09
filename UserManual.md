# Reality Later — User Manual

*The real world can wait.*

Reality Later is a self-hosted AI roleplay application. It runs on your computer and is accessed through a web browser, including from another device such as your phone.

## Table of Contents

- [1. Installation](#1-installation)
  - [1.1. Requirements](#11-requirements)
  - [1.2. Install from a package (recommended)](#12-install-from-a-package-recommended)
    - [1.2.1. Windows](#121-windows)
    - [1.2.2. Linux (Flatpak)](#122-linux-flatpak)
  - [1.3. Install from source (advanced)](#13-install-from-source-advanced)
    - [1.3.1. Windows](#131-windows)
    - [1.3.2. macOS](#132-macos)
    - [1.3.3. Linux](#133-linux)
    - [1.3.4. Linux systemd service](#134-linux-systemd-service)
- [2. Configuration](#2-configuration)
  - [2.1. Default settings](#21-default-settings)
  - [2.2. Where to find the configuration file](#22-where-to-find-the-configuration-file)
  - [2.3. Customizing the configuration](#23-customizing-the-configuration)
- [3. First launch and connection](#3-first-launch-and-connection)
- [4. Updating Reality Later](#4-updating-reality-later)

## 1. Installation

### 1.1. Requirements

- **Windows, macOS, or Linux.**
- An **[OpenRouter](https://openrouter.ai/)** account and API key. OpenRouter is currently the only supported AI provider.
- An internet connection for installation and OpenRouter requests.

**For packaged installations:** Python and Git do not need to be installed separately. Use a package for your operating system when one is available.

**For installation from source:** You need Git and **Python 3.10–3.13** (inclusive), including support for Python virtual environments. **Python 3.13 is recommended.** Versions **3.12 and 3.13 have been tested**; 3.10 and 3.11 are expected to work but have not been confirmed by these tests. **Python 3.14 is not currently supported**, because some dependencies are not yet available for it.

Reality Later's Python dependencies are declared in `pyproject.toml`; no `requirements.txt` is needed. The source installation instructions use a virtual environment to avoid installing dependencies into system Python.

### 1.2. Install from a package (recommended)

For most users, this is the simplest option. Packaged versions include the required Python runtime and application dependencies, so you do not have to configure a development environment.

Download the appropriate build from the [GitHub Releases page](https://github.com/MiryanaNovak/reality-later/releases) **when a packaged release is available**. If no build is provided for your operating system yet, use the [source installation](#13-install-from-source-advanced) instructions below.

#### 1.2.1. Windows

1. Download the Windows executable provided in the release assets.
2. Run the downloaded application. Follow any instructions supplied with that release.
3. Reality Later should open its **This computer** page in your browser automatically. Otherwise, check the address displayed by the application.

#### 1.2.2. Linux (Flatpak)

Download the `.flatpak` bundle from the release assets, if available. You need [Flatpak](https://flatpak.org/setup/) installed on your system.

You can install the downloaded bundle using your graphical software manager, or from a terminal in the directory containing it:

```bash
flatpak install --user ./YOUR_DOWNLOADED_FILE.flatpak
```

Replace `YOUR_DOWNLOADED_FILE.flatpak` with the name of the file you downloaded. Then launch Reality Later from your application menu.

The `--user` option installs the application only for your user account. If you prefer a system-wide installation, omit it (your system may ask for administrator authorization). You can then start Reality later from your desktop launcher or from the termina with:

```bash
flatpak run io.github.MiryanaNovak.RealityLater
```

Reality Later should open its **This computer** page in your browser automatically. Otherwise, check the address displayed by the application.

### 1.3. Install from source (advanced)

Use this method if a package is unavailable for your platform, or if you want to run the source code directly.

**Check the Python interpreter before creating your virtual environment.** It must report Python **3.10, 3.11, 3.12, or 3.13**. Do not use Python 3.14, even if it is your operating system's default. The examples prefer Python 3.13; you can substitute another supported version.

#### 1.3.1. Windows

Install [Python](https://www.python.org/downloads/windows/) and [Git](https://git-scm.com/downloads/win) if needed. Open **PowerShell** and list the installed Python versions:

```powershell
py -0p
py -3.13 --version
```

The second command must report Python 3.13.x. If you have another supported version instead, substitute `-3.12`, `-3.11`, or `-3.10` in both that command and the virtual environment command below. If no supported version is installed, install Python 3.13 first.

```powershell
# Download the source code
git clone https://github.com/MiryanaNovak/reality-later.git
cd reality-later

# Create a virtual environment with the version you verified
py -3.13 -m venv .venv

# Verify the virtual environment's Python version
.\.venv\Scripts\python.exe --version

# Install the app and its dependencies
.\.venv\Scripts\python.exe -m pip install .

# Start Reality Later
.\.venv\Scripts\python.exe main.py
```

Reality Later should open its **This computer** page in your browser automatically. Otherwise, check the address displayed by the application.

To start it again later, open PowerShell in the `reality-later` directory and run:

```powershell
.\.venv\Scripts\python.exe main.py
```

Leave the process running while using the application. Press **Ctrl+C** to stop it.

> If `py` is unavailable, install the Python Launcher or use the corresponding full path to a supported Python interpreter. Verify its version before creating the virtual environment.

#### 1.3.2. macOS

Install a supported version of [Python](https://www.python.org/downloads/macos/) and Git if needed. Git can be installed with Apple's Command Line Tools (`xcode-select --install`).

Open **Terminal**. Check which Python version is available:

```bash
python3 --version
python3.13 --version
```

Use the command for the supported Python installation you want. In the example below, `python3.13` is used. If you have Python 3.12, 3.11, or 3.10 instead, replace `python3.13` with that interpreter's command. Your system's default `python3` can also be used if it reports a supported version.

```bash
# Download the source code
git clone https://github.com/MiryanaNovak/reality-later.git
cd reality-later

# Create a virtual environment with the version you verified
python3.13 -m venv .venv

# Verify the virtual environment's Python version
.venv/bin/python --version

# Install the app and its dependencies
.venv/bin/python -m pip install .

# Start Reality Later
.venv/bin/python main.py
```

Reality Later should open its **This computer** page in your browser automatically. Otherwise, check the address displayed by the application.

To launch it again:

```bash
cd reality-later  # if you are not already in this directory
.venv/bin/python main.py
```

Keep Terminal open while using the app; press **Ctrl+C** to stop it.

#### 1.3.3. Linux

Install Git, a supported Python version (3.10–3.13), and its virtual environment support. On **Ubuntu/Debian**, first check the distribution's Python version:

```bash
sudo apt update
sudo apt install git python3 python3-venv
python3 --version
```

If `python3` reports **3.10–3.13**, you can use it. If it reports **3.14** (or an older unsupported version), install a separate supported interpreter, such as Python 3.13, plus its `venv` support, using packages appropriate to your distribution. Do not replace the system's default `python3` interpreter.

On another distribution, install the equivalent packages using its package manager. Verify the version of the interpreter you intend to use. The example below uses `python3.13`; replace it with `python3` or another supported interpreter if appropriate.

Run these commands as your **normal user**, without `sudo`:

```bash
# Check the selected Python version
python3.13 --version

# Download the source code
git clone https://github.com/MiryanaNovak/reality-later.git
cd reality-later

# Create a virtual environment
python3.13 -m venv .venv

# Verify the virtual environment's Python version
.venv/bin/python --version

# Install the app and its dependencies
.venv/bin/python -m pip install .

# Start Reality Later
.venv/bin/python main.py
```

Reality Later should open its **This computer** page in your browser automatically. Otherwise, check the address displayed by the application.

To start it again:

```bash
cd reality-later
.venv/bin/python main.py
```

Keep the terminal running; press **Ctrl+C** to stop the app.

#### 1.3.4. Linux systemd service

This option is intended for a Linux machine that runs Reality Later as a background server, starting automatically at boot. It uses:

- `/opt/reality-later` for the Git checkout, application code, and virtual environment;
- a dedicated **`reality-later`** system account, without an interactive login;
- writable `data/` and `certificates/` directories, with the rest of the application read-only to the service;
- systemd sandboxing to restrict the application's access to the rest of the machine.

The instructions below target **Ubuntu/Debian with systemd**. They assume **a new installation** and clone the repository **directly into `/opt/reality-later`**. Do not run these steps over an existing installation without first backing up its data and configuration.

##### Install prerequisites

```bash
sudo apt update
sudo apt install git python3 python3-venv
python3 --version
```

Use a Python interpreter in the **3.10–3.13** range; Python 3.14 is not supported. The commands below use `python3.13` as an example. Install Python 3.13 and its matching `venv` package if necessary, or substitute another supported interpreter (such as `python3` when it reports 3.12) throughout the following steps. Verify the chosen version before continuing:

```bash
python3.13 --version
```

##### Create the service account

```bash
sudo adduser --system --group \
    --home /var/lib/reality-later --no-create-home \
    reality-later
```

This creates a dedicated system user and group. Do **not** give it sudo access.

##### Clone the source directly into `/opt`

```bash
sudo git clone https://github.com/MiryanaNovak/reality-later.git /opt/reality-later
```

The checkout is managed by root so the service account will not be able to modify its own program files.

##### Create the virtual environment and install dependencies

```bash
sudo python3.13 -m venv /opt/reality-later/.venv
sudo /opt/reality-later/.venv/bin/python -m pip install /opt/reality-later
```

These commands use `sudo` **only to prepare the root-owned installation**. Reality Later itself will run as the unprivileged `reality-later` user.

##### Configure filesystem permissions

Make the code and virtual environment readable, but not writable, by the service account:

```bash
sudo chown -R root:reality-later /opt/reality-later
sudo chmod -R u+rwX,g+rX,go-w,o-rwx /opt/reality-later
```

Create persistent directories and give only the service account access to them:

```bash
sudo mkdir -p /opt/reality-later/data /opt/reality-later/certificates
sudo chown -R reality-later:reality-later \
    /opt/reality-later/data /opt/reality-later/certificates
sudo chmod -R u+rwX,go-rwx \
    /opt/reality-later/data /opt/reality-later/certificates
```

The application can now save conversations, generated memories, paired-device information, and HTTPS certificates, but cannot change its own source code or installed dependencies.

A configuration file is **not required**. Reality Later uses its built-in defaults when `config.toml` does not exist. If you need to customize the service, create the file from the example and edit it:

```bash
sudo cp /opt/reality-later/config.example.toml /opt/reality-later/config.toml
sudo chown root:reality-later /opt/reality-later/config.toml
sudo chmod 640 /opt/reality-later/config.toml
sudoedit /opt/reality-later/config.toml
```

The service can read this file but cannot modify it. This file is local configuration and is not tracked by Git. See [Configuration](#2-configuration) for details.

##### Create the systemd unit

Open a new unit file:

```bash
sudo nano /etc/systemd/system/reality-later.service
```

Paste the following configuration:

```ini
[Unit]
Description=Reality Later AI Roleplay Server
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=reality-later
Group=reality-later
WorkingDirectory=/opt/reality-later
ExecStart=/opt/reality-later/.venv/bin/python main.py --no-browser --no-tray

Restart=on-failure
RestartSec=5

# Privilege restrictions
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=

# Filesystem isolation
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/reality-later/data /opt/reality-later/certificates
StateDirectory=reality-later
StateDirectoryMode=0700
Environment=HOME=/var/lib/reality-later

# Temporary files and hardware
PrivateTmp=yes
PrivateDevices=yes

# Protect the host
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectProc=invisible

# Required socket families, including AF_NETLINK
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK

# Other restrictions
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
SystemCallArchitectures=native
RestrictNamespaces=yes
SystemCallFilter=~@mount @reboot @swap @raw-io @module @obsolete
UMask=0077

[Install]
WantedBy=multi-user.target
```

The `--no-browser` option prevents Reality Later from attempting to open a desktop browser when started in the background. The `--no-tray` option prevents it from attempting to register a system tray icon.

##### Enable and start the service

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now reality-later
systemctl status reality-later
```

The expected status is **`active (running)`**. The service now starts automatically at boot, without requiring anyone to log in.

If startup fails, inspect the logs:

```bash
journalctl -u reality-later -n 100 --no-pager
```

##### Manage the service

```bash
sudo systemctl start reality-later      # Start
sudo systemctl stop reality-later       # Stop
sudo systemctl restart reality-later    # Restart
systemctl status reality-later          # Status
journalctl -u reality-later -f          # Live logs
```

To stop it from starting at boot:

```bash
sudo systemctl disable --now reality-later
```

##### Update a systemd installation

Stop Reality Later before updating it. The following commands update the root-owned Git checkout and Python environment while preserving `data/` and `certificates/`:

```bash
sudo systemctl stop reality-later

sudo git -C /opt/reality-later pull --ff-only
sudo /opt/reality-later/.venv/bin/python -m pip install /opt/reality-later

# Reapply permissions after an update or package build
sudo chown -R root:reality-later /opt/reality-later
sudo chmod -R u+rwX,g+rX,go-w,o-rwx /opt/reality-later
sudo chown -R reality-later:reality-later \
    /opt/reality-later/data /opt/reality-later/certificates
sudo chmod -R u+rwX,go-rwx \
    /opt/reality-later/data /opt/reality-later/certificates

sudo systemctl start reality-later
```

## 2. Configuration

### 2.1. Default settings

**You do not need to create or edit a configuration file to use Reality Later.** If no `config.toml` is present, the application uses its built-in defaults, which should be suitable for most users. Any individual setting omitted from an existing file also uses its default value.

Advanced users can customize network access, listening ports, HTTPS, device pairing, and other server behavior. The provided `config.example.toml` documents the available settings and their default values.

Configuration changes take effect when you **restart Reality Later**.

### 2.2. Where to find the configuration file

The configuration file is named `config.toml`. Its location depends on how you installed Reality Later:

| Installation method | Configuration file |
| --- | --- |
| Windows package | `%APPDATA%\Reality Later\config.toml` |
| Linux Flatpak | `~/.var/app/io.github.MiryanaNovak.RealityLater/config/reality-later/config.toml` |
| Windows, macOS, or Linux from source | `config.toml` next to `main.py` in the source directory |
| Linux systemd service (as described above) | `/opt/reality-later/config.toml` |

If Reality Later is already running, the easiest way to access its configuration file is to open the **This computer** page from a browser on the computer running the application, then click **Open the settings file**. This will open the configuration file, creating it in the correct location if it doesn't already exist.

### 2.3. Customizing the configuration

An example configuration file is provided in `config.example.toml`. You can change only the settings you need; you do not have to keep every setting from the example. For instance, to allow Tailscale clients in addition to ordinary private networks, `config.toml` can contain just:

```toml
[server]
allowed_networks = ["private", "100.64.0.0/10"]
```

The example file contains explanations for the other settings, including `port`, `address`, `pairing`, `https`, and `local_port`. Be careful when changing network access or disabling device pairing: Reality Later is not intended to be directly exposed to the public internet.

After editing `config.toml`, quit Reality Later and start it again (or restart the systemd service).

## 3. First launch and connection

After installation, you can launch Reality Later without creating a configuration file. It should open your web browser and display the **This computer** page, allowing you to use the application from the computer it runs on.

By default, Reality Later provides two ways to connect: an HTTP connection accessible only from the computer running the application, and an HTTPS connection accessible from that computer and other devices on allowed local networks.

| Address | Purpose |
| --- | --- |
| `http://localhost:8091` | Local **This computer** page, accessible from the machine running Reality Later |
| `https://<server-LAN-IP>:8090` | Access from another device on an allowed local network |

### Connecting from another device

To connect from another device on your local network (such as a phone, a tablet, or another computer), you must first pair that device with Reality Later.

If the device has a camera, the easiest way to pair it is with a QR code. On the **This computer** page, click **Pair a device**, select the correct network from the dropdown menu, and scan the QR code with the device. It will be paired automatically.

If the device does not have a camera, open a web browser on it and enter the address displayed on the **This computer** page. The device will request pairing and display a code. On the computer running Reality Later, verify the code and approve the pairing request.

For HTTPS connections, Reality Later creates a **self-signed certificate**. The first time you connect from another device, your browser will warn that the certificate is not trusted. Before proceeding, compare its SHA-256 fingerprint with the one displayed on the **This computer** page. Once you accept the certificate, your browser may remember the exception so you will not have to repeat this process on subsequent visits.

### Managing pairing on a headless server

If Reality Later is running on a server without a graphical interface, you can approve device pairing using the command line.

If the device has a camera, the easiest way to pair it is to generate a QR code. On the server, run:

```bash
sudo -u reality-later -H sh -c 'cd /opt/reality-later && .venv/bin/python main.py pair'
```

Then scan the QR code with the device.

If the device does not have a camera, open a web browser on it and enter the server's address. The device will request pairing and display a code. On the server, run:

```bash
sudo -u reality-later -H sh -c 'cd /opt/reality-later && .venv/bin/python main.py approve CODE'
```

Replace `CODE` with the code displayed on the device.

### Access Reality Later remotely with Tailscale

For access away from home, it is recommended to use a private network such as [Tailscale](https://tailscale.com/) rather than forwarding port 8090 on your router.

Install Tailscale on the server and your phone, then authenticate both devices into the same tailnet.

**Allow Tailscale clients in Reality Later.** Its default `allowed_networks = ["private"]` does not include Tailscale's `100.64.0.0/10` address range. Without this change, a Tailscale connection can return **HTTP 403 Forbidden**, even though Tailscale itself is connected.

Create or open `config.toml` at the location described in [Configuration](#2-configuration) and, under `[server]`, set:

```toml
[server]
allowed_networks = ["private", "100.64.0.0/10"]
```

This is an excerpt, **not** a replacement for the entire configuration file. If `[server]` or `allowed_networks` already exists, edit the existing entry rather than adding a duplicate. Keeping `"private"` preserves access from ordinary private LAN addresses; adding `"100.64.0.0/10"` allows Tailscale-assigned addresses. Device pairing is still required for remote browsers.

With Reality Later running and its default local HTTP listener on port 8091, enable Tailscale Serve on the server:

```bash
tailscale serve --bg http://127.0.0.1:8091
tailscale serve status
```

Tailscale Serve exposes a private HTTPS `*.ts.net` address to devices authorized on your tailnet. Remote browsers must still follow Reality Later's pairing flow. The `--bg` configuration is retained across restarts by Tailscale.

Use **Tailscale Serve**, not **Tailscale Funnel**: Funnel exposes the service to everybody on the internet, which is precisely what you don't want.

## 4. Updating Reality Later

Reality Later does not update automatically. It displays a notification when a new version is available, but you must install the update yourself. The procedure depends on how the application was installed.

### 4.1. Update a packaged installation

Check the [GitHub Releases page](https://github.com/MiryanaNovak/reality-later/releases) and download the newer package. Stop Reality Later before updating.

For **Windows**, follow the instructions provided with the new release. If it is a standalone executable, replace the old executable with the new one. If it is an installer, run the installer.

For **Linux Flatpak**, install the new `.flatpak` bundle in the same way as the original, using the same installation scope (`--user` or system-wide).

### 4.2. Update a source installation

For Windows, macOS, or Linux **source installations**, stop Reality Later, open a terminal in your checkout, then run:

```bash
git pull --ff-only
```

Once the source has been updated, reinstall Reality Later and its dependencies into your existing virtual environment:

**Windows (PowerShell):**

```powershell
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe main.py
```

**macOS / Linux:**

```bash
.venv/bin/python -m pip install .
.venv/bin/python main.py
```

## 5. Using Reality Later

### 5.1. Connections

A **Connection** tells Reality Later which AI model to use and how to communicate with it. Reality Later currently uses [OpenRouter](https://openrouter.ai/), a service that provides access to many AI models through a single API.

You can create as many Connections as you like, each using a different model or different settings. For example, you might create separate Connections for different roleplay models and switch between them whenever you want.

#### Creating a Connection

Before creating a Connection, you need an **OpenRouter API key**. You can create an account and obtain a key from [openrouter.ai/keys](https://openrouter.ai/keys).

To create a Connection:

1. Open **Connections** from the navigation menu and click **New**.
2. Give your Connection a name. This is only used to identify it within Reality Later.
3. Enter your **OpenRouter API key**.
4. Choose an AI model. Reality Later automatically retrieves the list of available models from OpenRouter. You can select one from the list or enter its model ID manually.
5. Click **Send test message** at the bottom of the page to check that everything works.

All changes are saved automatically. You can return to this page at any time to modify a Connection.

You don't need to change the other parameters to get started. Their default values should work for most models.

#### Choosing an AI model

OpenRouter provides both free and paid AI models. Free models are useful for experimenting, although they may have usage limits or limited availability. Paid models charge according to usage, with prices varying considerably between models.

For roleplay, different models can produce very different results in terms of writing style, creativity, consistency, and ability to follow instructions. It's worth experimenting to find one that suits your preferences.

You can browse OpenRouter's [free models](https://openrouter.ai/collections/free-models) and [roleplay models](https://openrouter.ai/collections/roleplay) for starting points. Model availability and pricing may change over time.

#### Selecting the Connection to use

Creating a Connection doesn't automatically make it the one used for conversations.

To select it, open **Settings** from the navigation menu, expand the **Chat** section, and choose your preferred Connection from the **Chat connection** dropdown menu.

The selected Connection will be used to generate character replies in your conversations. You can change it at any time.

Settings are saved automatically.
