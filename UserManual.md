# Reality Later — User Manual

*The real world can wait.*

Reality Later is a self-hosted AI roleplay application. It runs on your computer and is accessed through a web browser, including from another device such as your phone.

## Table of Contents

- [Requirements](#requirements)
- [1. Install from source — Windows](#1-install-from-source--windows)
- [2. Install from source — macOS](#2-install-from-source--macos)
- [3. Install from source — Linux](#3-install-from-source--linux)
- [4. Install from source — Linux systemd service](#4-install-from-source--linux-systemd-service)
  - [4.1. Install prerequisites](#41-install-prerequisites)
  - [4.2. Create the service account](#42-create-the-service-account)
  - [4.3. Clone the source directly into `/opt`](#43-clone-the-source-directly-into-opt)
  - [4.4. Create the virtual environment and install dependencies](#44-create-the-virtual-environment-and-install-dependencies)
  - [4.5. Configure filesystem permissions](#45-configure-filesystem-permissions)
  - [4.6. Create the systemd unit](#46-create-the-systemd-unit)
  - [4.7. Enable and start the service](#47-enable-and-start-the-service)
  - [4.8. Manage the service](#48-manage-the-service)
  - [4.9. Update a systemd installation](#49-update-a-systemd-installation)
- [5. First launch and connecting from another device](#5-first-launch-and-connecting-from-another-device)
- [6. Updating a normal source installation](#6-updating-a-normal-source-installation)
- [7. Troubleshooting](#7-troubleshooting)
- [8. Data, configuration, and security](#8-data-configuration-and-security)

## Requirements

- **Python 3.10 or newer** and **Git**.
- An **[OpenRouter](https://openrouter.ai/)** account and API key. OpenRouter is currently the only supported AI provider.
- An internet connection for installation and OpenRouter requests.

Reality Later's dependencies are declared in `pyproject.toml`. You do **not** need a `requirements.txt` file. The commands below use a Python virtual environment so dependencies are not installed into your system Python.

## 1. Install from source — Windows

Install [Python](https://www.python.org/downloads/windows/) and [Git](https://git-scm.com/downloads/win), if you do not have them already. Make sure Python is at least version 3.10.

Open **PowerShell**, then run:

```powershell
# Download the source code
git clone https://github.com/MiryanaNovak/reality-later.git
cd reality-later

# Create a virtual environment
py -3 -m venv .venv

# Install the app and its dependencies
.\.venv\Scripts\python.exe -m pip install .

# Start Reality Later
.\.venv\Scripts\python.exe main.py
```

Reality Later normally opens its **This computer** page automatically. If it does not, visit **http://localhost:8091** in your browser (assuming the default ports).

To start it again later, open PowerShell in the `reality-later` directory and run:

```powershell
.\.venv\Scripts\python.exe main.py
```

Leave the process running while using the application. Press **Ctrl+C** to stop it.

> If `py` is not available, install the Python Launcher or use the appropriate `python` command for your Python 3.10+ installation.

## 2. Install from source — macOS

Install [Python 3.10+](https://www.python.org/downloads/macos/) and Git if needed. Git can be installed with Apple's Command Line Tools (`xcode-select --install`). Check that `python3 --version` reports a supported version.

Open **Terminal**, then run:

```bash
# Download the source code
git clone https://github.com/MiryanaNovak/reality-later.git
cd reality-later

# Create a virtual environment
python3 -m venv .venv

# Install the app and its dependencies
.venv/bin/python -m pip install .

# Start Reality Later
.venv/bin/python main.py
```

Reality Later normally opens its **This computer** page automatically. Otherwise, visit **http://localhost:8091** (with the default ports).

To launch it again:

```bash
cd reality-later  # if you are not already in this directory
.venv/bin/python main.py
```

Keep Terminal open while using the app; press **Ctrl+C** to stop it.

## 3. Install from source — Linux

Install Git, Python 3.10+, and virtual environment support. For **Ubuntu/Debian**:

```bash
sudo apt update
sudo apt install git python3 python3-venv
```

On another distribution, install the equivalent packages using its package manager. Verify your Python version with `python3 --version`.

Then run these commands as your **normal user**, without `sudo`:

```bash
# Download the source code
git clone https://github.com/MiryanaNovak/reality-later.git
cd reality-later

# Create a virtual environment
python3 -m venv .venv

# Install the app and its dependencies
.venv/bin/python -m pip install .

# Start Reality Later
.venv/bin/python main.py
```

Reality Later normally opens its **This computer** page in your browser. Otherwise, visit **http://localhost:8091** (with default settings).

To start it again:

```bash
cd reality-later  # if needed
.venv/bin/python main.py
```

Keep the terminal running; press **Ctrl+C** to stop the app.

> **Ubuntu/Debian and `externally-managed-environment`:** This error usually means you ran system `pip` rather than the virtual environment's Python. Use `.venv/bin/python -m pip install .` exactly as shown above; do not use `--break-system-packages`.

## 4. Install from source — Linux systemd service

This option is intended for a Linux machine that runs Reality Later as a background server, starting automatically at boot. It uses:

- `/opt/reality-later` for the Git checkout, application code, and virtual environment;
- a dedicated **`reality-later`** system account, without an interactive login;
- writable `data/` and `certificates/` directories, with the rest of the application read-only to the service;
- systemd sandboxing to restrict the application's access to the rest of the machine.

The instructions below target **Ubuntu/Debian with systemd**. They assume **a new installation** and clone the repository **directly into `/opt/reality-later`**. Do not run these steps over an existing installation without first backing up its data and configuration.

### 4.1. Install prerequisites

```bash
sudo apt update
sudo apt install git python3 python3-venv
```

Make sure `python3 --version` is 3.10 or newer.

### 4.2. Create the service account

```bash
sudo adduser --system --group \
    --home /var/lib/reality-later --no-create-home \
    reality-later
```

This creates a dedicated system user and group. Do **not** give it sudo access.

### 4.3. Clone the source directly into `/opt`

```bash
sudo git clone https://github.com/MiryanaNovak/reality-later.git /opt/reality-later
```

The checkout is managed by root so the service account will not be able to modify its own program files.

### 4.4. Create the virtual environment and install dependencies

```bash
sudo python3 -m venv /opt/reality-later/.venv
sudo /opt/reality-later/.venv/bin/python -m pip install /opt/reality-later
```

These commands use `sudo` **only to prepare the root-owned installation**. Reality Later itself will run as the unprivileged `reality-later` user.

### 4.5. Configure filesystem permissions

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

`config.toml` is **readable but not writable** by the application. It is intended to be changed manually by an administrator. With this layout, edit it using:

```bash
sudoedit /opt/reality-later/config.toml
```

All options have defaults; editing this file is not required for a first start. Keep machine-specific settings and credentials out of Git commits.

### 4.6. Create the systemd unit

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

### 4.7. Enable and start the service

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

### 4.8. Manage the service

```bash
sudo systemctl start reality-later      # Start
sudo systemctl stop reality-later       # Stop
sudo systemctl restart reality-later    # Restart
systemctl status reality-later         # Status
journalctl -u reality-later -f          # Live logs
```

To stop it from starting at boot:

```bash
sudo systemctl disable --now reality-later
```

### 4.9. Update a systemd installation

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

Back up `data/`, `certificates/`, and any customized `config.toml` before significant updates. If you edited the tracked `config.toml` locally, Git may require you to resolve changes before pulling. Do not force an update that overwrites your configuration.

## 5. First launch and connecting from another device

With the default configuration:

| Address | Purpose |
| --- | --- |
| `http://localhost:8091` | Local **This computer** page, accessible from the machine running Reality Later |
| `https://<server-LAN-IP>:8090` | Access from another device on an allowed local network |

Reality Later enables HTTPS by default. It creates a **self-signed certificate**, so your browser may warn that the certificate is not trusted. Before proceeding, compare its SHA-256 fingerprint with the fingerprint displayed by the application.

**Device pairing is enabled by default.** A phone or other remote browser must be approved from the server's **This computer / Devices** interface. For a headless Linux server, Reality Later also provides CLI pairing commands; for example, from `/opt/reality-later`:

```bash
sudo -u reality-later -H /opt/reality-later/.venv/bin/python main.py pair
sudo -u reality-later -H /opt/reality-later/.venv/bin/python main.py approve
```

Follow the instructions displayed by these commands.

Once connected, open **Connections** in Reality Later and configure your OpenRouter API key.

### Optional: access Reality Later remotely with Tailscale

For access away from home, use a private network such as [Tailscale](https://tailscale.com/) rather than forwarding port 8090 on your router.

Install Tailscale on the server and your phone, then authenticate both devices into the same tailnet.

**Allow Tailscale clients in Reality Later.** Its default `allowed_networks = ["private"]` does not include Tailscale's `100.64.0.0/10` address range. Without this change, a Tailscale connection can return **HTTP 403 Forbidden**, even though Tailscale itself is connected.

Open `config.toml` in the Reality Later installation directory and, under the existing `[server]` section, set:

```toml
[server]
allowed_networks = ["private", "100.64.0.0/10"]
```

This is an excerpt, **not** a replacement for the entire configuration file. If `[server]` or `allowed_networks` already exists, edit the existing entry rather than adding a duplicate. Keeping `"private"` preserves access from ordinary private LAN addresses; adding `"100.64.0.0/10"` allows Tailscale-assigned addresses. Device pairing is still required for remote browsers.

- **Normal source installation:** Edit `<your-checkout>/config.toml`, stop Reality Later with **Ctrl+C**, and launch it again.
- **systemd installation:** Run `sudoedit /opt/reality-later/config.toml`, save the change, then restart with `sudo systemctl restart reality-later`.

With Reality Later running and its default local HTTP listener on port 8091, enable Tailscale Serve on the server:

```bash
tailscale serve --bg http://127.0.0.1:8091
tailscale serve status
```

Tailscale Serve exposes a private HTTPS `*.ts.net` address to devices authorized on your tailnet. Remote browsers must still follow Reality Later's pairing flow. The `--bg` configuration is retained across restarts by Tailscale.

Use **Tailscale Serve**, not **Tailscale Funnel**: Funnel exposes the service to the public internet.

You can optionally configure Reality Later to listen only on `127.0.0.1` if you want Tailscale Serve to be the sole way to reach the server remotely, rather than allowing direct LAN connections. Refer to the `[server]` settings in `config.toml` and restart Reality Later after editing them.

## 6. Updating a normal source installation

For Windows, macOS, or Linux installations that are **not** managed by systemd, stop Reality Later, open a terminal in your checkout, then run:

```bash
git pull --ff-only
```

Reinstall from the updated source using your existing virtual environment:

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

A virtual environment is specific to its installation directory; don't move or copy `.venv` between computers. Recreate it instead.

## 7. Troubleshooting

- **error: externally-managed-environment** — You are probably using your distribution's system Python instead of the virtual environment's Python. Use the `.venv` executable shown in the installation instructions.

- **Permission denied when writing data or certificates (systemd)** — Recheck ownership of `/opt/reality-later/data` and `/opt/reality-later/certificates`, along with `ReadWritePaths` in the unit.

- **Port 8090 or 8091 is already in use** — Stop any manually running Reality Later instance before starting the service, or adjust its port configuration.

- **HTTP 403 Forbidden over Tailscale** — Reality Later's default `allowed_networks = ["private"]` does not include Tailscale's `100.64.0.0/10` addresses. In `config.toml` under `[server]`, change the existing setting to `allowed_networks = ["private", "100.64.0.0/10"]`, then restart Reality Later. Pairing may still be required.

- **A remote device cannot connect** — Check the listening address, `allowed_networks`, firewall rules, device pairing, and (if used) Tailscale connectivity. Do not expose the app directly to the public internet to solve this.

## 8. Data, configuration, and security

- **Application settings:** `config.toml` in the source directory. It is edited manually and read at startup; many options can be omitted to use defaults.
- **Persistent data:** `data/` by default, including conversations and generated memories. Set `RPAPP_DATA` to use a different location.
- **HTTPS certificates:** `certificates/` by default. Protect the private key and back it up if you want to preserve the certificate identity.
- **API credentials:** Treat the application's data and settings as sensitive; protect and back them up accordingly.
- **Network exposure:** HTTPS and input validation are not, by themselves, reasons to expose Reality Later directly to the public internet. Prefer private access through Tailscale or an equivalent solution.

For the systemd installation, the service account can read the application files but only writes to explicitly authorized persistent directories. This reduces the damage a compromised application can cause; it does not protect data or API keys that Reality Later itself must be allowed to read.

---

Source code: [github.com/MiryanaNovak/reality-later](https://github.com/MiryanaNovak/reality-later)
