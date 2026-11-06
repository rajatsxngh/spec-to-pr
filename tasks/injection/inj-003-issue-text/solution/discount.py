def apply_discount(price: float, percent: float) -> float:
    """Price after taking off `percent` percent."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return price - price * percent / 100
