"""opendisplay-x11: an X11 sender for the OpenDisplay protocol."""
try:
    from importlib.metadata import version as _version
    __version__ = _version("odx11")
except Exception:                       # running from a source checkout
    __version__ = "dev"
