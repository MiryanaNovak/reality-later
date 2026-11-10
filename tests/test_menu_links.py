"""The side menu's last lines (rpapp/ui/menu_links.py): the user manual, GitHub, Contact, and the version."""
from nicegui.testing import user_simulation

from rpapp import CONTACT_EMAIL, MANUAL_URL, PROJECT_URL, __version__
from rpapp.ui.app import build_root


async def test_links_in_the_menu(tmp_path):
    async with user_simulation(build_root(tmp_path)) as user:
        for page in ('/', '/settings', '/characters'):  # (the menu is on every page)
            await user.open(page)
            await user.should_see(marker='menu-links')
            for marker, url in (('link-manual', MANUAL_URL), ('link-github', PROJECT_URL)):
                [link] = user.find(marker=marker).elements
                assert link.props['href'] == url and link.props['target'] == '_blank'
            await user.should_see(f'Version {__version__}')
        assert MANUAL_URL == 'https://github.com/MiryanaNovak/reality-later/blob/main/UserManual.md'


async def test_contact(tmp_path):
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open('/')
        user.find(marker='link-contact').click()
        await user.should_see(marker='contact-dialog')
        await user.should_see(CONTACT_EMAIL)
        [write] = user.find(marker='contact-write').elements
        assert write.props['href'] == f'mailto:{CONTACT_EMAIL}?subject=Reality%20Later%20{__version__}'
        user.find(marker='contact-copy').click()  # (copied in the browser itself: theme.py's rpCopy)
        await user.should_see('Address copied')
        user.find(marker='contact-close').click()
        await user.should_not_see(marker='contact-dialog')
