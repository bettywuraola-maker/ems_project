"""First-run setup and demo data."""
from werkzeug.security import generate_password_hash

from . import rules
from .db import init_db, q, run


def init_system():
    init_db()
    for name in rules.ROLES:
        run("INSERT INTO role(role_name) VALUES (?)", (name,))
    rid = q("SELECT role_id FROM role WHERE role_name='Administrator'", one=True)["role_id"]
    run("INSERT INTO user(role_id, full_name, email, password_hash) VALUES (?,?,?,?)",
        (rid, "System Administrator", "admin@ui.edu.ng", generate_password_hash("ChangeMe123")))


def _user(name, email, role, pw="Password123"):
    rid = q("SELECT role_id FROM role WHERE role_name=?", (role,), one=True)["role_id"]
    return run("INSERT INTO user(role_id, full_name, email, password_hash) VALUES (?,?,?,?)",
               (rid, name, email, generate_password_hash(pw)))


def demo_data():
    staff = _user("Check-in Staff", "staff@ui.edu.ng", "Event Staff")
    finance = _user("Finance Officer", "finance@ui.edu.ng", "Finance Officer")
    ada = _user("Ada Okonkwo", "ada@example.com", "Participant")
    v1 = run("INSERT INTO venue(name, location, capacity) VALUES (?,?,?)",
             ("Trenchard Hall", "University of Ibadan", 800))
    v2 = run("INSERT INTO venue(name, location, capacity) VALUES (?,?,?)",
             ("Faculty of Technology Auditorium", "University of Ibadan", 300))
    s1 = run("INSERT INTO speaker(name, role_type, biodata, biography, email) VALUES (?,?,?,?,?)",
             ("Prof. Folake Adeyemi", "Speaker", "Professor of Computer Science",
              "Researches software engineering and digital systems.", "folake@example.com"))
    s2 = run("INSERT INTO speaker(name, role_type, biodata, email) VALUES (?,?,?,?)",
             ("Dr. Tunde Bakare", "Guest", "Chief Technology Officer, an Ibadan software company", "tunde@example.com"))
    ev = run("""INSERT INTO event(title, description, start_date, end_date, registration_fee,
                min_sessions_for_certificate, status) VALUES (?,?,?,?,?,?,?)""",
             ("UI Computing and Technology Conference 2026",
              "Two days of talks and workshops on computing in Nigeria.", "2026-11-12", "2026-11-13", 5000, 2, "Open"))
    run("INSERT INTO event_field(event_id, field_name, is_required) VALUES (?,?,?)", (ev, "Dietary requirement", 0))
    a = run("INSERT INTO session(event_id, venue_id, title, session_date, start_time, end_time) VALUES (?,?,?,?,?,?)",
            (ev, v1, "Opening ceremony and keynote", "2026-11-12", "09:00", "11:00"))
    b = run("INSERT INTO session(event_id, venue_id, title, session_date, start_time, end_time) VALUES (?,?,?,?,?,?)",
            (ev, v2, "Workshop: building web systems", "2026-11-12", "12:00", "14:00"))
    run("INSERT INTO session(event_id, venue_id, title, session_date, start_time, end_time) VALUES (?,?,?,?,?,?)",
        (ev, v1, "Panel: computing education", "2026-11-13", "10:00", "12:00"))
    run("INSERT INTO session_speaker(session_id, speaker_id) VALUES (?,?)", (a, s1))
    run("INSERT INTO session_speaker(session_id, speaker_id) VALUES (?,?)", (b, s2))
    run("""INSERT INTO event(title, description, start_date, end_date, registration_fee, status)
           VALUES (?,?,?,?,?,?)""",
        ("Departmental Seminar Series", "Free monthly seminar.", "2026-12-04", "2026-12-04", 0, "Open"))
