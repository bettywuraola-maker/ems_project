"""Front-desk and finance pages: check-in, participant search, staff-assisted registration, payments."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import roles_required
from .. import rules
from ..db import q

bp = Blueprint("ops", __name__)

SEARCH_SQL = """
SELECT r.registration_id, r.registration_ref, r.status, r.event_id, r.registered_at,
       p.surname, p.other_names, p.organisation, p.email, p.phone, e.title, e.registration_fee,
       (SELECT status FROM payment x WHERE x.registration_id = r.registration_id
          ORDER BY x.payment_id DESC LIMIT 1) AS payment_status
FROM registration r JOIN participant p ON p.participant_id = r.participant_id
JOIN event e ON e.event_id = r.event_id
WHERE 1=1 """


def search_registrations(text, event_id=None, limit=50):
    sql, args = SEARCH_SQL, []
    if event_id:
        sql += " AND r.event_id=?"
        args.append(event_id)
    if text:
        like = f"%{text}%"
        sql += """ AND (r.registration_ref = ? COLLATE NOCASE OR p.surname LIKE ? OR p.other_names LIKE ?
                   OR (p.surname || ' ' || p.other_names) LIKE ? OR (p.other_names || ' ' || p.surname) LIKE ?
                   OR p.email LIKE ? OR p.organisation LIKE ?)"""
        args += [text, like, like, like, like, like, like]
    sql += " ORDER BY p.surname, p.other_names LIMIT ?"
    args.append(limit)
    return q(sql, args)


# ------------------------------------------------------------ check-in (Event Staff)
@bp.route("/staff/checkin")
@roles_required("Event Staff")
def checkin():
    events = q("SELECT * FROM event WHERE status IN ('Open','Closed') ORDER BY start_date")
    event_id = request.args.get("event", type=int) or (events[0]["event_id"] if events else None)
    sessions = q("SELECT * FROM session WHERE event_id=? ORDER BY session_date, start_time", (event_id,)) if event_id else []
    session_id = request.args.get("session", type=int)
    text = request.args.get("q", "").strip()
    results, count = [], None
    if session_id:
        count = q("SELECT COUNT(*) c FROM attendance WHERE session_id=?", (session_id,), one=True)["c"]
        if text:
            for r in search_registrations(text, event_id, 20):
                done = q("SELECT 1 FROM attendance WHERE registration_id=? AND session_id=?",
                         (r["registration_id"], session_id), one=True)
                results.append({"r": r, "done": bool(done)})
    return render_template("checkin.html", events=events, event_id=event_id, sessions=sessions,
                           session_id=session_id, text=text, results=results, count=count)


@bp.route("/staff/checkin", methods=["POST"])
@roles_required("Event Staff")
def checkin_do():
    reg_id, session_id = request.form.get("registration_id", type=int), request.form.get("session_id", type=int)
    text = request.form.get("q", "")
    reg = q("SELECT registration_ref FROM registration WHERE registration_id=?", (reg_id,), one=True)
    method = "Reference" if reg and text.strip().upper() == reg["registration_ref"] else "Name search"
    ok, msg = rules.check_in(reg_id, session_id, g.user["user_id"], method)
    flash(msg if ok else f"Not checked in: {msg}.", "ok" if ok else "error")
    ev = request.form.get("event", type=int)
    return redirect(url_for("ops.checkin", event=ev, session=session_id, q=text))


@bp.route("/list/<int:event_id>")
@roles_required("Event Staff", "Administrator")
def printable_list(event_id):
    """Fallback for network or power failure: print it and record attendance afterwards."""
    ev = q("SELECT * FROM event WHERE event_id=?", (event_id,), one=True)
    if not ev:
        abort(404)
    rows = q("""SELECT r.registration_ref, p.surname, p.other_names, p.organisation FROM registration r
                JOIN participant p ON p.participant_id=r.participant_id
                WHERE r.event_id=? AND r.status='Confirmed' ORDER BY p.surname, p.other_names""", (event_id,))
    sess = q("SELECT * FROM session WHERE session_id=?", (request.args.get("session", type=int),), one=True)
    return render_template("list_print.html", ev=ev, rows=rows, sess=sess)


# ------------------------------------------------------------ participants (search)
@bp.route("/participants")
@roles_required("Event Staff", "Finance Officer", "Administrator")
def participants():
    text = request.args.get("q", "").strip()
    event_id = request.args.get("event", type=int)
    return render_template("participants.html", rows=search_registrations(text, event_id), text=text,
                           event_id=event_id, events=q("SELECT * FROM event ORDER BY start_date DESC"))


@bp.route("/participants/cancel/<int:reg_id>", methods=["POST"])
@roles_required("Administrator")
def cancel(reg_id):
    ok, msg = rules.cancel_registration(reg_id, g.user["user_id"])
    flash(msg, "ok" if ok else "error")
    return redirect(request.referrer or url_for("ops.participants"))


@bp.route("/staff/register/<int:event_id>", methods=["GET", "POST"])
@roles_required("Event Staff", "Administrator")
def register_for(event_id):
    ev = q("SELECT * FROM event WHERE event_id=?", (event_id,), one=True)
    if not ev:
        abort(404)
    if request.method == "POST":
        ok, msg, _ = rules.register(event_id, request.form, g.user["user_id"], user_id=None)
        flash(msg, "ok" if ok else "error")
        if ok:
            return redirect(url_for("ops.participants", event=event_id))
    return render_template("register.html", ev=ev, fields=q("SELECT * FROM event_field WHERE event_id=?", (event_id,)),
                           v=request.form if request.method == "POST" else {}, behalf=True, profile=rules.PROFILE)


# ------------------------------------------------------------ finance
@bp.route("/finance/payments")
@roles_required("Finance Officer")
def finance():
    pending = q("""SELECT p.*, r.registration_ref, pa.surname, pa.other_names, e.title FROM payment p
                   JOIN registration r ON r.registration_id=p.registration_id
                   JOIN participant pa ON pa.participant_id=r.participant_id
                   JOIN event e ON e.event_id=r.event_id WHERE p.status='Pending' ORDER BY p.payment_id""")
    recent = q("""SELECT p.*, r.registration_ref, pa.surname, pa.other_names, rc.receipt_no FROM payment p
                  JOIN registration r ON r.registration_id=p.registration_id
                  JOIN participant pa ON pa.participant_id=r.participant_id
                  LEFT JOIN receipt rc ON rc.payment_id=p.payment_id
                  WHERE p.status != 'Pending' ORDER BY p.payment_id DESC LIMIT 15""")
    return render_template("finance.html", pending=pending, recent=recent)


@bp.route("/finance/record", methods=["POST"])
@roles_required("Finance Officer")
def record():
    reg = q("SELECT registration_id FROM registration WHERE registration_ref=?",
            (request.form.get("registration_ref", "").strip().upper(),), one=True)
    if not reg:
        flash("No registration has that reference.", "error")
    else:
        ok, msg, _ = rules.record_payment(reg["registration_id"], request.form.get("amount"),
                                          request.form.get("method"), request.form.get("reference"),
                                          request.form.get("note"), g.user["user_id"])
        flash(msg, "ok" if ok else "error")
    return redirect(url_for("ops.finance"))


@bp.route("/finance/verify/<int:payment_id>", methods=["POST"])
@roles_required("Finance Officer")
def verify(payment_id):
    ok, msg = rules.verify_payment(payment_id, g.user["user_id"])
    flash(msg, "ok" if ok else "error")
    return redirect(url_for("ops.finance"))


@bp.route("/finance/reject/<int:payment_id>", methods=["POST"])
@roles_required("Finance Officer")
def reject(payment_id):
    ok, msg = rules.fail_payment(payment_id, g.user["user_id"], request.form.get("note"))
    flash(msg, "ok" if ok else "error")
    return redirect(url_for("ops.finance"))


@bp.route("/finance/waive/<int:reg_id>", methods=["POST"])
@roles_required("Finance Officer", "Administrator")
def waive(reg_id):
    ok, msg = rules.waive_fee(reg_id, g.user["user_id"], request.form.get("note"))
    flash(msg, "ok" if ok else "error")
    return redirect(request.referrer or url_for("ops.participants"))
