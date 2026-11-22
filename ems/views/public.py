"""Participant-facing pages: accounts, events, registration, payment, receipts, certificates."""
import io
import os
import secrets

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from .. import ROLE_HOME, login_required, roles_required
from .. import rules
from ..db import q, run

bp = Blueprint("public", __name__)

SESSIONS_SQL = """
SELECT s.*, v.name AS venue_name, v.location,
  (SELECT group_concat(sp.name, ', ') FROM session_speaker ss
     JOIN speaker sp ON sp.speaker_id = ss.speaker_id WHERE ss.session_id = s.session_id) AS speakers
FROM session s JOIN venue v ON v.venue_id = s.venue_id
WHERE s.event_id=? ORDER BY s.session_date, s.start_time"""


def my_participant():
    return q("SELECT * FROM participant WHERE user_id=?", (g.user["user_id"],), one=True)


# ------------------------------------------------------------ accounts
@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw, pw2 = request.form.get("password", ""), request.form.get("confirm", "")
        if not name or "@" not in email:
            flash("Enter your full name and a valid email address.", "error")
        elif len(pw) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif pw != pw2:
            flash("The two passwords do not match.", "error")
        elif q("SELECT 1 FROM user WHERE email=?", (email,), one=True):     # BR1
            flash("An account with that email already exists. Log in instead.", "error")
        else:
            rid = q("SELECT role_id FROM role WHERE role_name='Participant'", one=True)["role_id"]
            run("INSERT INTO user(role_id, full_name, email, password_hash) VALUES (?,?,?,?)",
                (rid, name, email, generate_password_hash(pw)))
            flash("Account created. Log in to register for an event.", "ok")
            return redirect(url_for("public.login"))
    return render_template("signup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        u = q("SELECT u.*, r.role_name FROM user u JOIN role r ON r.role_id=u.role_id WHERE u.email=?",
              (email,), one=True)
        if not u or not check_password_hash(u["password_hash"], request.form.get("password", "")):
            flash("Wrong email or password.", "error")
        elif u["status"] != "Active":
            flash("This account is disabled. Contact the administrator.", "error")
        else:
            session.clear()
            session["uid"] = u["user_id"]
            nxt = request.args.get("next", "")
            if nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            return redirect(url_for(ROLE_HOME[u["role_name"]]))
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("public.home"))


# ------------------------------------------------------------ events
@bp.route("/")
def home():
    events = q("""SELECT e.*, (SELECT COUNT(*) FROM registration r WHERE r.event_id=e.event_id
                  AND r.status != 'Cancelled') AS n FROM event e WHERE e.status='Open' ORDER BY e.start_date""")
    return render_template("home.html", events=events)


@bp.route("/event/<int:event_id>")
def event(event_id):
    ev = q("SELECT * FROM event WHERE event_id=?", (event_id,), one=True)
    if not ev or ev["status"] == "Draft" and (g.user is None or g.user["role_name"] != "Administrator"):
        abort(404)
    mine = None
    if g.user and g.user["role_name"] == "Participant":
        p = my_participant()
        if p:
            mine = q("SELECT * FROM registration WHERE participant_id=? AND event_id=?",
                     (p["participant_id"], event_id), one=True)
    return render_template("event.html", ev=ev, sessions=q(SESSIONS_SQL, (event_id,)), mine=mine,
                           fields=q("SELECT * FROM event_field WHERE event_id=?", (event_id,)))


@bp.route("/event/<int:event_id>/register", methods=["GET", "POST"])
@roles_required("Participant")
def register(event_id):
    ev = q("SELECT * FROM event WHERE event_id=?", (event_id,), one=True)
    if not ev:
        abort(404)
    if request.method == "POST":
        form = request.form.to_dict()
        form["email"] = g.user["email"]          # a participant registers with their own account email
        ok, msg, _ = rules.register(event_id, form, g.user["user_id"], user_id=g.user["user_id"])
        flash(msg, "ok" if ok else "error")
        if ok:
            return redirect(url_for("public.my"))
    p = my_participant()
    values = dict(p) if p else {"email": g.user["email"]}
    if request.method == "POST":
        values = request.form.to_dict()
        values["email"] = g.user["email"]
    values["email"] = g.user["email"]
    return render_template("register.html", ev=ev, fields=q("SELECT * FROM event_field WHERE event_id=?", (event_id,)),
                           v=values, behalf=False, profile=rules.PROFILE)


# ------------------------------------------------------------ my registrations
@bp.route("/my")
@roles_required("Participant")
def my():
    p = my_participant()
    regs = []
    if p:
        for r in q("""SELECT r.*, e.title, e.registration_fee, e.start_date FROM registration r
                      JOIN event e ON e.event_id=r.event_id WHERE r.participant_id=? ORDER BY r.registration_id DESC""",
                   (p["participant_id"],)):
            pays = q("""SELECT p.*, rc.receipt_no FROM payment p LEFT JOIN receipt rc ON rc.payment_id=p.payment_id
                        WHERE p.registration_id=? ORDER BY p.payment_id""", (r["registration_id"],))
            cert = q("SELECT * FROM certificate WHERE registration_id=?", (r["registration_id"],), one=True)
            regs.append({"r": r, "payments": pays, "cert": cert,
                         "can_pay": r["status"] == "Pending" and float(r["registration_fee"]) > 0
                                    and not any(x["status"] == "Pending" for x in pays)})
    return render_template("my.html", regs=regs)


@bp.route("/my/pay/<int:reg_id>", methods=["POST"])
@roles_required("Participant")
def pay(reg_id):
    p = my_participant()
    r = q("SELECT * FROM registration WHERE registration_id=?", (reg_id,), one=True)
    if not p or not r or r["participant_id"] != p["participant_id"]:
        abort(403)
    evidence = None
    f = request.files.get("evidence")
    if f and f.filename:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".pdf"):
            flash("Evidence must be a PNG, JPG or PDF file.", "error")
            return redirect(url_for("public.my"))
        evidence = f"{secrets.token_hex(6)}-{secure_filename(f.filename)}"
    ok, msg = rules.submit_payment(reg_id, request.form.get("reference"), evidence)
    if ok and evidence:
        f.save(os.path.join(current_app.config["UPLOAD_DIR"], evidence))
    flash(msg, "ok" if ok else "error")
    return redirect(url_for("public.my"))


@bp.route("/notifications")
@roles_required("Participant")
def notifications():
    p = my_participant()
    items = []
    if p:
        items = q("""SELECT n.*, e.title FROM notification_recipient nr
                     JOIN notification n ON n.notification_id=nr.notification_id
                     JOIN event e ON e.event_id=n.event_id
                     WHERE nr.participant_id=? ORDER BY n.notification_id DESC""", (p["participant_id"],))
    return render_template("notifications.html", items=items)


# ------------------------------------------------------------ PDFs
def _pdf(draw, landscape=False):
    from reportlab.lib.pagesizes import A4, landscape as ls
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    size = ls(A4) if landscape else A4
    c = canvas.Canvas(buf, pagesize=size)
    draw(c, *size)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


@bp.route("/receipt/<int:payment_id>.pdf")
@login_required
def receipt(payment_id):
    r = q("""SELECT rc.*, p.amount, p.method, p.reference, pa.surname, pa.other_names, pa.user_id, e.title,
                    reg.registration_ref FROM receipt rc JOIN payment p ON p.payment_id=rc.payment_id
             JOIN registration reg ON reg.registration_id=p.registration_id
             JOIN participant pa ON pa.participant_id=reg.participant_id
             JOIN event e ON e.event_id=reg.event_id WHERE rc.payment_id=?""", (payment_id,), one=True)
    if not r:
        abort(404)
    if g.user["role_name"] == "Participant" and r["user_id"] != g.user["user_id"]:
        abort(403)
    if g.user["role_name"] == "Event Staff":
        abort(403)

    def draw(c, w, h):
        c.setFont("Helvetica-Bold", 20)
        c.drawString(60, h - 80, "University of Ibadan - Event Management System")
        c.setFont("Helvetica-Bold", 16)
        c.drawString(60, h - 120, "PAYMENT RECEIPT")
        c.setFont("Helvetica", 12)
        lines = [("Receipt no", r["receipt_no"]), ("Issued", r["issued_at"]),
                 ("Received from", f"{r['surname']} {r['other_names']}"), ("Event", r["title"]),
                 ("Registration ref", r["registration_ref"]), ("Amount", f"NGN {float(r['amount']):,.2f}"),
                 ("Method", r["method"]), ("Payment reference", r["reference"] or "-")]
        y = h - 170
        for k, v in lines:
            c.drawString(60, y, f"{k}:")
            c.drawString(200, y, str(v))
            y -= 24
        c.drawString(60, y - 20, "This receipt was issued after the payment was verified.")
    return send_file(_pdf(draw), mimetype="application/pdf", download_name=f"{r['receipt_no']}.pdf")


@bp.route("/certificate/<int:reg_id>.pdf")
@login_required
def certificate(reg_id):
    c_ = q("""SELECT c.*, pa.surname, pa.other_names, pa.user_id, e.title, e.start_date, e.end_date
              FROM certificate c JOIN registration r ON r.registration_id=c.registration_id
              JOIN participant pa ON pa.participant_id=r.participant_id
              JOIN event e ON e.event_id=r.event_id WHERE c.registration_id=?""", (reg_id,), one=True)
    if not c_:
        abort(404)
    if g.user["role_name"] == "Participant" and c_["user_id"] != g.user["user_id"]:
        abort(403)
    if g.user["role_name"] not in ("Participant", "Administrator"):
        abort(403)

    def draw(c, w, h):
        c.setStrokeColorRGB(0.11, 0.17, 0.39)
        c.setLineWidth(6)
        c.rect(30, 30, w - 60, h - 60)
        c.setLineWidth(1)
        c.rect(44, 44, w - 88, h - 88)
        c.setFillColorRGB(0.11, 0.17, 0.39)
        c.setFont("Helvetica-Bold", 34)
        c.drawCentredString(w / 2, h - 130, "Certificate of Participation")
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 15)
        c.drawCentredString(w / 2, h - 190, "This is to certify that")
        c.setFont("Helvetica-Bold", 28)
        c.drawCentredString(w / 2, h - 245, f"{c_['other_names']} {c_['surname']}".upper())
        c.setFont("Helvetica", 15)
        c.drawCentredString(w / 2, h - 295, "participated in")
        c.setFont("Helvetica-Bold", 20)
        c.drawCentredString(w / 2, h - 335, c_["title"])
        c.setFont("Helvetica", 14)
        c.drawCentredString(w / 2, h - 370, f"held from {c_['start_date']} to {c_['end_date']} at the University of Ibadan")
        c.setFont("Helvetica", 11)
        c.drawCentredString(w / 2, 80, f"Certificate no {c_['certificate_no']}  |  Issued {c_['issued_at'][:10]}")
    return send_file(_pdf(draw, landscape=True), mimetype="application/pdf",
                     download_name=f"{c_['certificate_no']}.pdf")
