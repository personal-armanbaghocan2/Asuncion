"""Independent, in-memory pricing diagnostics. No application/model/database imports."""

__version__ = "0.1.0"


def verify_snapshot(snapshot, *, enabled=False):
    """Evaluate an explicitly supplied snapshot only when enabled is exactly True.

    This API never loads documents, models, configuration or database records.
    Results cannot approve, block or change an official procurement/NLP status.
    """
    if enabled is not True:
        return {"schema_version": __version__, "processing_state": "DISABLED",
                "diagnostic_only": True, "authoritative": False,
                "procurement_write": False, "suggested_status": None,
                "overall_compliance": None, "findings": []}
    from .engine import evaluate
    return evaluate(snapshot)
