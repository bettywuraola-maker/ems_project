"""Administrator pages: dashboard, events, sessions, venues, speakers, users, reports, audit trail."""
import csv
import io
from datetime import date

from flask import (Blueprint, abort, flash, g, redirect, render_template, request, Response, url_for)
from werkzeug.security import generate_password_hash

from .. import roles_required
from .. import rules
from ..db import q, run
from .public import SESSIONS_SQL

bp = Blueprint("admin", __name__)


def ev_or_404(event_id):
    ev = q("SELECT * FROM event WHERE event_id=?", (event_id,), one=True)
    if not ev:
        abort(404)
    return ev


# ------------------------------------------------------------ dashboard
@bp.route("/admin")
@roles_required("Administrator")
def dashboard():
    events = q("SELECT * FROM event ORDER BY start_date DESC")
    event_id = request.args.get("event", type=int) or (events[0]["event_id"] if events else None)
    stats = rules.event_stats(event_id) if event_id else None
    pending = q("""SELECT p.*, r.registration_ref, pa.surname, pa.other_names FROM payment p
                   JOIN registration r ON r.registration_id=p.registration_id
                   JOIN participant pa ON pa.participant_id=r.participant_id
                   WHERE p.status='Pending' AND r.event_id=? ORDER BY p.payment_id LIMIT 8""", (event_id,)) if event_id else []
    return render_template("admin_home.html", events=events, event_id=event_id, stats=stats, pending=pending)


# ------------------------------------------------------------ events
def _event_form(form):
    title = form.get("title", "").strip()
    start, end = rules.parse_date(form.get("start_date")), rules.parse_date(form.get("end_date"))
    try:
        fee = round(float(form.get("registration_fee") or 0), 2)
    except ValueError:
        fee = -1
    mins = (form.get("min_sessions") or "").strip()
    if not title:
        return None, "Enter the event title."
    if not start or not end or end < start:
        return None, "Enter valid start and end dates (the end cannot be before the start)."
    if fee < 0:
        return None, "The fee must be 0 or more."
    if mins and (not mins.isdigit() or int(mins) < 1):
        return None, "Minimum sessions must be a whole number of 1 or more, or empty."
    status = form.get("status", "Draft")
    if status not in ("Draft", "Open", "Closed", "Archived"):
        status = "Draft"
    return (title, form.get("description", "").strip(), start, end, fee, int(mins) if mins else None, status), None


@bp.route("/admin/events", methods=["GET", "POST"])
@roles_required("Administrator")
def events():
    if request.method == "POST":
        vals, err = _event_form(request.form)
        if err:
            flash(err, "error")
        else:
            eid = run("""INSERT INTO event(title, description, start_date, end_date, registration_fee,
                         min_sessions_for_certificate, status) VALUES (?,?,?,?,?,?,?)""", vals)
            flash("Event created. Add sessions and open registration when ready.", "ok")
            return redirect(url_for("admin.event", event_id=eid))
    rows = q("""SELECT e.*, (SELECT COUNT(*) FROM registration r WHERE r.event_id=e.event_id
                AND r.status != 'Cancelled') AS n FROM event e ORDER BY e.start_date DESC""")
    return render_template("admin_events.html", events=rows)


@bp.route("/admin/events/<int:event_id>")
@roles_required("Administrator")
def event(event_id):
    ev = ev_or_404(event_id)
    sessions = q(SESSIONS_SQL, (event_id,))
    assigned = {s["session_id"]: q("""SELECT sp.* FROM session_speaker ss JOIN speaker sp ON sp.speaker_id=ss.speaker_id
                                      WHERE ss.session_id=?""", (s["session_id"],)) for s in sessions}
    return render_template("admin_event.html", ev=ev, sessions=sessions, assigned=assigned,
                           venues=q("SELECT * FROM venue ORDER BY name"), speakers=q("SELECT * FROM speaker ORDER BY name"),
                           fields=q("SELECT * FROM event_field WHERE event_id=?", (event_id,)),
                           stats=rules.event_stats(event_id),
                           sent=q("""SELECT n.*, (SELECT COUNT(*) FROM notification_recipient x
                                     WHERE x.notification_id=n.notification_id) AS n_to FROM notification n
                                     WHERE n.event_id=? ORDER BY n.notification_id DESC LIMIT 10""", (event_id,)))


@bp.route("/admin/events/<int:event_id>/update", methods=["POST"])
@roles_required("Administrator")
def event_update(event_id):
    ev_or_404(event_id)
    vals, err = _event_form(request.form)
    if err:
        flash(err, "error")
    else:
        run("""UPDATE event SET title=?, description=?, start_date=?, end_date=?, registration_fee=?,
               min_sessions_for_certificate=?, status=? WHERE event_id=?""", vals + (event_id,))
        flash("Event saved.", "ok")
    return redirect(url_for("admin.event", event_id=event_id))


@bp.route("/admin/events/<int:event_id>/fields", methods=["POST"])
@roles_required("Administrator")
def field_add(event_id):
    ev_or_404(event_id)
    name = request.form.get("field_name", "").strip()
    if not name:
        flash("Enter the field name.", "error")
    else:
        run("INSERT INTO event_field(event_id, field_name, is_required) VALUES (?,?,?)",
            (event_id, name, 1 if request.form.get("is_required") else 0))
        flash("Extra field added to the registration form.", "ok")
    return redirect(url_for("admin.event", event_id=event_id))


@bp.route("/admin/events/<int:event_id>/sessions", methods=["POST"])
@roles_required("Administrator")
def session_add(event_id):
    ev_or_404(event_id)
    f = request.form
    title = f.get("title", "").strip()
    venue_id = f.get("venue_id", type=int)
    if not title or not venue_id:
        flash("Enter the session title and choose a venue.", "error")
    else:
        err, warn = rules.validate_session(event_id, venue_id, f.get("session_date"), f.get("start_time"), f.get("end_time"))
        if err:
            flash(err, "error")
        else:
            run("""INSERT INTO session(event_id, venue_id, title, session_date, start_time, end_time)
                   VALUES (?,?,?,?,?,?)""", (event_id, venue_id, title, f["session_date"], f["start_time"], f["end_time"]))
            flash("Session added.", "ok")
            if warn:
                flash(warn, "warn")
    return redirect(url_for("admin.event", event_id=event_id))


@bp.route("/admin/sessions/<int:session_id>/update", methods=["POST"])
@roles_required("Administrator")
def session_update(session_id):
    s = q("SELECT * FROM session WHERE session_id=?", (session_id,), one=True)
    if not s:
        abort(404)
    f = request.form
    venue_id = f.get("venue_id", type=int) or s["venue_id"]
    err, warn = rules.validate_session(s["event_id"], venue_id, f.get("session_date"), f.get("start_time"),
                                       f.get("end_time"), exclude=session_id)
    if err:
        flash(err, "error")
    else:
        changed = (s["session_date"], s["start_time"], s["end_time"], s["venue_id"]) != \
                  (f["session_date"], f["start_time"], f["end_time"], venue_id)
        run("UPDATE session SET venue_id=?, session_date=?, start_time=?, end_time=? WHERE session_id=?",
            (venue_id, f["session_date"], f["start_time"], f["end_time"], session_id))
        if changed:
            n = rules.notify_schedule_change(session_id, g.user["user_id"], "the schedule or venue has changed")  # BR11
            flash(f"Session updated. {n} participant(s) notified.", "ok")
        else:
            flash("Session saved.", "ok")
        if warn:
            flash(warn, "warn")
    return redirect(url_for("admin.event", event_id=s["event_id"]))


@bp.route("/admin/sessions/<int:session_id>/assign", methods=["POST"])
@roles_required("Administrator")
def assign(session_id):
    s = q("SELECT * FROM session WHERE session_id=?", (session_id,), one=True)
    sp = request.form.get("speaker_id", type=int)
    if not s or not sp:
        abort(404)
    if q("SELECT 1 FROM session_speaker WHERE session_id=? AND speaker_id=?", (session_id, sp), one=True):
        flash("That speaker is already assigned to this session.", "error")
    else:
        clash = rules.speaker_conflict(sp, session_id)
        if clash:
            flash(f"Speaker clash: already assigned to '{clash['title']}' ({clash['start_time']}-{clash['end_time']}).", "error")
        else:
            run("INSERT INTO session_speaker(session_id, speaker_id) VALUES (?,?)", (session_id, sp))
            flash("Speaker assigned.", "ok")
    return redirect(url_for("admin.event", event_id=s["event_id"]))


@bp.route("/admin/sessions/<int:session_id>/unassign/<int:speaker_id>", methods=["POST"])
@roles_required("Administrator")
def unassign(session_id, speaker_id):
    s = q("SELECT * FROM session WHERE session_id=?", (session_id,), one=True)
    if not s:
        abort(404)
    run("DELETE FROM session_speaker WHERE session_id=? AND speaker_id=?", (session_id, speaker_id))
    flash("Speaker removed from the session.", "ok")
    return redirect(url_for("admin.event", event_id=s["event_id"]))


@bp.route("/admin/events/<int:event_id>/certificates", methods=["POST"])
@roles_required("Administrator")
def certificates(event_id):
    ev_or_404(event_id)
    issued, skipped = rules.generate_certificates(event_id, g.user["user_id"])
    flash(f"{issued} certificate(s) issued. {len(skipped)} registration(s) not eligible.", "ok")
    for ref, why in skipped[:5]:
        flash(f"{ref}: {why}", "warn")
    return redirect(url_for("admin.event", event_id=event_id))


@bp.route("/admin/events/<int:event_id>/announce", methods=["POST"])
@roles_required("Administrator")
def announce(event_id):
    ev_or_404(event_id)
    f = request.form
    subject, message, channel = f.get("subject", "").strip(), f.get("message", "").strip(), f.get("channel")
    audience = f.get("audience", "participants")
    if not subject or not message or channel not in ("Email", "SMS"):
        flash("Enter a subject, a message and a channel.", "error")
        return redirect(url_for("admin.event", event_id=event_id))
    pids = [r["participant_id"] for r in q(
        "SELECT participant_id FROM registration WHERE event_id=? AND status != 'Cancelled'", (event_id,))] \
        if audience in ("participants", "both") else []
    sids = [r["speaker_id"] for r in q("""SELECT DISTINCT ss.speaker_id FROM session_speaker ss
                                          JOIN session s ON s.session_id=ss.session_id WHERE s.event_id=?""", (event_id,))] \
        if audience in ("speakers", "both") else []
    rules.notify(event_id, subject, message, channel, g.user["user_id"], pids, sids)
    flash(f"Sent to {len(pids)} participant(s) and {len(sids)} speaker(s)/guest(s).", "ok")
    return redirect(url_for("admin.event", event_id=event_id))


# ------------------------------------------------------------ venues and speakers
@bp.route("/admin/venues", methods=["GET", "POST"])
@roles_required("Administrator")
def venues():
    if request.method == "POST":
        name, cap = request.form.get("name", "").strip(), (request.form.get("capacity") or "").strip()
        if not name or (cap and not cap.isdigit()):
            flash("Enter the venue name; capacity must be a number.", "error")
        else:
            run("INSERT INTO venue(name, location, capacity) VALUES (?,?,?)",
                (name, request.form.get("location", "").strip(), int(cap) if cap else None))
            flash("Venue added.", "ok")
    return render_template("admin_venues.html", venues=q("SELECT * FROM venue ORDER BY name"))


@bp.route("/admin/speakers", methods=["GET", "POST"])
@roles_required("Administrator")
def speakers():
    if request.method == "POST":
        f = request.form
        name = f.get("name", "").strip()
        if not name or f.get("role_type") not in ("Speaker", "Guest"):
            flash("Enter the name and choose Speaker or Guest.", "error")
        else:
            run("INSERT INTO speaker(name, role_type, biodata, biography, email, phone) VALUES (?,?,?,?,?,?)",
                (name, f["role_type"], f.get("biodata", "").strip(), f.get("biography", "").strip(),
                 f.get("email", "").strip(), f.get("phone", "").strip()))
            flash("Profile saved.", "ok")
    return render_template("admin_speakers.html", speakers=q("SELECT * FROM speaker ORDER BY name"))


# ------------------------------------------------------------ users and audit
@bp.route("/admin/users", methods=["GET", "POST"])
@roles_required("Administrator")
def users():
    if request.method == "POST":
        f = request.form
        name, email, pw = f.get("full_name", "").strip(), f.get("email", "").strip().lower(), f.get("password", "")
        role = q("SELECT role_id FROM role WHERE role_name=?", (f.get("role"),), one=True)
        if not name or "@" not in email or len(pw) < 8 or not role:
            flash("Enter a name, valid email, role and a password of at least 8 characters.", "error")
        elif q("SELECT 1 FROM user WHERE email=?", (email,), one=True):
            flash("An account with that email already exists.", "error")
        else:
            uid = run("INSERT INTO user(role_id, full_name, email, password_hash) VALUES (?,?,?,?)",
                      (role["role_id"], name, email, generate_password_hash(pw)))
            rules.audit(g.user["user_id"], f"account created ({f.get('role')})", "user", uid)
            flash("Account created.", "ok")
    return render_template("admin_users.html", users=q(
        "SELECT u.*, r.role_name FROM user u JOIN role r ON r.role_id=u.role_id ORDER BY r.role_id, u.full_name"),
        roles=rules.ROLES)


@bp.route("/admin/users/<int:user_id>", methods=["POST"])
@roles_required("Administrator")
def user_update(user_id):
    u = q("SELECT * FROM user WHERE user_id=?", (user_id,), one=True)
    if not u:
        abort(404)
    if user_id == g.user["user_id"]:
        flash("You cannot change your own role or disable your own account.", "error")
    else:
        role = q("SELECT role_id FROM role WHERE role_name=?", (request.form.get("role"),), one=True)
        status = "Disabled" if request.form.get("disable") else "Active"   # accounts are disabled, never deleted (BR13)
        run("UPDATE user SET role_id=?, status=? WHERE user_id=?", (role["role_id"] if role else u["role_id"], status, user_id))
        rules.audit(g.user["user_id"], f"account updated (status {status})", "user", user_id)
        flash("Account updated.", "ok")
    return redirect(url_for("admin.users"))


@bp.route("/admin/audit")
@roles_required("Administrator")
def audit():
    return render_template("audit.html", rows=q("""SELECT a.*, u.full_name FROM audit_log a
                                                   JOIN user u ON u.user_id=a.user_id ORDER BY a.log_id DESC LIMIT 200"""))


# ------------------------------------------------------------ reports and past events
@bp.route("/reports")
@roles_required("Administrator", "Finance Officer")
def reports():
    events = q("SELECT * FROM event ORDER BY start_date DESC")
    event_id = request.args.get("event", type=int) or (events[0]["event_id"] if events else None)
    data = {}
    if event_id:
        data["stats"] = rules.event_stats(event_id)
        data["by_method"] = q("""SELECT p.method, p.status, COUNT(*) n, SUM(p.amount) total FROM payment p
                                 JOIN registration r ON r.registration_id=p.registration_id WHERE r.event_id=?
                                 GROUP BY p.method, p.status""", (event_id,))
        data["attendance"] = q("""SELECT s.title, s.session_date, COUNT(a.attendance_id) n FROM session s
                                  LEFT JOIN attendance a ON a.session_id=s.session_id WHERE s.event_id=?
                                  GROUP BY s.session_id ORDER BY s.session_date, s.start_time""", (event_id,))
    return render_template("reports.html", events=events, event_id=event_id, **data)


@bp.route("/reports/<int:event_id>/<kind>.csv")
@roles_required("Administrator", "Finance Officer")
def export(event_id, kind):
    if g.user["role_name"] == "Finance Officer" and kind != "payments":
        abort(403)
    queries = {
        "registrations": ("""SELECT r.registration_ref, p.surname, p.other_names, p.gender, p.organisation, p.level_cadre,
                             p.membership_status, p.email, p.phone, r.purpose, r.expected_days, r.status, r.registered_at
                             FROM registration r JOIN participant p ON p.participant_id=r.participant_id
                             WHERE r.event_id=? ORDER BY p.surname""", None),
        "payments": ("""SELECT r.registration_ref, p.surname, pay.amount, pay.method, pay.reference, pay.status,
                        rc.receipt_no, pay.verified_at, pay.note FROM payment pay
                        JOIN registration r ON r.registration_id=pay.registration_id
                        JOIN participant p ON p.participant_id=r.participant_id
                        LEFT JOIN receipt rc ON rc.payment_id=pay.payment_id WHERE r.event_id=? ORDER BY pay.payment_id""", None),
        "attendance": ("""SELECT r.registration_ref, p.surname, p.other_names, s.title, s.session_date, a.checked_in_at, a.method
                          FROM attendance a JOIN registration r ON r.registration_id=a.registration_id
                          JOIN participant p ON p.participant_id=r.participant_id
                          JOIN session s ON s.session_id=a.session_id WHERE r.event_id=? ORDER BY s.session_date, a.checked_in_at""", None),
        "certificates": ("""SELECT c.certificate_no, r.registration_ref, p.surname, p.other_names, c.issued_at
                            FROM certificate c JOIN registration r ON r.registration_id=c.registration_id
                            JOIN participant p ON p.participant_id=r.participant_id WHERE r.event_id=?""", None),
    }
    if kind not in queries:
        abort(404)
    rows = q(queries[kind][0], (event_id,))
    buf = io.StringIO()
    w = csv.writer(buf)
    if rows:
        w.writerow(rows[0].keys())
        w.writerows([tuple(r) for r in rows])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=event{event_id}-{kind}.csv"})


@bp.route("/admin/past")
@roles_required("Administrator")
def past():
    today = date.today().isoformat()
    rows = q("SELECT * FROM event WHERE status IN ('Closed','Archived') OR end_date < ? ORDER BY start_date DESC", (today,))
    return render_template("past.html", rows=[(e, rules.event_stats(e["event_id"])) for e in rows])
