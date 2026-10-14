"""Translations (gettext). User-visible strings are wrapped in _() so translations can be
added later as /usr/share/locale/<lang>/LC_MESSAGES/opendisplay-x11.mo; until then the
English text is used as is."""
import gettext

DOMAIN = "opendisplay-x11"
_ = gettext.translation(DOMAIN, fallback=True).gettext
ngettext = gettext.translation(DOMAIN, fallback=True).ngettext
