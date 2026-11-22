"""WRDS closing-auction data module. Nothing connects or writes during import.

Stages: wrds_queries/pipeline (download) -> identity/labels/events/eligibility/splits (rules)
-> build/publish (versioned outputs) -> report/compare.
"""
from .compare import compare
from .pipeline import audit_data, process_data, update_data

__all__ = ['update_data', 'process_data', 'audit_data', 'compare']
