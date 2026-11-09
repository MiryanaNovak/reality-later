# Reality Later

*The real world can wait.*

**A self-hosted AI roleplay app with decentralized character sharing — for when you need a break from reality.**

Create characters, build worlds, get lost in stories, and share what you've made. Discover characters published by other users without depending on a single platform to host the entire collection. It's not just a place to chat: it's a way for people who create characters and worlds to discover and share each other's work. Your chats and characters stay on your computer, and you decide how you want to use them.

No account. No subscription. No disappearing bots. No fighting with the interface just to have a conversation.

## Why?

I made Reality Later because I was tired of AI roleplay platforms.

Tired of having to create accounts. Tired of characters being removed without explanation. Tired of chats being censored. Tired of ads. Tired of having no control over prompts and generation parameters. Tired of waiting for features that would never be implemented. And tired of complicated interfaces where even simple things are difficult to figure out.

But I didn't just want another place to keep my own characters. I wanted a way for people to **share characters without putting the whole collection at the mercy of one platform**. That's why Reality Later's character-sharing system is decentralized: announcements are distributed through independent relays, and the cards themselves are shared between users.

## Features

- **Decentralized character sharing.** Discover and publish character cards through a network of Nostr relays, then download them directly from other users with BitTorrent. There is no single central catalog or card-hosting server that controls the entire network, no central authority that decide what is allowed and what isn't, no erratic moderation that'll delete your favorite character overnight.

- **Import existing characters.** Bring in character cards in V1, V2, V3, and CharX formats, including cards with embedded lore books.

- **Control over the AI.** Choose your models, and customize prompts and generation parameters to shape how your stories unfold.

- **Automatic memories.** As older messages leave the AI's context, Reality Later can turn important details into memories. Review, edit, or delete them whenever you like.

- **Vector search.** Optionally use a locally run embedding model boost the efficiency of lore book entries and memories.

- **Simple interface.** Everything should be easy to find and understand. No digging through endless menus to figure out how something works.

- **Secure by default.** HTTPS is enabled for remote devices, pairing is required for new remote browsers, and imported content is treated as untrusted data.
  
- **Couch compatible.** Run Reality Later on your computer and access it in a browser, including from your phone or another device.

## Getting started

Download an available Windows build or Linux Flatpak bundle from [GitHub Releases](https://github.com/MiryanaNovak/reality-later/releases). You can also install from source on Windows, macOS, or Linux using a supported Python version (3.10–3.13; 3.13 recommended).

For step-by-step installation, configuration, and usage instructions, see the [User Manual](UserManual.md).

## AI providers

Reality Later currently supports **[OpenRouter](https://openrouter.ai/)**. You'll need your own OpenRouter API key to generate AI replies; using Reality Later itself doesn't require a platform account. Model availability, pricing, content policies, and usage limits depend on OpenRouter and its model providers.

Support for additional providers may come later.

## Security

Reality Later runs on your own computer and supports HTTPS out of the box. It generates a self-signed certificate by default, so remote browsers will warn that it isn't trusted until you verify its fingerprint and accept an exception.

Remote devices must be paired before they can access the app by default. External content, including imported character cards and lore books, is treated as untrusted; inputs are validated and sanitized to reduce the risks of malformed or malicious data.

**Do not expose Reality Later's web interface directly to the public internet.** For access away from home, use a private networking solution such as [Tailscale](https://tailscale.com/). The decentralized card-sharing feature is separate from remote access to the web interface.

## Built with

- Python
- [NiceGUI](https://nicegui.io/)
- Nostr and BitTorrent for decentralized card sharing
- A healthy dose of frustration

## Contributing

Bug reports and suggestions are welcome. I do not accept contributions.
