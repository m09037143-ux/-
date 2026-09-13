"""Optional AI-generated executive summary (opt-in via a checkbox in the
UI). See client.py (the HTTP call) and summary.py (what gets sent and how
the response is parsed/rendered). Kept entirely separate from
analytics/engine.py, which stays a plain, offline, network-free library --
this package is the ONLY place in the app that makes an outbound network
call. See docs/REVERSE_ENGINEERING.md §16 for the full design rationale,
including the one thing that could NOT be verified in this environment
(the actual Yandex Cloud endpoint's response shape -- outbound access to
it was blocked by network policy everywhere this was developed and
tested; response parsing is defensive/best-effort and should be
confirmed against a real call before being trusted).
"""
