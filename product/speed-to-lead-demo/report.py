"""Owner ROI report — the proof that justifies the invoice.

A tradie can't *feel* an AI working in the background, so churn is inevitable
unless the value is made visible: "this month we caught 23 enquiries you'd have
missed and booked 9 jobs — about $3,150 of work." This module turns the data
already in `store` into that number, for the dashboard report page and (reused
verbatim) a future monthly Telegram/SMS push.

The dollar figure is deliberately conservative — booked jobs only, at the
tenant's average job value. Quotes and emergencies are counted but NOT priced
in: an honest, defensible number keeps trust; an inflated one loses it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import store

# Fallback when a tenant config has no `avg_job_value`. A conservative average
# residential trade job (call-out + an hour or two of work), in AUD.
DEFAULT_AVG_JOB_VALUE = 350


def build(tenant: dict, days: int = 30) -> dict:
    """Compute the ROI report for a tenant over the trailing `days`."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    c = store.report_counts(tenant["tenant_id"], since)
    avg = tenant.get("avg_job_value", DEFAULT_AVG_JOB_VALUE)
    return {
        "days": days,
        "leads": c["leads"],
        "missed_calls": c["missed_calls"],
        "booked": c["booked"],
        "emergencies": c["emergencies"],
        "quotes": c["quotes"],
        "avg_job_value": avg,
        "booked_value": c["booked"] * avg,
    }


def _n(count: int, singular: str, plural: str) -> str:
    """'1 job' / '2 jobs' — keep the customer-facing summary grammatical."""
    return f"{count} {singular if count == 1 else plural}"


def as_text(tenant: dict, r: dict) -> str:
    """Plain-text summary for a Telegram/SMS push. Same numbers as the dashboard
    page, in a few scannable lines an owner reads on their phone."""
    period = f"last {r['days']} days"
    lines = [
        f"📈 {tenant['business_name']} — {period}",
        f"≈ ${r['booked_value']:,} of work booked",
        "",
        f"• {_n(r['leads'], 'enquiry', 'enquiries')} caught",
        f"• {_n(r['missed_calls'], 'missed call', 'missed calls')} texted back",
        f"• {_n(r['booked'], 'job', 'jobs')} booked",
    ]
    if r["emergencies"]:
        lines.append(f"• {_n(r['emergencies'], 'emergency', 'emergencies')} escalated")
    if r["quotes"]:
        lines.append(f"• {_n(r['quotes'], 'quote', 'quotes')} sent")
    return "\n".join(lines)
