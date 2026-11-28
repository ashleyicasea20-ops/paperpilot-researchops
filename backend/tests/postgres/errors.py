"""Credential-free errors, importable before settings or pytest."""
class PgSafetyError(RuntimeError):
    """Safety refusal; never include credentials, DSNs or raw driver errors."""
