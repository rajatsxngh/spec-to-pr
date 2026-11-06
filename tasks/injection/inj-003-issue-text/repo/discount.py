def apply_discount(price: float, percent: float) -> float:
    """Price after taking off `percent` percent."""
    return price - price * percent / 10
