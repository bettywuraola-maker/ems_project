"""Event Management System for a conference or convention (case study: University of Ibadan)."""
import os
import secrets
from functools import wraps

from flask import Flask, abort, g, redirect, render_template, request, session, url_for

from . import db as database
from .db import q

ROLE_HOME = {
    "Participant": "public.my",
    "Event Staff": "ops.checkin",
    "Finance Officer": "ops.finance",
    "Administrator": "admin.dashboard",
}


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            return redirect(url_for("public.login", next=request.full_path.rstrip("?")))
        return view(*a, **kw)
    return wrapped


def roles_required(*roles):
    """Role-based access control (NFR3, Table 3.28): a role can only use its own functions."""
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if g.user is None:
                return redirect(url_for("public.login", next=request.full_path.rstrip("?")))
            if g.user["role_name"] not in roles:
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


def create_app(config=None):
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("EMS_SECRET_KEY", "dev-only-change-me"),
        DATABASE=os.environ.get("EMS_DATABASE", os.path.join(base, "ems.sqlite")),
        UPLOAD_DIR=os.path.join(base, "uploads"),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        CSRF=True,
    )
    if config:
        app.config.update(config)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    app.teardown_appcontext(database.close_db)

    @app.before_request
    def load_user_and_check_csrf():
        g.user = None
        uid = session.get("uid")
        if uid:
            g.user = q("""SELECT u.*, r.role_name FROM user u JOIN role r ON r.role_id = u.role_id
                          WHERE u.user_id=? AND u.status='Active'""", (uid,), one=True)
            if g.user is None:
                session.clear()
        if request.method == "POST" and app.config["CSRF"]:
            sent = request.form.get("csrf", "")
            if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
                abort(400)

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return session["csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.filters["naira"] = lambda x: "\u20a6{:,.2f}".format(float(x or 0))

    @app.context_processor
    def inject_nav():
        return {"role": g.user["role_name"] if g.user else None}

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, text="Your role cannot use this page."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, text="That page does not exist."), 404

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("error.html", code=400,
                               text="The form expired or was invalid. Go back, refresh and try again."), 400

    @app.errorhandler(413)
    def too_large(e):
        return render_template("error.html", code=413, text="That file is larger than 2 MB."), 413

    from .views import public, ops, admin
    app.register_blueprint(public.bp)
    app.register_blueprint(ops.bp)
    app.register_blueprint(admin.bp)

    @app.cli.command("init-db")
    def init_db_command():
        """Create the database, the four roles and a first administrator."""
        from .seed import init_system
        init_system()
        print("Database ready. Sign in as admin@ui.edu.ng / ChangeMe123 and change the password.")

    @app.cli.command("demo-data")
    def demo_data_command():
        """Load demo users, venues, speakers and a sample conference."""
        from .seed import demo_data
        demo_data()
        print("Demo data loaded. See README.md for the demo accounts.")

    return app
