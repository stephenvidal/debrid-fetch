"""Live view of settings: `config.X` always returns the current value (UI override > env > default)."""
from settings import store, BY_KEY


def __getattr__(name):
    if name in BY_KEY:
        return store.get(name)
    raise AttributeError(name)
