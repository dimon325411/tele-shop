class IntegrityError(Exception):
    """A uniqueness/foreign-key/check constraint was violated (both storage backends raise this)."""
