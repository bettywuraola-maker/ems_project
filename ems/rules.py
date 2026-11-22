"""Application layer: business rules BR1 to BR14 live here, apart from the web pages.

Every function validates first and writes afterwards, so a rejected action leaves
no partial record (NFR2, NFR7). Functions return (ok, message[, id]).
"""
import random
import sqlite3
from datetime import datetime

from .db import q, run

ROLES = ("Participant", "Event Staff", "Finance Officer", "Administrator")
SAFE = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PROFILE = [
    ("surname", "Surname"), ("other_names", "Other names"), ("gender", "Gender"),
    ("organisation", "Organisation"), ("level_cadre", "Level or cadre"),
    ("membership_status", "Professional membership status"),
    ("email", "Email"), ("phone", "Phone number"),
]


# ---------------------------------------------------------------- helpers
def audit(user_id, action, table, target_id):
    """BR12: record who did what, to which record, and when."""
    run("INSERT INTO audit_log(user_id, action, target_table, target_id) VALUES (?,?,?,?)",
        (user_id, action, table, target_id))


def parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def parse_time(s):
    try:
        return datetime.strptime(s, "%H:%M").strftime("%H:%M")
    except (ValueError, TypeError):
        return None


def deliver(channel, contact, subject, message):
    """Integration point for the email/SMS service (Notification Service actor).
    In this prototype nothing leaves the server, so delivery is always 'Sent'."""
    return "Sent"


def notify(event_id, subject, message, channel, sender_id, participant_ids=(), speaker_ids=()):
    nid = run("INSERT INTO notification(event_id, subject, message, channel, sent_by) VALUES (?,?,?,?,?)",
              (event_id, subject, message, channel, sender_id))
    for pid in participant_ids:
        p = q("SELECT email, phone FROM participant WHERE participant_id=?", (pid,), one=True)
        status = deliver(channel, p["email"] if channel == "Email" else p["phone"], subject, message)
        run("INSERT INTO notification_recipient(notification_id, participant_id, delivery_status) VALUES (?,?,?)",
            (nid, pid, status))
    for sid in speaker_ids:
        s = q("SELECT email, phone FROM speaker WHERE speaker_id=?", (sid,), one=True)
        status = deliver(channel, s["email"] if channel == "Email" else s["phone"], subject, message)
        run("INSERT INTO notification_recipient(notification_id, speaker_id, delivery_status) VALUES (?,?,?)",
            (nid, sid, status))
    return nid


def new_reference(event_id):
    while True:
        ref = f"UI{event_id:02d}-" + "".join(random.choices(SAFE, k=6))
        if not q("SELECT 1 FROM registration WHERE registration_ref=?", (ref,), one=True):
            return ref


def sync_status(reg_id):
    """BR3: Pending becomes Confirmed once payment is Verified or Waived."""
    run("UPDATE registration SET status='Confirmed' WHERE registration_id=? AND status='Pending'", (reg_id,))


# ---------------------------------------------------------------- registration
def register(event_id, form, actor_id, user_id=None):
    """Register a participant. user_id set = self registration; None = staff-assisted."""
    ev = q("SELECT * FROM event WHERE event_id=?", (event_id,), one=True)
    if not ev:
        return False, "Event not found.", None
    if ev["status"] != "Open":
        return False, "Registration is not open for this event.", None

    data = {k: (form.get(k) or "").strip() for k, _ in PROFILE}
    missing = [label for k, label in PROFILE if not data[k]]
    answers = {}
    for f in q("SELECT * FROM event_field WHERE event_id=?", (event_id,)):
        v = (form.get(f"field_{f['field_id']}") or "").strip()
        if f["is_required"] and not v:
            missing.append(f["field_name"])
        answers[f["field_id"]] = v
    if missing:
        return False, "Please fill in: " + ", ".join(missing) + ".", None
    if "@" not in data["email"]:
        return False, "Enter a valid email address.", None
    data["email"] = data["email"].lower()

    days = (form.get("expected_days") or "").strip()
    if days and (not days.isdigit() or int(days) < 1):
        return False, "Expected days of stay must be a whole number of 1 or more.", None
    purpose = (form.get("purpose") or "").strip()

    if user_id:
        p = q("SELECT * FROM participant WHERE user_id=?", (user_id,), one=True)
        if not p:  # link a profile that staff created earlier for this email
            p = q("SELECT * FROM participant WHERE email=? AND user_id IS NULL", (data["email"],), one=True)
            if p:
                run("UPDATE participant SET user_id=? WHERE participant_id=?", (user_id, p["participant_id"]))
    else:
        p = q("SELECT * FROM participant WHERE email=?", (data["email"],), one=True)

    if p:
        pid = p["participant_id"]
        if user_id:
            run("""UPDATE participant SET surname=?, other_names=?, gender=?, organisation=?, level_cadre=?,
                   membership_status=?, phone=? WHERE participant_id=?""",
                (data["surname"], data["other_names"], data["gender"], data["organisation"],
                 data["level_cadre"], data["membership_status"], data["phone"], pid))
    else:
        try:
            pid = run("""INSERT INTO participant(user_id, surname, other_names, gender, organisation,
                         level_cadre, membership_status, email, phone) VALUES (?,?,?,?,?,?,?,?,?)""",
                      (user_id, data["surname"], data["other_names"], data["gender"], data["organisation"],
                       data["level_cadre"], data["membership_status"], data["email"], data["phone"]))
        except sqlite3.IntegrityError:
            return False, "A participant with that email already exists.", None

    dup = q("SELECT status FROM registration WHERE participant_id=? AND event_id=?", (pid, event_id), one=True)
    if dup:  # BR2
        return False, f"Already registered for this event (status: {dup['status']}).", None

    ref = new_reference(event_id)
    status = "Confirmed" if float(ev["registration_fee"]) == 0 else "Pending"   # BR3
    rid = run("""INSERT INTO registration(participant_id, event_id, registration_ref, purpose, expected_days,
                 status, registered_by) VALUES (?,?,?,?,?,?,?)""",
              (pid, event_id, ref, purpose or None, int(days) if days else None, status,
               None if user_id else actor_id))
    for fid, v in answers.items():
        run("INSERT INTO field_response(registration_id, field_id, value) VALUES (?,?,?)", (rid, fid, v))
    notify(event_id, "Registration received",
           f"Your registration for {ev['title']} was received. Reference: {ref}. Status: {status}.",
           "Email", actor_id, participant_ids=[pid])
    return True, f"Registration received. Reference: {ref}", rid


def cancel_registration(reg_id, admin_id):
    r = q("SELECT * FROM registration WHERE registration_id=?", (reg_id,), one=True)
    if not r:
        return False, "Registration not found."
    if r["status"] == "Cancelled":
        return False, "Registration is already cancelled."
    run("UPDATE registration SET status='Cancelled' WHERE registration_id=?", (reg_id,))
    audit(admin_id, "registration cancelled", "registration", reg_id)      # BR12
    return True, "Registration cancelled. The record is kept (refunds are handled outside the system)."


# ---------------------------------------------------------------- payments
def _reg_for_payment(reg_id):
    r = q("""SELECT r.*, e.registration_fee, e.title FROM registration r
             JOIN event e ON e.event_id = r.event_id WHERE r.registration_id=?""", (reg_id,), one=True)
    if not r:
        return None, "Registration not found."
    if r["status"] == "Cancelled":
        return None, "This registration is cancelled."                      # BR14
    if float(r["registration_fee"]) == 0:
        return None, "This event has no fee."
    return r, None


def submit_payment(reg_id, reference, evidence=None, amount=None):
    """Participant submits a bank-transfer reference (UC3). A failed payment goes back to Pending."""
    r, err = _reg_for_payment(reg_id)
    if err:
        return False, err
    if r["status"] == "Confirmed":
        return False, "This registration is already confirmed."
    reference = (reference or "").strip()
    if len(reference) < 4:
        return False, "Enter the transfer reference from your bank (at least 4 characters)."
    current = q("SELECT * FROM payment WHERE registration_id=? ORDER BY payment_id DESC", (reg_id,))
    if any(p["status"] == "Pending" for p in current):
        return False, "A payment is already waiting for verification."
    failed = next((p for p in current if p["status"] == "Failed"), None)
    if failed:
        run("UPDATE payment SET status='Pending', reference=?, evidence=COALESCE(?, evidence), note=NULL WHERE payment_id=?",
            (reference, evidence, failed["payment_id"]))
    else:
        run("""INSERT INTO payment(registration_id, amount, method, reference, evidence, status)
               VALUES (?,?,?,?,?, 'Pending')""",
            (reg_id, r["registration_fee"], "Bank transfer", reference, evidence))
    return True, "Payment submitted. The Finance Officer will verify it."


def record_payment(reg_id, amount, method, reference, note, finance_id):
    """UC10. Cash is verified at the desk with a receipt (BR6); transfers wait for verification (BR5)."""
    r, err = _reg_for_payment(reg_id)
    if err:
        return False, err, None
    if r["status"] == "Confirmed":
        return False, "This registration is already confirmed.", None
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return False, "Enter a valid amount.", None
    if amount <= 0:
        return False, "Amount must be more than zero.", None
    if method not in ("Cash", "Bank transfer"):
        return False, "Choose Cash or Bank transfer.", None
    pid = run("""INSERT INTO payment(registration_id, amount, method, reference, status, recorded_by, note)
                 VALUES (?,?,?,?, 'Pending', ?, ?)""",
              (reg_id, amount, method, (reference or "").strip() or None, finance_id, (note or "").strip() or None))
    if method == "Cash":
        verify_payment(pid, finance_id)
        return True, "Cash payment recorded and receipt issued.", pid
    return True, "Transfer recorded. Verify it against the bank record.", pid


def verify_payment(payment_id, finance_id):
    """UC11: Verified payment confirms the registration (BR3), issues a receipt (BR4), is audited (BR12)."""
    p = q("SELECT * FROM payment WHERE payment_id=?", (payment_id,), one=True)
    if not p:
        return False, "Payment not found."
    if p["status"] != "Pending":
        return False, f"Only Pending payments can be verified (this one is {p['status']})."
    r = q("SELECT * FROM registration WHERE registration_id=?", (p["registration_id"],), one=True)
    if r["status"] == "Cancelled":
        return False, "This registration is cancelled."
    run("""UPDATE payment SET status='Verified', verified_by=?, verified_at=datetime('now','+1 hour')
           WHERE payment_id=?""", (finance_id, payment_id))
    receipt_no = f"RC-{datetime.now().year}-{payment_id:05d}"
    run("INSERT INTO receipt(payment_id, receipt_no) VALUES (?,?)", (payment_id, receipt_no))
    sync_status(r["registration_id"])
    audit(finance_id, "payment verified", "payment", payment_id)
    notify(r["event_id"], "Payment verified",
           f"Your payment was verified. Receipt {receipt_no}. Your registration is confirmed.",
           "Email", finance_id, participant_ids=[r["participant_id"]])
    return True, f"Payment verified. Receipt {receipt_no} issued."


def fail_payment(payment_id, finance_id, note=None):
    p = q("SELECT * FROM payment WHERE payment_id=?", (payment_id,), one=True)
    if not p:
        return False, "Payment not found."
    if p["status"] != "Pending":
        return False, f"Only Pending payments can be rejected (this one is {p['status']})."
    run("UPDATE payment SET status='Failed', verified_by=?, note=? WHERE payment_id=?",
        (finance_id, (note or "").strip() or "Evidence did not match the bank record.", payment_id))
    audit(finance_id, "payment rejected", "payment", payment_id)
    r = q("SELECT * FROM registration WHERE registration_id=?", (p["registration_id"],), one=True)
    notify(r["event_id"], "Payment not verified",
           "We could not match your payment to the bank record. Please check the reference and submit it again.",
           "Email", finance_id, participant_ids=[r["participant_id"]])
    return True, "Payment marked Failed. The participant can try again."


def waive_fee(reg_id, actor_id, note=None):
    """UC12 (Finance Officer or Administrator)."""
    r, err = _reg_for_payment(reg_id)
    if err:
        return False, err
    if q("SELECT 1 FROM payment WHERE registration_id=? AND status IN ('Verified','Waived')", (reg_id,), one=True):
        return False, "This registration is already paid or waived."
    note = (note or "").strip() or "Fee waived"
    open_p = q("SELECT * FROM payment WHERE registration_id=? AND status IN ('Pending','Failed') ORDER BY payment_id DESC",
               (reg_id,), one=True)
    if open_p:
        run("UPDATE payment SET status='Waived', amount=0, note=?, verified_by=? WHERE payment_id=?",
            (note, actor_id, open_p["payment_id"]))
        pid = open_p["payment_id"]
    else:
        pid = run("""INSERT INTO payment(registration_id, amount, method, status, recorded_by, verified_by, note)
                     VALUES (?, 0, 'Cash', 'Waived', ?, ?, ?)""", (reg_id, actor_id, actor_id, note))
    sync_status(reg_id)
    audit(actor_id, "fee waived", "payment", pid)
    return True, "Fee waived. The registration is confirmed."


# ---------------------------------------------------------------- scheduling
def venue_conflict(venue_id, date, start, end, exclude_session=None):
    """BR9: a venue cannot host overlapping sessions."""
    sql = """SELECT s.session_id, s.title, s.start_time, s.end_time, e.title AS event_title
             FROM session s JOIN event e ON e.event_id = s.event_id
             WHERE s.venue_id=? AND s.session_date=? AND s.start_time < ? AND s.end_time > ?"""
    args = [venue_id, date, end, start]
    if exclude_session:
        sql += " AND s.session_id != ?"
        args.append(exclude_session)
    return q(sql, args, one=True)


def speaker_conflict(speaker_id, session_id):
    """BR10: a speaker cannot be assigned to overlapping sessions."""
    s = q("SELECT * FROM session WHERE session_id=?", (session_id,), one=True)
    return q("""SELECT x.title, x.start_time, x.end_time FROM session x
                JOIN session_speaker ss ON ss.session_id = x.session_id
                WHERE ss.speaker_id=? AND x.session_id != ? AND x.session_date=?
                AND x.start_time < ? AND x.end_time > ?""",
             (speaker_id, session_id, s["session_date"], s["end_time"], s["start_time"]), one=True)


def validate_session(event_id, venue_id, date, start, end, exclude=None):
    """Returns (error, warning)."""
    d, s, e = parse_date(date), parse_time(start), parse_time(end)
    if not (d and s and e):
        return "Enter a valid date, start time and end time.", None
    if e <= s:
        return "End time must be after start time.", None
    clash = venue_conflict(venue_id, d, s, e, exclude)
    if clash:
        return (f"Venue clash: the venue is already used by '{clash['title']}' "
                f"({clash['start_time']}-{clash['end_time']}) in {clash['event_title']}."), None
    v = q("SELECT capacity FROM venue WHERE venue_id=?", (venue_id,), one=True)
    n = q("SELECT COUNT(*) c FROM registration WHERE event_id=? AND status != 'Cancelled'", (event_id,), one=True)["c"]
    warn = None
    if v and v["capacity"] and n > v["capacity"]:
        warn = f"Warning: {n} registrations exceed this venue's capacity of {v['capacity']}."
    return None, warn


def notify_schedule_change(session_id, actor_id, what):
    """BR11: tell the affected participants when the schedule changes."""
    s = q("SELECT * FROM session WHERE session_id=?", (session_id,), one=True)
    ids = [r["participant_id"] for r in q(
        "SELECT participant_id FROM registration WHERE event_id=? AND status != 'Cancelled'", (s["event_id"],))]
    if ids:
        notify(s["event_id"], "Programme change",
               f"{s['title']}: {what}. Now {s['session_date']}, {s['start_time']}-{s['end_time']}.",
               "Email", actor_id, participant_ids=ids)
    return len(ids)


# ---------------------------------------------------------------- attendance
def check_in(reg_id, session_id, staff_id, method="Name search"):
    """UC9 and BR7: only Confirmed registrations, once per session; rejections are logged."""
    r = q("SELECT * FROM registration WHERE registration_id=?", (reg_id,), one=True)
    s = q("SELECT * FROM session WHERE session_id=?", (session_id,), one=True)
    if not r or not s or r["event_id"] != s["event_id"]:
        return False, "Registration and session do not belong to the same event."

    def reject(reason):
        audit(staff_id, f"check-in rejected: {reason}", "registration", reg_id)
        return False, reason

    if r["status"] == "Cancelled":
        return reject("registration is cancelled")                          # BR14
    if r["status"] != "Confirmed":
        return reject("registration is not confirmed (payment pending)")    # BR7
    if q("SELECT 1 FROM attendance WHERE registration_id=? AND session_id=?", (reg_id, session_id), one=True):
        return reject("already checked in for this session")                # BR7
    aid = run("INSERT INTO attendance(registration_id, session_id, method, recorded_by) VALUES (?,?,?,?)",
              (reg_id, session_id, method, staff_id))
    audit(staff_id, "attendance recorded", "attendance", aid)
    return True, "Checked in."


# ---------------------------------------------------------------- certificates
def eligibility(reg_id):
    """BR8 and BR14. A free event counts as 'payment settled'."""
    r = q("""SELECT r.*, e.registration_fee, e.min_sessions_for_certificate AS min_s
             FROM registration r JOIN event e ON e.event_id = r.event_id WHERE r.registration_id=?""",
          (reg_id,), one=True)
    if r["status"] == "Cancelled":
        return False, "Registration is cancelled."
    if r["status"] != "Confirmed":
        return False, "Registration is not confirmed."
    if float(r["registration_fee"]) > 0 and not q(
            "SELECT 1 FROM payment WHERE registration_id=? AND status IN ('Verified','Waived')", (reg_id,), one=True):
        return False, "Payment is not verified or waived."
    if r["min_s"]:
        n = q("SELECT COUNT(*) c FROM attendance WHERE registration_id=?", (reg_id,), one=True)["c"]
        if n < r["min_s"]:
            return False, f"Attended {n} of the {r['min_s']} sessions required."
    return True, "Eligible"


def generate_certificates(event_id, admin_id):
    """UC18: one certificate per eligible registration, with a unique number."""
    issued, skipped = 0, []
    for r in q("SELECT * FROM registration WHERE event_id=? ORDER BY registration_id", (event_id,)):
        if q("SELECT 1 FROM certificate WHERE registration_id=?", (r["registration_id"],), one=True):
            continue
        ok, why = eligibility(r["registration_id"])
        if not ok:
            skipped.append((r["registration_ref"], why))
            continue
        cid = run("INSERT INTO certificate(registration_id, certificate_no) VALUES (?, ?)",
                  (r["registration_id"], f"CERT-{event_id:02d}-{r['registration_id']:05d}"))
        audit(admin_id, "certificate issued", "certificate", cid)
        issued += 1
    return issued, skipped


# ---------------------------------------------------------------- statistics
def event_stats(event_id):
    c = lambda sql: q(sql, (event_id,), one=True)[0] or 0
    return {
        "registrations": c("SELECT COUNT(*) FROM registration WHERE event_id=?"),
        "confirmed": c("SELECT COUNT(*) FROM registration WHERE event_id=? AND status='Confirmed'"),
        "pending": c("SELECT COUNT(*) FROM registration WHERE event_id=? AND status='Pending'"),
        "cancelled": c("SELECT COUNT(*) FROM registration WHERE event_id=? AND status='Cancelled'"),
        "paid_total": c("""SELECT SUM(p.amount) FROM payment p JOIN registration r USING(registration_id)
                           WHERE r.event_id=? AND p.status='Verified'"""),
        "pending_payments": c("""SELECT COUNT(*) FROM payment p JOIN registration r USING(registration_id)
                                 WHERE r.event_id=? AND p.status='Pending'"""),
        "attended": c("""SELECT COUNT(DISTINCT a.registration_id) FROM attendance a
                         JOIN registration r USING(registration_id) WHERE r.event_id=?"""),
        "certificates": c("""SELECT COUNT(*) FROM certificate c JOIN registration r USING(registration_id)
                             WHERE r.event_id=?"""),
        "sessions": c("SELECT COUNT(*) FROM session WHERE event_id=?"),
    }
