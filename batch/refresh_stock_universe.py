from app.utils.ticker_normalizer import force_refresh_stock_universe_cache, get_lookup_status


if __name__ == "__main__":
    print("[refresh_stock_universe] before:", get_lookup_status())
    payload = force_refresh_stock_universe_cache()
    print("[refresh_stock_universe] after:", get_lookup_status())
    print("[refresh_stock_universe] item_count:", payload.get("item_count"))
