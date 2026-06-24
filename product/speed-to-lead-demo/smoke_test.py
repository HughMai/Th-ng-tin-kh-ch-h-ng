"""Live smoke test for the speed-to-lead engine.

Runs scripted conversations through the real model to confirm the journey
behaves: booking, emergency (instant escalation), callback, not-a-job, and the
per-tenant quoting policy. Costs a handful of Haiku calls.

Run:  python smoke_test.py
"""

import workflow as w
import tenants


def show(label, turn):
    print(f"\n=== {label} ===")
    print(f"  reply:        {turn.reply}")
    print(f"  stage:        {turn.stage}   triage: {turn.triage}")
    print(f"  notify_kind:  {turn.notification_kind}")
    if turn.electrician_notification:
        print(f"  -> OWNER:     {turn.electrician_notification}")
    if turn.booking:
        print(f"  booking:      {turn.booking}")
    print(f"  complete:     {turn.conversation_complete}")


def u(text):
    return {"role": "user", "content": text}


def a(text):
    return {"role": "assistant", "content": text}


# 1. Normal bookable job -> two turns (enquiry, then pick a window)
t1 = w.run_turn([u("just need an exhaust fan swapped in the bathroom, we've got the new one here. home in Bulli")])
show("1a BOOKABLE — enquiry", t1)
t1b = w.run_turn([
    u("just need an exhaust fan swapped in the bathroom, we've got the new one here. home in Bulli"),
    a(t1.reply),
    u("wednesday morning works"),
])
show("1b BOOKABLE — picks window (expect booked)", t1b)

# 2. Emergency -> expect instant escalation, even before name/suburb
t2 = w.run_turn([u("power's out in half the house and i can smell something burning near the meter box")])
show("2 EMERGENCY (expect emergency + instant owner alert)", t2)

# 3. Callback request -> expect notification_kind callback
t3 = w.run_turn([u("can someone just give me a call back? i'd rather talk to a person than text")])
show("3 CALLBACK (expect notify_kind=callback)", t3)

# 4. Not a job -> spam/sales -> expect polite close, NO owner notification
t4 = w.run_turn([u("Hi, I'm reaching out about your Google Business listing — we can get you ranked #1, are you the owner?")])
show("4 NOT A JOB (expect closed, no owner alert)", t4)

# 5. Quoting policy — same 'how much' question under never vs ranges
print("\n--- quoting policy check ---")
dave = tenants.load_tenant("dave")
w.SYSTEM = w.build_system(dict(dave, quoting_policy="never"))
qn = w.run_turn([u("how much to install 3 downlights in the kitchen, home in Corrimal?")])
show("5a quoting=NEVER (expect no price, defers to human)", qn)
w.SYSTEM = w.build_system(dict(dave, quoting_policy="ranges"))
qr = w.run_turn([u("how much to install 3 downlights in the kitchen, home in Corrimal?")])
show("5b quoting=RANGES (expect a ballpark range)", qr)

print("\nSmoke test complete.")
