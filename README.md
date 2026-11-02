# Reality Later

*The real world can wait.*

A self-hosted AI roleplay app for when you need a break from reality.

Create characters, build worlds, and get lost in stories. No platform accounts, no disappearing bots, and no fighting with an interface just to have a conversation.

## Why?

I made Reality Later because I was tired of AI roleplay platforms.

Tired of having to create accounts. Tired of characters disappearing explanation. Tired of chats being censored. Tired of having no control over prompts and generation parameters. And tired of complicated interfaces where even simple things are difficult to figure out.

I wanted something that was mine, that worked the way I wanted, and that didn't get in the way. And something that'd let me chat from my phone while lying on the couch. Because, frankly, there's no better place to escape reality than the couch.

## Features

- **Your characters, your data.** Characters, personas, conversations, and lore books stay on your computer, not hosted on someone else's platform. No need to create an account. No need to verify your age. No moderator will come to erase your favorite characters.

- **Control over the AI.** Customize prompts and generation parameters to influence how your stories unfold.

- **Automatic memories.** As older messages leave the conversation context, memories are generated automatically.

- **Simple interface.** Everything should be easy to find and understand. No digging through endless menus to figure out how something works.

- **Couch compatible.** Run it on your computer and connect from your phone or another device.

- **Hardened.** HTTPS is enabled by default, no reverse proxy needed. Only paired devices can connect. All inputs are strictly validated so you can import character cards from untrusted sources.

## Installation

### Requirements

- Windows, Mac or Linux
- An OpenRouter account and API key

### Installing from sources

Clone the repository:

```bash
git clone https://github.com/MiryanaNovak/reality-later.git
cd reality-later
```

Create and activate a Python virtual environment, then install the application:

```bash
python -m venv .venv
python -m pip install .
```

Activate the virtual environment before installing (and replace the startup command below with the actual application entry point):

```bash
python main.py
```

Your web browser should open automatically. Otherwise, open the address displayed in the terminal.

## AI providers

Reality Later currently supports **[OpenRouter](https://openrouter.ai/)**.

You'll need your own API key. Model availability, pricing, and usage limits depend on OpenRouter and the model providers.

Support for additional providers may come later.

## Security

Reality Later runs on your own computer and supports HTTPS out of the box.

Self-signed certificates are generated automatically and will require a one-time trust or browser exception on devices connecting to the application.

External content, including imported character cards and lore books, is treated as untrusted. Inputs are validated and sanitized to limit the risks of malformed or malicious data.

HTTPS and input validation alone do not make an application safe to expose directly to the public internet. If you want to access Reality Later from outside your home network, consider using a secure networking solution such as Tailscale instead of exposing the application directly.

## Built with

- Python
- [NiceGUI](https://nicegui.io/)
- A healthy dose of frustration 

## Contributing

Bug reports and suggestions are welcome.
