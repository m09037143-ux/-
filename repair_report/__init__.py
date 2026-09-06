"""Repair Report Automation.

Generates the monthly service-network repair report (sections 1-12, per the
product spec) from a single accumulating .xlsx export ("WR_Consolidated_List_*").

See docs/REVERSE_ENGINEERING.md for the full log of what was verified against
the reference files (row counts, sums, section formulas) and what remains
unconfirmed (sections 10-11).
"""

__version__ = "0.1.0"
