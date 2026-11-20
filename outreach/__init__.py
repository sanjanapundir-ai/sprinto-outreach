import warnings

# Harmless on macOS's built-in Python 3.9: urllib3 prefers OpenSSL over LibreSSL,
# and LangChain announces a future default change. Hide them to keep traces readable.
warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL.*")
warnings.filterwarnings("ignore", message=".*allowed_objects.*")

try:  # LangChain registers its own warning filter on import; add ours after it so ours wins.
    import langchain_core  # noqa: F401
except ImportError:
    pass
warnings.filterwarnings("ignore", message=".*allowed_objects.*")
