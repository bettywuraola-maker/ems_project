"""Automated tests. Each test names the business rule (BR), requirement or use case it checks.
Run with:  python -m unittest discover -s tests -v
"""
import os
import shutil
import sqlite3
import tempfile
import unittest

from ems import create_app
from ems.db import q
from ems.seed import demo_data, init_system

PW = "Password123"
# Demo data ids: event 1 = paid conference (NGN 5000, min 2 sessions), event 2 = free seminar,
# sessions 1-3 belong to event 1 (1: day1 09-11 venue1, 2: day1 12-14 venue2, 3: day2 venue1),
# speakers 1 and 2 are assigned to sessions 1 and 2.


def profile(email, **kw):
    d = {"surname": "Okoro", "other_names": "Ife", "gender": "Female", "organisation": "UI",
         "level_cadre": "300L", "membership_status": "Member", "email": email, "phone": "08030000000"}
    d.update(kw)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "t.sqlite")
        self.app = create_app({"TESTING": True, "DATABASE": self.path, "CSRF": False,
                               "UPLOAD_DIR": self.tmp, "SECRET_KEY": "test"})
        with self.app.app_context():
            init_system()
            demo_data()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers
    def login(self, email, pw=PW):
        c = self.app.test_client()
        r = c.post("/login", data={"email": email, "password": pw})
        self.assertEqual(r.status_code, 302, f"login failed for {email}")
        return c

    def admin(self):
        return self.login("admin@ui.edu.ng", "ChangeMe123")

    def signup(self, email, name="Test User"):
        c = self.app.test_client()
        c.post("/signup", data={"full_name": name, "email": email, "password": PW, "confirm": PW})
        return self.login(email)

    def register(self, client, event=1, **kw):
        data = profile("ignored@x.com")
        data.update(kw)
        return client.post(f"/event/{event}/register", data=data, follow_redirects=True)

    def sql(self, sql, args=()):
        with self.app.app_context():
            return [dict(r) for r in q(sql, args)]

    def write(self, sql, args=()):
        con = sqlite3.connect(self.path)
        con.execute(sql, args)
        con.commit()
        con.close()

    def reg_of(self, email, event=1):
        return self.sql("""SELECT r.* FROM registration r JOIN participant p ON p.participant_id=r.participant_id
                           WHERE p.email=? AND r.event_id=?""", (email, event))[0]

    def paid_participant(self, email="p@x.com"):
        """A participant whose payment is verified (registration Confirmed)."""
        c = self.signup(email)
        self.register(c)
        reg = self.reg_of(email)
        c.post(f"/my/pay/{reg['registration_id']}", data={"reference": "TRF12345"})
        fin = self.login("finance@ui.edu.ng")
        pay = self.sql("SELECT payment_id FROM payment WHERE registration_id=?", (reg["registration_id"],))[0]
        fin.post(f"/finance/verify/{pay['payment_id']}")
        return c, self.reg_of(email)


class TestAccountsAndRegistration(Base):
    def test_br1_one_account_per_email(self):
        self.signup("a@x.com")
        r = self.app.test_client().post("/signup", data={"full_name": "Dup", "email": "A@x.com", "password": PW, "confirm": PW})
        self.assertIn(b"already exists", r.data)
        self.assertEqual(len(self.sql("SELECT * FROM user WHERE email='a@x.com'")), 1)

    def test_br1_unique_registration_references(self):
        refs = set()
        for i in range(5):
            c = self.signup(f"u{i}@x.com")
            self.register(c, event=2)
            refs.add(self.reg_of(f"u{i}@x.com", 2)["registration_ref"])
        self.assertEqual(len(refs), 5)

    def test_passwords_are_hashed(self):
        self.signup("h@x.com")
        h = self.sql("SELECT password_hash FROM user WHERE email='h@x.com'")[0]["password_hash"]
        self.assertNotIn(PW, h)

    def test_br2_cannot_register_twice(self):
        c = self.signup("d@x.com")
        self.register(c)
        r = self.register(c)
        self.assertIn(b"Already registered", r.data)
        self.assertEqual(len(self.sql("SELECT * FROM registration")), 1)

    def test_br3_paid_event_starts_pending_free_event_confirmed(self):
        c = self.signup("f@x.com")
        self.register(c, event=1)
        self.register(c, event=2)
        self.assertEqual(self.reg_of("f@x.com", 1)["status"], "Pending")
        self.assertEqual(self.reg_of("f@x.com", 2)["status"], "Confirmed")

    def test_fr4_missing_required_fields_saves_nothing(self):
        c = self.signup("m@x.com")
        r = self.register(c, surname="", phone="")
        self.assertIn(b"Please fill in", r.data)
        self.assertEqual(self.sql("SELECT * FROM registration"), [])
        self.assertEqual(self.sql("SELECT * FROM participant"), [])

    def test_required_extra_event_field_enforced(self):
        self.write("UPDATE event_field SET is_required=1 WHERE field_id=1")
        c = self.signup("e@x.com")
        r = self.register(c)
        self.assertIn(b"Dietary requirement", r.data)
        self.assertEqual(self.sql("SELECT * FROM registration"), [])
        self.register(c, field_1="None")
        self.assertEqual(self.sql("SELECT value FROM field_response")[0]["value"], "None")

    def test_closed_event_rejects_registration(self):
        self.write("UPDATE event SET status='Closed' WHERE event_id=1")
        c = self.signup("c@x.com")
        r = self.register(c)
        self.assertIn(b"not open", r.data)

    def test_fr3_staff_registers_person_without_account_then_account_links(self):
        staff = self.login("staff@ui.edu.ng")
        r = staff.post("/staff/register/1", data=profile("walkin@x.com"), follow_redirects=True)
        self.assertIn(b"Reference", r.data)
        reg = self.reg_of("walkin@x.com")
        self.assertIsNotNone(reg["registered_by"])
        self.assertIsNone(self.sql("SELECT user_id FROM participant WHERE email='walkin@x.com'")[0]["user_id"])
        c = self.signup("walkin@x.com")                       # later creates an account
        r = self.register(c)
        self.assertIn(b"Already registered", r.data)           # linked profile, no duplicate (BR2)
        self.assertEqual(len(self.sql("SELECT * FROM participant WHERE email='walkin@x.com'")), 1)


class TestPayments(Base):
    def test_br4_br5_verify_confirms_and_issues_receipt(self):
        c = self.signup("p@x.com")
        self.register(c)
        reg = self.reg_of("p@x.com")
        c.post(f"/my/pay/{reg['registration_id']}", data={"reference": "TRF12345"})
        self.assertEqual(self.sql("SELECT status FROM payment")[0]["status"], "Pending")
        self.assertEqual(self.sql("SELECT * FROM receipt"), [])                  # no receipt before verification
        self.assertEqual(self.reg_of("p@x.com")["status"], "Pending")
        pay = self.sql("SELECT payment_id FROM payment")[0]
        self.login("finance@ui.edu.ng").post(f"/finance/verify/{pay['payment_id']}")
        self.assertEqual(self.sql("SELECT status FROM payment")[0]["status"], "Verified")
        self.assertEqual(len(self.sql("SELECT * FROM receipt")), 1)
        self.assertEqual(self.reg_of("p@x.com")["status"], "Confirmed")

    def test_br5_only_finance_can_verify(self):
        c = self.signup("p@x.com")
        self.register(c)
        reg = self.reg_of("p@x.com")
        c.post(f"/my/pay/{reg['registration_id']}", data={"reference": "TRF12345"})
        pid = self.sql("SELECT payment_id FROM payment")[0]["payment_id"]
        for who in (c, self.login("staff@ui.edu.ng"), self.admin()):
            self.assertEqual(who.post(f"/finance/verify/{pid}").status_code, 403)
        self.assertEqual(self.sql("SELECT status FROM payment")[0]["status"], "Pending")

    def test_failed_payment_can_be_resubmitted_and_gets_no_receipt(self):
        c = self.signup("p@x.com")
        self.register(c)
        reg = self.reg_of("p@x.com")
        c.post(f"/my/pay/{reg['registration_id']}", data={"reference": "WRONG1"})
        fin = self.login("finance@ui.edu.ng")
        pid = self.sql("SELECT payment_id FROM payment")[0]["payment_id"]
        fin.post(f"/finance/reject/{pid}")
        self.assertEqual(self.sql("SELECT status FROM payment")[0]["status"], "Failed")
        self.assertEqual(self.sql("SELECT * FROM receipt"), [])
        self.assertEqual(self.reg_of("p@x.com")["status"], "Pending")
        c.post(f"/my/pay/{reg['registration_id']}", data={"reference": "RIGHT12"})   # state diagram: Failed -> Pending
        self.assertEqual(self.sql("SELECT status FROM payment")[0]["status"], "Pending")
        fin.post(f"/finance/verify/{pid}")
        self.assertEqual(self.reg_of("p@x.com")["status"], "Confirmed")

    def test_cannot_verify_twice(self):
        c, reg = self.paid_participant()
        pid = self.sql("SELECT payment_id FROM payment")[0]["payment_id"]
        r = self.login("finance@ui.edu.ng").post(f"/finance/verify/{pid}", follow_redirects=True)
        self.assertIn(b"Only Pending", r.data)
        self.assertEqual(len(self.sql("SELECT * FROM receipt")), 1)

    def test_br6_cash_recorded_by_finance_is_receipted(self):
        c = self.signup("cash@x.com")
        self.register(c)
        reg = self.reg_of("cash@x.com")
        fin = self.login("finance@ui.edu.ng")
        fin.post("/finance/record", data={"registration_ref": reg["registration_ref"], "amount": "5000", "method": "Cash"})
        self.assertEqual(self.sql("SELECT status, method FROM payment")[0], {"status": "Verified", "method": "Cash"})
        self.assertEqual(len(self.sql("SELECT * FROM receipt")), 1)
        self.assertEqual(self.reg_of("cash@x.com")["status"], "Confirmed")

    def test_record_payment_validates_amount(self):
        c = self.signup("cash@x.com")
        self.register(c)
        ref = self.reg_of("cash@x.com")["registration_ref"]
        fin = self.login("finance@ui.edu.ng")
        fin.post("/finance/record", data={"registration_ref": ref, "amount": "-5", "method": "Cash"})
        fin.post("/finance/record", data={"registration_ref": ref, "amount": "abc", "method": "Cash"})
        self.assertEqual(self.sql("SELECT * FROM payment"), [])

    def test_fr24_waiver_confirms_without_receipt(self):
        c = self.signup("w@x.com")
        self.register(c)
        reg = self.reg_of("w@x.com")
        self.login("finance@ui.edu.ng").post(f"/finance/waive/{reg['registration_id']}")
        self.assertEqual(self.sql("SELECT status FROM payment")[0]["status"], "Waived")
        self.assertEqual(self.sql("SELECT * FROM receipt"), [])              # BR4: receipt only when Verified
        self.assertEqual(self.reg_of("w@x.com")["status"], "Confirmed")

    def test_admin_can_waive_but_staff_cannot(self):
        c = self.signup("w@x.com")
        self.register(c)
        rid = self.reg_of("w@x.com")["registration_id"]
        self.assertEqual(self.login("staff@ui.edu.ng").post(f"/finance/waive/{rid}").status_code, 403)
        self.admin().post(f"/finance/waive/{rid}")
        self.assertEqual(self.reg_of("w@x.com")["status"], "Confirmed")

    def test_pdf_receipt_access(self):
        c, reg = self.paid_participant()
        pid = self.sql("SELECT payment_id FROM payment")[0]["payment_id"]
        r = c.get(f"/receipt/{pid}.pdf")
        self.assertEqual((r.status_code, r.mimetype), (200, "application/pdf"))
        self.assertTrue(r.data.startswith(b"%PDF"))
        other = self.signup("other@x.com")
        self.assertEqual(other.get(f"/receipt/{pid}.pdf").status_code, 403)
        self.assertEqual(self.login("finance@ui.edu.ng").get(f"/receipt/{pid}.pdf").status_code, 200)


class TestAttendance(Base):
    def test_br7_pending_cannot_check_in_confirmed_can_once(self):
        staff = self.login("staff@ui.edu.ng")
        c = self.signup("a@x.com")
        self.register(c)
        reg = self.reg_of("a@x.com")
        staff.post("/staff/checkin", data={"registration_id": reg["registration_id"], "session_id": 1, "q": "a", "event": 1})
        self.assertEqual(self.sql("SELECT * FROM attendance"), [])
        self.login("finance@ui.edu.ng").post(f"/finance/waive/{reg['registration_id']}")
        for _ in range(2):
            staff.post("/staff/checkin", data={"registration_id": reg["registration_id"], "session_id": 1, "q": "a", "event": 1})
        self.assertEqual(len(self.sql("SELECT * FROM attendance")), 1)
        self.assertEqual(len(self.sql("SELECT * FROM audit_log WHERE action LIKE 'check-in rejected%'")), 2)   # logged

    def test_checkin_by_reference_and_by_name(self):
        c, reg = self.paid_participant()
        staff = self.login("staff@ui.edu.ng")
        r = staff.get(f"/staff/checkin?event=1&session=1&q={reg['registration_ref'].lower()}")
        self.assertIn(b"Okoro", r.data)
        r = staff.get("/staff/checkin?event=1&session=1&q=Ife")
        self.assertIn(reg["registration_ref"].encode(), r.data)
        staff.post("/staff/checkin", data={"registration_id": reg["registration_id"], "session_id": 1,
                                           "q": reg["registration_ref"], "event": 1})
        self.assertEqual(self.sql("SELECT method FROM attendance")[0]["method"], "Reference")

    def test_session_must_belong_to_registrations_event(self):
        c = self.signup("a@x.com")
        self.register(c, event=2)
        reg = self.reg_of("a@x.com", 2)
        self.login("staff@ui.edu.ng").post("/staff/checkin", data={"registration_id": reg["registration_id"], "session_id": 1})
        self.assertEqual(self.sql("SELECT * FROM attendance"), [])

    def test_printable_list_only_confirmed(self):
        self.paid_participant("paid@x.com")
        c = self.signup("unpaid@x.com")
        self.register(c, surname="Pending")
        r = self.login("staff@ui.edu.ng").get("/list/1?session=1")
        self.assertIn(b"Okoro", r.data)
        self.assertNotIn(b"Pending</td>", r.data)


class TestCancellation(Base):
    def test_br14_cancelled_cannot_check_in_pay_or_get_certificate_and_record_kept(self):
        c, reg = self.paid_participant()
        adm = self.admin()
        adm.post(f"/participants/cancel/{reg['registration_id']}")
        self.assertEqual(self.reg_of("p@x.com")["status"], "Cancelled")
        self.assertEqual(len(self.sql("SELECT * FROM registration")), 1)          # BR13: archived, not deleted
        staff = self.login("staff@ui.edu.ng")
        staff.post("/staff/checkin", data={"registration_id": reg["registration_id"], "session_id": 1})
        self.assertEqual(self.sql("SELECT * FROM attendance"), [])
        adm.post("/admin/events/1/certificates")
        self.assertEqual(self.sql("SELECT * FROM certificate"), [])
        self.assertEqual(c.get(f"/certificate/{reg['registration_id']}.pdf").status_code, 404)

    def test_only_admin_cancels(self):
        c, reg = self.paid_participant()
        for who in (c, self.login("staff@ui.edu.ng"), self.login("finance@ui.edu.ng")):
            self.assertEqual(who.post(f"/participants/cancel/{reg['registration_id']}").status_code, 403)

    def test_cancelled_registration_cannot_receive_payment(self):
        c = self.signup("x@x.com")
        self.register(c)
        reg = self.reg_of("x@x.com")
        self.admin().post(f"/participants/cancel/{reg['registration_id']}")
        c.post(f"/my/pay/{reg['registration_id']}", data={"reference": "TRF12345"})
        self.assertEqual(self.sql("SELECT * FROM payment"), [])


class TestCertificates(Base):
    def attend(self, reg, sessions):
        staff = self.login("staff@ui.edu.ng")
        for s in sessions:
            staff.post("/staff/checkin", data={"registration_id": reg["registration_id"], "session_id": s})

    def test_br8_eligibility_rules(self):
        c, reg = self.paid_participant("ok@x.com")                       # paid + attends 2 sessions
        self.attend(reg, [1, 2])
        c2, reg2 = self.paid_participant("few@x.com")                    # paid, attends 1 session only
        self.attend(reg2, [1])
        c3 = self.signup("unpaid@x.com")                                 # attends none, unpaid
        self.register(c3)
        adm = self.admin()
        adm.post("/admin/events/1/certificates")
        certs = self.sql("SELECT c.*, p.email FROM certificate c JOIN registration r ON r.registration_id=c.registration_id "
                         "JOIN participant p ON p.participant_id=r.participant_id")
        self.assertEqual([x["email"] for x in certs], ["ok@x.com"])
        adm.post("/admin/events/1/certificates")                         # run again: one certificate per registration
        self.assertEqual(len(self.sql("SELECT * FROM certificate")), 1)
        r = c.get(f"/certificate/{reg['registration_id']}.pdf")
        self.assertEqual((r.status_code, r.mimetype), (200, "application/pdf"))
        self.assertEqual(c2.get(f"/certificate/{reg2['registration_id']}.pdf").status_code, 404)

    def test_waived_participant_with_attendance_gets_certificate(self):
        c = self.signup("w@x.com")
        self.register(c)
        reg = self.reg_of("w@x.com")
        self.login("finance@ui.edu.ng").post(f"/finance/waive/{reg['registration_id']}")
        self.attend(reg, [1, 3])
        self.admin().post("/admin/events/1/certificates")
        self.assertEqual(len(self.sql("SELECT * FROM certificate")), 1)

    def test_certificate_numbers_unique_and_other_user_blocked(self):
        c, reg = self.paid_participant("a@x.com")
        c2, reg2 = self.paid_participant("b@x.com")
        self.attend(reg, [1, 2]); self.attend(reg2, [1, 2])
        self.admin().post("/admin/events/1/certificates")
        nos = [x["certificate_no"] for x in self.sql("SELECT certificate_no FROM certificate")]
        self.assertEqual(len(set(nos)), 2)
        self.assertEqual(c2.get(f"/certificate/{reg['registration_id']}.pdf").status_code, 403)

    def test_free_event_certificate_without_payment(self):
        c = self.signup("free@x.com")
        self.register(c, event=2)
        self.admin().post("/admin/events/2/certificates")
        self.assertEqual(len(self.sql("SELECT * FROM certificate")), 1)


class TestScheduling(Base):
    def add_session(self, adm, venue, date="2026-11-12", start="09:30", end="10:30", event=1):
        return adm.post(f"/admin/events/{event}/sessions", data={"title": "New", "venue_id": venue, "session_date": date,
                        "start_time": start, "end_time": end}, follow_redirects=True)

    def test_br9_venue_overlap_rejected_adjacent_allowed(self):
        adm = self.admin()
        r = self.add_session(adm, 1)                                   # overlaps session 1 (09:00-11:00) in venue 1
        self.assertIn(b"Venue clash", r.data)
        self.assertEqual(len(self.sql("SELECT * FROM session")), 3)
        self.add_session(adm, 1, start="11:00", end="12:00")           # touches the end, no overlap
        self.add_session(adm, 2, start="09:30", end="10:30")           # different venue
        self.assertEqual(len(self.sql("SELECT * FROM session")), 5)

    def test_br9_capacity_warning(self):
        self.write("UPDATE venue SET capacity=1 WHERE venue_id=2")
        for i in range(2):
            self.register(self.signup(f"c{i}@x.com"))
        r = self.add_session(self.admin(), 2, date="2026-11-14", start="09:00", end="10:00")
        self.assertIn(b"exceed this venue", r.data)
        self.assertEqual(len(self.sql("SELECT * FROM session")), 4)    # a warning, not a block

    def test_invalid_times_rejected(self):
        r = self.add_session(self.admin(), 2, start="14:00", end="13:00")
        self.assertIn(b"End time must be after", r.data)

    def test_br10_speaker_overlap_rejected(self):
        adm = self.admin()
        self.add_session(adm, 2, date="2026-11-12", start="09:30", end="10:30")      # overlaps session 1 time, venue 2 free
        new = self.sql("SELECT MAX(session_id) m FROM session")[0]["m"]
        r = adm.post(f"/admin/sessions/{new}/assign", data={"speaker_id": 1}, follow_redirects=True)   # speaker 1 is in session 1
        self.assertIn(b"Speaker clash", r.data)
        self.assertEqual(len(self.sql("SELECT * FROM session_speaker WHERE session_id=?", (new,))), 0)
        r = adm.post(f"/admin/sessions/{new}/assign", data={"speaker_id": 2}, follow_redirects=True)   # speaker 2 is free then
        self.assertIn(b"Speaker assigned", r.data)

    def test_br11_schedule_change_notifies_participants(self):
        c = self.signup("n@x.com")
        self.register(c)
        self.admin().post("/admin/sessions/1/update", data={"venue_id": 1, "session_date": "2026-11-12",
                          "start_time": "10:00", "end_time": "12:00"})
        r = c.get("/notifications")
        self.assertIn(b"Programme change", r.data)
        n_before = len(self.sql("SELECT * FROM notification WHERE subject='Programme change'"))
        self.admin().post("/admin/sessions/1/update", data={"venue_id": 1, "session_date": "2026-11-12",
                          "start_time": "10:00", "end_time": "12:00"})        # unchanged: no new message
        self.assertEqual(len(self.sql("SELECT * FROM notification WHERE subject='Programme change'")), n_before)

    def test_announcement_reaches_participants_and_speakers(self):
        c = self.signup("n@x.com")
        self.register(c)
        r = self.admin().post("/admin/events/1/announce", data={"subject": "Venue notice", "message": "Bring ID.",
                              "channel": "SMS", "audience": "both"}, follow_redirects=True)
        self.assertIn(b"Sent to 1 participant(s) and 2 speaker(s)", r.data)
        self.assertIn(b"Venue notice", c.get("/notifications").data)
        self.assertEqual(self.sql("SELECT DISTINCT delivery_status FROM notification_recipient")[0]["delivery_status"], "Sent")


class TestAuditAndAccess(Base):
    def test_br12_actions_are_audited(self):
        c, reg = self.paid_participant()
        self.login("staff@ui.edu.ng").post("/staff/checkin", data={"registration_id": reg["registration_id"], "session_id": 1})
        adm = self.admin()
        adm.post("/admin/events/1/certificates")
        adm.post(f"/participants/cancel/{reg['registration_id']}")
        actions = {a["action"] for a in self.sql("SELECT action FROM audit_log")}
        for needed in ("payment verified", "attendance recorded", "registration cancelled"):
            self.assertIn(needed, actions)
        self.assertIn(b"payment verified", adm.get("/admin/audit").data)

    def test_waiver_and_certificate_audited(self):
        c = self.signup("w@x.com")
        self.register(c, event=2)
        self.admin().post("/admin/events/2/certificates")
        c2 = self.signup("w2@x.com")
        self.register(c2)
        self.login("finance@ui.edu.ng").post(f"/finance/waive/{self.reg_of('w2@x.com')['registration_id']}")
        actions = {a["action"] for a in self.sql("SELECT action FROM audit_log")}
        self.assertTrue({"certificate issued", "fee waived"} <= actions)

    def test_role_permission_matrix(self):
        part, staff, fin, adm = self.signup("p@x.com"), self.login("staff@ui.edu.ng"), self.login("finance@ui.edu.ng"), self.admin()
        expect = {                                    # path: (participant, staff, finance, admin)
            "/my": (200, 403, 403, 403),
            "/staff/checkin": (403, 200, 403, 403),
            "/finance/payments": (403, 403, 200, 403),
            "/participants": (403, 200, 200, 200),
            "/admin": (403, 403, 403, 200),
            "/admin/users": (403, 403, 403, 200),
            "/admin/audit": (403, 403, 403, 200),
            "/admin/past": (403, 403, 403, 200),
            "/admin/venues": (403, 403, 403, 200),
            "/reports": (403, 403, 200, 200),
            "/reports/1/registrations.csv": (403, 403, 403, 200),
            "/reports/1/payments.csv": (403, 403, 200, 200),
            "/staff/register/1": (403, 200, 403, 200),
            "/list/1": (403, 200, 403, 200),
        }
        for path, codes in expect.items():
            for who, name, code in zip((part, staff, fin, adm), ("participant", "staff", "finance", "admin"), codes):
                self.assertEqual(who.get(path).status_code, code, f"{name} on {path}")

    def test_login_required_redirects(self):
        r = self.app.test_client().get("/admin")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_wrong_password_and_disabled_account(self):
        self.signup("d@x.com")
        r = self.app.test_client().post("/login", data={"email": "d@x.com", "password": "nope"})
        self.assertIn(b"Wrong email or password", r.data)
        uid = self.sql("SELECT user_id FROM user WHERE email='d@x.com'")[0]["user_id"]
        self.admin().post(f"/admin/users/{uid}", data={"role": "Participant", "disable": "1"})
        r = self.app.test_client().post("/login", data={"email": "d@x.com", "password": PW})
        self.assertIn(b"disabled", r.data)
        self.assertEqual(len(self.sql("SELECT * FROM user WHERE email='d@x.com'")), 1)       # disabled, not deleted

    def test_csrf_blocks_forged_post(self):
        app = create_app({"TESTING": True, "DATABASE": self.path, "CSRF": True, "UPLOAD_DIR": self.tmp, "SECRET_KEY": "t"})
        c = app.test_client()
        self.assertEqual(c.post("/login", data={"email": "admin@ui.edu.ng", "password": "ChangeMe123"}).status_code, 400)
        page = c.get("/login").data.decode()
        token = page.split('name="csrf" value="')[1].split('"')[0]
        r = c.post("/login", data={"email": "admin@ui.edu.ng", "password": "ChangeMe123", "csrf": token})
        self.assertEqual(r.status_code, 302)

    def test_no_delete_routes_for_records(self):
        rules = {r.rule for r in self.app.url_map.iter_rules()}
        self.assertFalse([r for r in rules if "delete" in r])

    def test_open_redirect_blocked(self):
        c = self.app.test_client()
        r = c.post("/login?next=//evil.example", data={"email": "admin@ui.edu.ng", "password": "ChangeMe123"})
        self.assertNotIn("evil", r.headers["Location"])


class TestReportsAndAdmin(Base):
    def test_reports_and_csv(self):
        self.paid_participant()
        adm = self.admin()
        self.assertIn(b"5,000.00", adm.get("/reports?event=1").data)
        r = adm.get("/reports/1/registrations.csv")
        self.assertEqual(r.mimetype, "text/csv")
        self.assertIn(b"Okoro", r.data)
        self.assertIn(b"RC-", adm.get("/reports/1/payments.csv").data)

    def test_dashboard_and_past_events(self):
        self.paid_participant()
        adm = self.admin()
        self.assertEqual(adm.get("/admin?event=1").status_code, 200)
        adm.post("/admin/events/1/update", data={"title": "UI Computing and Technology Conference 2026",
                 "start_date": "2026-11-12", "end_date": "2026-11-13", "registration_fee": "5000", "status": "Archived"})
        self.assertIn(b"UI Computing", adm.get("/admin/past").data)       # archived events appear under past events

    def test_event_validation(self):
        adm = self.admin()
        r = adm.post("/admin/events", data={"title": "X", "start_date": "2026-12-02", "end_date": "2026-12-01",
                     "registration_fee": "0", "status": "Draft"}, follow_redirects=True)
        self.assertIn(b"end cannot be before", r.data)
        r = adm.post("/admin/events", data={"title": "Y", "start_date": "2026-12-01", "end_date": "2026-12-02",
                     "registration_fee": "1000", "min_sessions": "2", "status": "Draft"}, follow_redirects=True)
        self.assertIn(b"Add sessions", r.data)

    def test_draft_event_hidden_from_public(self):
        self.admin().post("/admin/events", data={"title": "Secret", "start_date": "2026-12-01", "end_date": "2026-12-02",
                          "registration_fee": "0", "status": "Draft"})
        self.assertNotIn(b"Secret", self.app.test_client().get("/").data)
        self.assertEqual(self.app.test_client().get("/event/3").status_code, 404)

    def test_user_management_and_role_change(self):
        adm = self.admin()
        adm.post("/admin/users", data={"full_name": "New Staff", "email": "ns@ui.edu.ng", "password": PW, "role": "Event Staff"})
        self.assertEqual(self.login("ns@ui.edu.ng").get("/staff/checkin").status_code, 200)
        adm.post("/admin/users", data={"full_name": "Short", "email": "s@ui.edu.ng", "password": "abc", "role": "Event Staff"})
        self.assertEqual(self.sql("SELECT * FROM user WHERE email='s@ui.edu.ng'"), [])

    def test_venue_and_speaker_management(self):
        adm = self.admin()
        adm.post("/admin/venues", data={"name": "Hall X", "location": "UI", "capacity": "50"})
        adm.post("/admin/speakers", data={"name": "Dr New", "role_type": "Guest", "biodata": "CTO"})
        self.assertEqual(len(self.sql("SELECT * FROM venue")), 3)
        self.assertEqual(len(self.sql("SELECT * FROM speaker")), 3)

    def test_extra_field_added_to_form(self):
        self.admin().post("/admin/events/2/fields", data={"field_name": "Genotype"})
        c = self.signup("g@x.com")
        self.assertIn(b"Genotype", c.get("/event/2/register").data)


if __name__ == "__main__":
    unittest.main()
