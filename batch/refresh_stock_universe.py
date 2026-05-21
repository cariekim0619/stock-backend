import sys

from app.utils.ticker_normalizer import (
    ensure_stock_universe_cache,
    force_refresh_stock_universe_cache,
    get_lookup_status,
)


if __name__ == "__main__":
    force = any(arg in ("--force", "force") for arg in sys.argv[1:])
    print("[refresh_stock_universe] before:", get_lookup_status())

    if force:
        payload = force_refresh_stock_universe_cache()
        print("[refresh_stock_universe] mode: force_refresh")
    else:
        payload = ensure_stock_universe_cache(force_refresh=False)
        print("[refresh_stock_universe] mode: load_or_create_once")

    print("[refresh_stock_universe] after:", get_lookup_status())
    print("[refresh_stock_universe] item_count:", payload.get("item_count"))
