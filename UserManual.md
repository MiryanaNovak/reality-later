# Reality Later — User Manual

*The real world can wait.*

Reality Later is a self-hosted AI roleplay application. It runs on your computer and is accessed through a web browser, including from another device such as your phone.

## Table of Contents

- [1. Installation](#1-installation)
  - [1.1. Requirements](#11-requirements)
  - [1.2. Install from a package (recommended)](#12-install-from-a-package-recommended)
    - [1.2.1. Windows](#121-windows)
    - [1.2.2. Linux (Flatpak)](#122-linux-flatpak)
    - [1.2.3. Docker (Linux only)](#123-docker-linux-only)
  - [1.3. Install from source (advanced)](#13-install-from-source-advanced)
    - [1.3.1. Windows](#131-windows)
    - [1.3.2. macOS](#132-macos)
    - [1.3.3. Linux](#133-linux)
    - [1.3.4. Linux systemd service](#134-linux-systemd-service)
  - [1.4. Install on a headless server](#14-install-on-a-headless-server)
    - [1.4.1. Docker (Linux only)](#141-docker-linux-only)
    - [1.4.2. Linux systemd service (Linux only)](#142-linux-systemd-service-linux-only)
- [2. Configuration](#2-configuration)
  - [2.1. Default settings](#21-default-settings)
  - [2.2. Where to find the configuration file](#22-where-to-find-the-configuration-file)
  - [2.3. Customizing the configuration](#23-customizing-the-configuration)
- [3. First launch and connection](#3-first-launch-and-connection)
- [4. Updating Reality Later](#4-updating-reality-later)
  - [4.1. Update a packaged installation](#41-update-a-packaged-installation)
  - [4.2. Update a source installation](#42-update-a-source-installation)
  - [4.3. Update a headless server installation](#43-update-a-headless-server-installation)
- [5. Using Reality Later](#5-using-reality-later)
  - [5.1. Connections](#51-connections)
  - [5.2. Prompts](#52-prompts)
  - [5.3. Characters](#53-characters)
  - [5.4. Personas](#54-personas)
  - [5.5. Conversations](#55-conversations)
- [6. Advanced Features](#6-advanced-features)
  - [6.1. Lore Books](#61-lore-books)
  - [6.2. Memory](#62-memory)
  - [6.3. Vector Search](#63-vector-search)
- [7. Character Sharing](#7-character-sharing)
  - [7.1. Finding and Downloading Characters](#71-finding-and-downloading-characters)
  - [7.2. Publishing Characters](#72-publishing-characters)

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

### 1.4. Install on a headless server

#### 1.4.1 Docker (Linux only)

Reality Later is available as a Docker image. Docker installation is supported on Linux only because Reality Later requires host networking for its networking features.

**Requirements:** Docker Engine and Docker Compose.

Create a directory for Reality Later, open a terminal in that directory, and download the Compose configuration:

```bash
curl -fL -o compose.yaml https://raw.githubusercontent.com/MiryanaNovak/reality-later/main/compose.yaml
```

Start Reality Later:

```bash
docker compose up -d
```

Docker automatically downloads the application image, creates the necessary persistent volumes, and starts Reality Later in the background.

No Git clone, Python installation, or local build is required.

Application data and certificates are stored in Docker volumes and are preserved when the container is stopped, updated, or recreated.

To view the application logs:

```bash
docker compose logs -f
```

##### Add a configuration file (optional)

A configuration file is **not required**. Reality Later uses its built-in defaults when `config.toml` does not exist. If you need to customize the service, create the file from the example and edit it:

From the directory containing `compose.yaml`, download the example configuration:

```bash
curl -fL -o config.toml https://raw.githubusercontent.com/MiryanaNovak/reality-later/main/config.example.toml
```

Edit `config.toml` with your preferred text editor.

Next, create a file named `compose.override.yaml` in the same directory with the following content:

```yaml
services:
  reality-later:
    volumes:
      - ./config.toml:/app/config.toml:ro,Z
```

Docker Compose automatically loads this override file alongside `compose.yaml`. It makes your configuration file available inside the container without modifying the original Compose configuration.

Apply the configuration by recreating the container:

```bash
docker compose up -d --force-recreate
```

After subsequent changes to `config.toml`, run the same command to restart Reality Later with the updated settings.

The service can read this file but cannot modify it. See [Configuration](#2-configuration) for details.

#### 1.4.2. Linux systemd service (Linux only)

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

##### Add a configuration file (optional)

A configuration file is **not required**. Reality Later uses its built-in defaults when `config.toml` does not exist. If you need to customize the service, create the file from the example and edit it:

```bash
sudo cp /opt/reality-later/config.example.toml /opt/reality-later/config.toml
sudo chown root:reality-later /opt/reality-later/config.toml
sudo chmod 640 /opt/reality-later/config.toml
sudoedit /opt/reality-later/config.toml
```

The service can read this file but cannot modify it. See [Configuration](#2-configuration) for details.

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
| Linux docker (as described above) | `config.toml` next to the `compose.yaml` file|
| Linux systemd service (as described above) | `/opt/reality-later/config.toml` |

If Reality Later is already running, the easiest way to access its configuration file is to open the **This computer** page from a browser on the computer running the application, then click **Open the settings file**. This will open the configuration file, creating it in the correct location if it doesn't already exist.

### 2.3. Customizing the configuration

An example configuration file is provided in `config.example.toml`. You can change only the settings you need; you do not have to keep every setting from the example. For instance, to allow Tailscale clients in addition to ordinary private networks, `config.toml` can contain just:

```toml
[server]
allowed_networks = ["private", "100.64.0.0/10"]
```

The example file contains explanations for the other settings, including `port`, `address`, `pairing`, `https`, and `local_port`. Be careful when changing network access or disabling device pairing: Reality Later is not intended to be directly exposed to the public internet.

After editing `config.toml`, quit Reality Later and start it again.

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

If the device has a camera, the easiest way to pair it is to generate a QR code on the server and scan it with the device.

**Linux systemd installation:**

```bash
sudo -u reality-later -H sh -c 'cd /opt/reality-later && .venv/bin/python main.py pair'
```

**Docker installation:**

From the directory containing `compose.yaml`, run:

```bash
docker compose exec reality-later python main.py pair
```

If the device does not have a camera, open a web browser on it and enter the server's address. The device will request pairing and display a code.

Approve that code on the server.

**Linux systemd installation:**

```bash
sudo -u reality-later -H sh -c 'cd /opt/reality-later && .venv/bin/python main.py approve CODE'
```

**Docker installation:**

```bash
docker compose exec reality-later python main.py approve CODE
```

Replace `CODE` with the code displayed on the device.

For Docker installations, the container must be running, and the commands must be executed from the directory containing `compose.yaml`.

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

### 4.3 Update a headless server installation

#### 4.3.1 Update a docker Installation

Open a terminal in the directory containing `compose.yaml`.

Download the latest application image:

```bash
docker compose pull
```

Recreate the container using the updated image:

```bash
docker compose up -d
```

Reality Later will restart with the new version. Existing application data, settings, and certificates are preserved in the Docker volumes.

If the Compose configuration itself has changed, download the updated file before running the commands above:

```bash
curl -fL -o compose.yaml https://raw.githubusercontent.com/MiryanaNovak/reality-later/main/compose.yaml
```

#### 4.3.2 Update a systemd service installation

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

### 5.2. Prompts

A **Prompt** is a set of instructions that tells the AI how to behave during roleplay. It defines general rules for the conversation, such as writing style, narrative perspective, character behavior, and what kind of content is allowed.

Unlike a character's description, which defines a particular character, a Prompt provides instructions that apply to every conversation using it.

Reality Later includes two default Prompts:

- **Default (SFW):** Intended for general roleplay, avoiding sexually explicit content and graphic violence.
- **Mature (NSFW):** Allows more mature and explicit themes when appropriate to the story.

These instructions guide the AI's behavior, but their effectiveness depends on the model being used. Some models may impose additional content restrictions regardless of the selected Prompt.

#### Creating and editing Prompts

Open **Prompts** from the navigation menu to view the available Prompts. Select an existing Prompt to edit it, or click **New** to create your own.

You can create as many Prompts as you like, each with different instructions, and switch between them whenever you want.

A Prompt contains instructions sent to the AI when generating replies. You can use `{{char}}` to refer to the character's name and `{{user}}` to refer to yours.

The other fields provide more precise control over how instructions are delivered, but you don't need to modify them to get started.

All changes are saved automatically.

#### Selecting a Prompt

Open **Settings** from the navigation menu, expand the **Chat** section, and select the Prompt you want to use from the **Prompt** dropdown menu.

The selected Prompt will be used for conversations, and you can change it at any time.

Settings are saved automatically.

### 5.3. Characters

A **Character** defines who or what the AI portrays during a conversation. It can represent a person, a fictional character, or even an entire world with multiple characters.

Each Character contains information about its personality, background, and the scenario in which the roleplay takes place. This information helps the AI understand the setting and generate appropriate replies.

You can create as many Characters as you like and have multiple independent conversations with the same Character.

#### Creating and importing Characters

Open **Characters** from the navigation menu to view your collection.

- Click **New** to create a Character from scratch.
- Click **Import** to import an existing character card.
- Select a Character to view or edit its information.

Changes are saved automatically.

#### Character information

A Character contains several fields, divided into two categories:

**Only for you:** Information used to organize your collection, such as the Character's title, picture, and description. This information is displayed in Reality Later but is not sent to the AI.

**Sent to the AI:** Information used to guide the roleplay, including:

- **Name:** The Character's name as known to the AI.
- **Scenario:** Describes the Character, their personality, background, the setting, and the situation in which the conversation takes place.
- **First messages:** The opening messages used when starting a new conversation. You can provide several alternatives and choose between them when creating a chat.
- **Example dialogues:** Optional examples showing how the Character speaks and behaves, helping the AI reproduce their style.
- **Lore books:** Additional background information made available to the AI when relevant to the conversation.

You can use `{{char}}` to refer to the Character's name and `{{user}}` to refer to your own name in fields sent to the AI.

You don't need to fill in every field. A name, scenario, and first message are enough to get started.

### 5.4. Personas

A **Persona** defines who you are in a conversation, just as a Character defines who the AI portrays. It tells the AI how to address you and what it should know about your role in the story.

A Persona can be as simple or as detailed as you like. You can play as yourself, a fictional character, or someone entirely different. You can also create multiple Personas and use different ones for different conversations.

#### Creating a Persona

Open **Personas** from the navigation menu and click **New** to create a Persona. Select an existing Persona to edit it.

A Persona contains the following information:

- **Title:** The name used to identify the Persona in Reality Later. This is not sent to the AI.
- **Picture:** An optional image used to represent your Persona in the application. It is not sent to the AI.
- **Name:** The name the AI uses to refer to you.
- **Gender and pronouns:** Help the AI refer to your Persona appropriately.
- **Description:** Describes who you are in the story. You can include your appearance, personality, background, abilities, or anything else the AI should know.
- **Lore books:** Optional additional information that the AI can access when relevant to the conversation.

You can use `{{user}}` to refer to your Persona's name and `{{char}}` to refer to the Character's name in fields sent to the AI.

You don't need to fill in every field. A simple name and a short description are enough to get started.

All changes are saved automatically.

### 5.5. Conversations

**Conversations** are where you interact with AI Characters. Each conversation is a separate roleplay between a Character, portrayed by the AI, and a Persona, portrayed by you.

You can have multiple independent conversations with the same Character, each with its own story and history.

#### Starting a Conversation

To start a new conversation:

1. Open **Characters** from the navigation menu.
2. Find the Character you want to interact with and click **New chat**.
3. Select the **Persona** you want to play as.

The conversation will open with the Character's first message, and you can begin interacting with them.

If the Character has several first messages, you can choose which one to start with.

#### Managing Conversations

Open **Conversations** from the navigation menu to see your existing conversations.

Conversations are grouped by Character, making it easy to find all the stories you've started with a particular Character. Select a conversation to return to it and continue where you left off.

You can also rename conversations to make them easier to identify, especially when you have several conversations with the same Character.

**Conversation titles are only used for organization.** They are not sent to the AI and do not influence the story.

## 6. Advanced Features

Reality Later provides several optional features to help the AI remember information, maintain consistency, and manage long conversations. You don't need to configure any of them to start roleplaying.

### 6.1. Lore Books

A **Lore Book** is a collection of information that the AI can use during a conversation. It can describe characters, locations, events, fictional worlds, or anything else relevant to the story.

Unlike information included directly in a Character's scenario, Lore Book entries are generally sent to the AI only when they are relevant. Each entry can have keywords that trigger its inclusion when those keywords appear in the conversation. Entries can also be configured to remain permanently active.

This allows you to provide detailed background information without filling every AI request with information that isn't currently needed.

#### Using Lore Books

Open **Lore books** from the navigation menu to create and manage your Lore Books.

Lore Books can be attached to Characters or Personas. During a conversation, Reality Later uses the Lore Books associated with both.

Character cards can also contain embedded Lore Books, so imported Characters may already have their own background information attached.

You can create your own Lore Books, modify existing ones, and attach multiple Lore Books to the same Character or Persona.

You don't need to configure Lore Books to use Reality Later. They're particularly useful for elaborate characters, large fictional settings, and stories involving many people or places.

### 6.2. Memory

**Memory** allows Reality Later to remember important information from earlier parts of a conversation, even after those messages no longer fit within the AI model's context.

As conversations grow longer, older messages eventually have to be removed from the context to make room for new ones. When Memory is enabled, Reality Later automatically uses an AI model to summarize important information from those messages into memory entries.

Memories work similarly to Lore Book entries. Each memory contains information and a set of keywords that determine when it should be included in the AI's context. Memories can also be configured to remain permanently active.

Unlike Lore Books, which you create manually, **Memories are generated automatically from your conversations**. Each conversation has its own independent memories, allowing the AI to retain important details about events, relationships, and developments in the story.

#### Enabling Memory

Open **Settings** from the navigation menu and expand the **Memory** section.

Enable **Keep a memory of each chat** to activate automatic memory generation.

Memory generation uses an AI Connection, which you can select from the **Memory connection** dropdown menu. You can use the same Connection as your conversations or choose a different one, such as a cheaper or faster model dedicated to generating memories.

Memory generation also uses its own instructions, which you can customize in the **Memory instructions** field. Default instructions are provided, so you don't need to modify them to get started.

#### Managing Memories

As older messages leave the context, Reality Later automatically generates or updates memories to preserve important information.

When new memories are generated, a notification appears in the conversation. You can review the proposed changes before they are applied. The changes take effect when you send your next message.

You can also generate memories manually from a conversation's menu.

Memories can be reviewed, edited, or deleted at any time. This is useful for correcting information the AI misunderstood, removing outdated details, or ensuring important events are remembered accurately.

### 6.3. Vector Search

**Vector Search** allows Reality Later to retrieve relevant Lore Book and Memory entries based on their meaning, rather than relying solely on keywords.

For example, a Lore Book entry about a village blacksmith might be retrieved when the conversation mentions *"the person who makes swords"*, even if none of the entry's keywords appear in the conversation.

Vector Search complements traditional keyword matching rather than replacing it. Both methods remain active and work together to identify relevant information.

#### Enabling Vector Search

Open **Settings** from the navigation menu and expand the **Vector search** section.

You can enable Vector Search independently for **Memories** and **Lore Books**, or for both.

Vector Search requires an embedding model, which runs locally on your computer. Reality Later provides several models to choose from. Select one from the **Model** dropdown menu and click the download button to install it.

Once downloaded, the model runs locally to analyze the meaning of Lore Book and Memory entries and compare them with the conversation. Vector Search does not require an external service or an additional API key.

If you use Reality Later in a language other than English, choose a **multilingual model**. English-only models may not correctly understand conversations in other languages.

#### How Vector Search works

When generating a reply, Reality Later searches for relevant Lore Book and Memory entries using both keyword matching and, when enabled, semantic similarity.

Entries can therefore be included because their keywords appear in the conversation, because their meaning is sufficiently similar to what is being discussed, or both.

You can adjust how closely entries must match and how many entries can be retrieved through Vector Search in the settings. The default values are a good starting point.

## 7. Character Sharing

Reality Later allows you to create your own Characters, import character cards from other platforms, and discover Characters shared by other users.

Character cards using the V1, V2, V3, and CharX formats can be imported directly through **Characters → Import**.

Reality Later also provides its own **decentralized character-sharing system**, designed so that no single platform or authority controls the entire collection of shared Characters.

Instead of storing all Characters on a central server, Reality Later uses a network of independent services to announce available cards and **BitTorrent** to transfer them directly between users.

### 7.1. Finding and Downloading Characters

Open **Shared cards** from the navigation menu to browse Characters published by other users.

Reality Later retrieves announcements from the sharing network. Each announcement contains basic information about a Character, including its name, a short description, and tags.

An announcement is not the Character itself. To use a Character, you must first **download its card**, which you can then find in your Characters collection.

#### How downloading works

Reality Later uses **BitTorrent** to download character cards directly from other users who have a copy.

This means that a Character's availability depends on other users sharing it. If nobody is currently sharing a particular card, it may not be possible to download it until someone who has a copy comes online.

When you download a card, you can also help make it available to other users. This is called **seeding**.

#### Helping Keep Characters Available

Because there is no central server storing every Character, the sharing system depends on its users to keep cards available.

In **Settings → Sharing cards**, the option **Share them in turn** controls whether Reality Later automatically seeds cards you have downloaded.

When enabled, Reality Later shares these cards with other users while the application is running. You don't need to do anything manually.

Seeding is entirely optional, but **the more users participate, the more reliable the sharing network becomes**. A card can remain available even when its original publisher is offline, provided someone else continues seeding it.

#### Filtering Shared Cards

In **Settings → Sharing cards**, you can choose whether to display cards marked as NSFW (Not Safe For Work) and specify tags you want to hide.

Content ratings are assigned by the publishers themselves. Reality Later does not independently verify them.

### 7.2. Publishing Characters

You can publish your own Characters to make them discoverable and downloadable by other Reality Later users.

#### Creating Your Publisher Identity

Before publishing your first Character, you need a **publisher identity**. This identity consists of a cryptographic key pair that identifies you as the publisher of your cards.

You can create your publisher identity in **Settings → Sharing cards**, or Reality Later will guide you through the process when you publish your first Character.

You can also choose a **publisher name**, which is displayed alongside your published cards. Publisher names are not unique: your cryptographic key is what distinguishes you from other publishers.

**Important: Back up your publisher key!**

Use **Back up my key** in **Settings → Sharing cards** and store the backup somewhere safe.

Your private key is what allows you to publish under your identity. If you lose it without a backup, it cannot be recovered, and you will no longer be able to publish under the same identity. You can create a new identity, but it will be different from the previous one.

Never share your private key with anyone. Your public publisher identifier, on the other hand, can be shared freely.

#### Publishing a Character

To publish a Character:

1. Open **Characters** from the navigation menu.
2. Select the Character you want to publish.
3. Open its menu and choose **Publish**.
4. Follow the publishing steps and provide the requested information.

You will be asked to provide:

- **Card name:** The name under which your Character will be listed.
- **Description:** A short description to help other users understand what the Character is about before downloading it.
- **Tags (optional):** Keywords that help users discover Characters they're interested in.
- **Content rating:** Specify whether your Character is **SFW** or **NSFW**.
- **Language:** The language in which the Character is written.

Choose the appropriate content rating so other users can filter cards according to their preferences.

Once you confirm publication, Reality Later announces your Character to the sharing network, making it discoverable by other users.

#### Keeping Your Published Characters Available

Publishing a Character makes its announcement available to other users, but the Character card itself must still be downloaded from someone who is sharing it.

**Reality Later automatically seeds your published cards while the application is running.**

When you close Reality Later, your computer stops seeding. However, if other users have downloaded your card and enabled seeding, they can continue distributing it even while you're offline.

This is what makes decentralized sharing possible: **once other users have copies of a Character, its availability no longer depends entirely on its original publisher.**

You can manage your published Characters from **Your publications** in the navigation menu.

#### Network Connectivity

BitTorrent works best when other users can establish incoming connections to your computer.

Reality Later can automatically ask your router to open the necessary port using UPnP or NAT-PMP. This option is available in **Settings → Sharing cards** and is enabled by default.

If automatic port forwarding isn't available, you can configure a port manually and forward it through your router.

You can still connect to other peers without an open incoming port, but some peers may be unable to connect to you, reducing your ability to share cards.
