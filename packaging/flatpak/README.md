# The Flatpak

`io.github.MiryanaNovak.RealityLater.yml` builds Reality Later as a Flatpak: one `.flatpak` file
that installs on any Linux with Flatpak. It's for the GitHub releases, not Flathub.

## On GitHub

`.github/workflows/flatpak.yml` builds it on GitHub's machines, for Intel/AMD and ARM computers:

- push a version tag (`git tag v0.2.0 && git push --tags`), once `__version__` in
  `rpapp/__init__.py` says that version (`'0.2.0'`: the workflow refuses a tag that isn't it):
  `reality-later-x86_64.flatpak` and `reality-later-aarch64.flatpak` are attached to that version's
  release, and the apps already installed say that it's out (`rpapp/updates.py`);
- or run it by hand (Actions › Flatpak › Run workflow): the files are in the run's artifacts.

Before handing them out, it checks them there: the tests, on Linux; then each `.flatpak` is
installed on a fresh Ubuntu, as anyone would (the base system downloading from Flathub), and
`packaging/smoke_test.py --flatpak io.github.MiryanaNovak.RealityLater` starts it and checks that its
pages, files and HTTPS answer, that vector search's libraries load, that card sharing works (its
`check-sharing` command publishes a card and downloads it by BitTorrent, on the machine only), and that
`quit` stops it. The ARM one too, on GitHub's ARM machines.

That test is for a computer where the app has no files yet: on yours, with its folder in
`~/.var/app/`, it stops rather than use your data (and quit the app if it runs).

## On your computer

Once, the tools and the base system (it comes from Flathub, even though the app doesn't):

    sudo apt install flatpak flatpak-builder       # or your distribution's equivalent
    flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
    flatpak install --user flathub org.freedesktop.Platform//25.08 org.freedesktop.Sdk//25.08

From the project's folder, build it and install it for yourself, then start it (or from your app menu):

    flatpak-builder --user --install --force-clean build packaging/flatpak/io.github.MiryanaNovak.RealityLater.yml
    flatpak run io.github.MiryanaNovak.RealityLater

The `.flatpak` file, to hand out:

    flatpak-builder --repo=repo --force-clean build packaging/flatpak/io.github.MiryanaNovak.RealityLater.yml
    flatpak build-bundle repo reality-later.flatpak io.github.MiryanaNovak.RealityLater \
        --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo

(`build/`, `repo/` and `.flatpak-builder/` are the build's own folders: keep them out of git.)

## For the people who install it

    flatpak install --user reality-later.flatpak     # the base system downloads by itself
    flatpak run io.github.MiryanaNovak.RealityLater  # or Reality Later, in the app menu
    flatpak uninstall --user io.github.MiryanaNovak.RealityLater

Its settings file and data are in `~/.var/app/io.github.MiryanaNovak.RealityLater/` (its
"This computer" page shows where, and opens them). It stops with Quit, in its tray icon's menu or on
that page. A new version installs over the old one, data kept: `flatpak install --user` the new file.

What it may do: use the network (its server, the AI services it calls, and card sharing: the relays,
and BitTorrent, whose port other computers connect to) and show its tray icon. Nothing else: no access
to your files, and no window of its own (it opens your browser).

To check that card sharing works in it, on your computer (nothing of yours is used, and nothing leaves
the computer): `flatpak run io.github.MiryanaNovak.RealityLater check-sharing`.
