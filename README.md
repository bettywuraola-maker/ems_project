# Event Management System for a Conference or Convention
Case study: University of Ibadan. CSC 302 group project (implementation of the design in Chapters 1 to 3).

A web application with the three layers from Section 3.1:

| Layer | Where | Technology |
|---|---|---|
| Presentation | `ems/templates/`, `ems/static/style.css` | HTML and CSS, no JavaScript framework, no external fonts or images (light pages, NFR5) |
| Application | `ems/views/` (pages and permissions) and `ems/rules.py` (business rules BR1 to BR14) | Python 3 and Flask |
| Data | `ems/schema.sql` (18 tables, Tables 3.9 to 3.26) and `ems/db.py` | SQLite (the same SQL runs on MySQL or PostgreSQL with minor type changes) |

## Run it

```bash
pip install -r requirements.txt        # flask and reportlab
python manage.py init                  # creates ems.sqlite, the 4 roles and the first administrator
python manage.py demo                  # optional: demo users, venues, speakers and two events
python manage.py run                   # open http://127.0.0.1:5000
```

Set `EMS_SECRET_KEY` to a long random value before any real use. To start fresh, delete `ems.sqlite` and run `init` again.

### Demo accounts (after `python manage.py demo`)

| Role | Email | Password |
|---|---|---|
| Administrator | admin@ui.edu.ng | ChangeMe123 (change it) |
| Event Staff | staff@ui.edu.ng | Password123 |
| Finance Officer | finance@ui.edu.ng | Password123 |
| Participant | ada@example.com | Password123 (or create your own account) |

## Try the main flow
1. **Participant:** log in, open the conference, register. The registration is *Pending* with a reference such as `UI01-K7M2QX`.
2. **Participant:** on *My registrations*, submit a bank-transfer reference.
3. **Finance Officer:** *Payments*, then Verify. The registration becomes *Confirmed* and a receipt PDF is issued.
4. **Event Staff:** *Check-in*, choose the session, search by name or reference, Check in. Use *Print the participant list* as the offline fallback.
5. **Administrator:** open the event, Generate certificates. Eligible participants download a PDF certificate.
6. **Administrator:** Dashboard, Reports (CSV exports), Past events and Audit trail.

## Where each module of Table 3.1 lives

| Module | Code |
|---|---|
| Authentication and role-based access | `ems/__init__.py` (`roles_required`), `views/public.py` (login, signup) |
| Registration and participant management | `rules.register`, `views/public.py`, `views/ops.py` (search, staff-assisted registration) |
| Payment management | `rules.submit_payment / record_payment / verify_payment / fail_payment / waive_fee`, `views/ops.py` (finance) |
| Attendance management | `rules.check_in`, `views/ops.py` (check-in, printable list) |
| Event and programme management | `views/admin.py` (events, sessions, extra fields) |
| Speaker, guest and venue management | `views/admin.py`, `rules.venue_conflict / speaker_conflict` |
| Certificate management | `rules.eligibility / generate_certificates`, PDF in `views/public.py` |
| Notification and communication | `rules.notify` (delivery is a stub, see below), `views/admin.py` (announce) |
| Reporting and event records | `views/admin.py` (reports, CSV, past events) |
| Audit trail | `rules.audit`, `views/admin.py` (audit) |

## Business rules in the code

| Rule | Enforced by |
|---|---|
| BR1 one account per email, unique reference | `user.email UNIQUE`, `registration.registration_ref UNIQUE`, `rules.new_reference` |
| BR2 one registration per participant per event | `UNIQUE(participant_id, event_id)` and a friendly check in `rules.register` |
| BR3 Pending until Verified or Waived (or no fee) | `rules.sync_status` |
| BR4 four payment statuses, receipt only when Verified | `payment.status CHECK`, `rules.verify_payment` |
| BR5 transfers verified by Finance | `@roles_required("Finance Officer")` on verify and reject |
| BR6 cash recorded with a receipt number | `rules.record_payment` (cash is verified and receipted at once) |
| BR7 only Confirmed registrations check in, once per session, rejections logged | `rules.check_in`, `UNIQUE(registration_id, session_id)` |
| BR8 certificate eligibility, one per registration, unique number | `rules.eligibility`, `certificate.registration_id UNIQUE` |
| BR9 venue overlap blocked, capacity warning | `rules.venue_conflict`, `rules.validate_session` |
| BR10 speaker overlap blocked | `rules.speaker_conflict` |
| BR11 schedule change notifies participants | `rules.notify_schedule_change` |
| BR12 audit log | `rules.audit` |
| BR13 archive, never delete | no delete routes for records; accounts are disabled; registrations are cancelled |
| BR14 cancelled registrations cannot check in, pay or get a certificate | `rules.check_in`, `rules._reg_for_payment`, `rules.eligibility` |

## Decisions where the interviews were silent (write these up in Chapter 4)
- **Free events:** BR8 says "payment Verified or Waived". For an event with no fee there is no payment, so a Confirmed registration counts as settled.
- **Cash:** recorded by the Finance Officer, so it is verified and receipted immediately. Bank transfers stay Pending until verified.
- **Online payment gateway:** not integrated (marked provisional in the design). Participants pay by bank transfer and submit the reference; the design allows a gateway (Paystack or Flutterwave) to be added in `rules.submit_payment`.
- **Email and SMS:** messages are stored and shown in the participant's Notifications page. `rules.deliver()` is the single place to connect a real email or SMS provider.
- **Out of scope (as in Section 2.4):** abstract review, accommodation, virtual delivery, sponsors, native apps, refunds. Participant networking (UC7) is provisional and not built.
- **Staff-assisted registration** creates a participant profile without an account; if that person later signs up with the same email, the profile is linked and no duplicate is made.

## Tests
```bash
python -m unittest discover -s tests -v
```
50 automated tests cover BR1 to BR14, the role and permission matrix (Table 3.28), validation, PDF access, CSRF and reports. The slowest part is password hashing, so the run takes about 40 seconds.

## Security notes
Passwords are hashed (Werkzeug scrypt); every POST needs a CSRF token; all input is validated on the server; roles are enforced on every route; uploads are limited to PNG, JPG or PDF under 2 MB. For deployment serve it over HTTPS behind a production server (for example `gunicorn "ems:create_app()"`), set `EMS_SECRET_KEY`, and schedule backups of `ems.sqlite` (NFR8).
