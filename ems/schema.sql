-- Event Management System: database schema (18 entities, see Table 3.9 to 3.26)
PRAGMA foreign_keys = ON;

CREATE TABLE role (
  role_id   INTEGER PRIMARY KEY,
  role_name TEXT NOT NULL UNIQUE
);

CREATE TABLE user (
  user_id       INTEGER PRIMARY KEY,
  role_id       INTEGER NOT NULL REFERENCES role(role_id),
  full_name     TEXT NOT NULL,
  email         TEXT NOT NULL UNIQUE,            -- BR1
  password_hash TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'Active' CHECK (status IN ('Active','Disabled'))
);

CREATE TABLE audit_log (
  log_id       INTEGER PRIMARY KEY,
  user_id      INTEGER NOT NULL REFERENCES user(user_id),
  action       TEXT NOT NULL,
  target_table TEXT NOT NULL,
  target_id    INTEGER NOT NULL,
  logged_at    TEXT NOT NULL DEFAULT (datetime('now','+1 hour'))
);

CREATE TABLE participant (
  participant_id    INTEGER PRIMARY KEY,
  user_id           INTEGER UNIQUE REFERENCES user(user_id),
  surname           TEXT NOT NULL,
  other_names       TEXT NOT NULL,
  gender            TEXT NOT NULL,
  organisation      TEXT NOT NULL,
  level_cadre       TEXT NOT NULL,
  membership_status TEXT NOT NULL,
  email             TEXT NOT NULL UNIQUE,
  phone             TEXT NOT NULL
);
CREATE INDEX idx_participant_name ON participant(surname, other_names);   -- NFR1

CREATE TABLE event (
  event_id                     INTEGER PRIMARY KEY,
  title                        TEXT NOT NULL,
  description                  TEXT,
  start_date                   TEXT NOT NULL,
  end_date                     TEXT NOT NULL,
  registration_fee             NUMERIC NOT NULL DEFAULT 0 CHECK (registration_fee >= 0),
  min_sessions_for_certificate INTEGER,
  status                       TEXT NOT NULL DEFAULT 'Draft' CHECK (status IN ('Draft','Open','Closed','Archived'))
);

CREATE TABLE event_field (
  field_id    INTEGER PRIMARY KEY,
  event_id    INTEGER NOT NULL REFERENCES event(event_id),
  field_name  TEXT NOT NULL,
  is_required INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE registration (
  registration_id  INTEGER PRIMARY KEY,
  participant_id   INTEGER NOT NULL REFERENCES participant(participant_id),
  event_id         INTEGER NOT NULL REFERENCES event(event_id),
  registration_ref TEXT NOT NULL UNIQUE,         -- BR1
  purpose          TEXT,
  expected_days    INTEGER,
  status           TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending','Confirmed','Cancelled')),
  registered_by    INTEGER REFERENCES user(user_id),
  registered_at    TEXT NOT NULL DEFAULT (datetime('now','+1 hour')),
  UNIQUE (participant_id, event_id)              -- BR2
);
CREATE INDEX idx_registration_event ON registration(event_id);

CREATE TABLE field_response (
  response_id     INTEGER PRIMARY KEY,
  registration_id INTEGER NOT NULL REFERENCES registration(registration_id),
  field_id        INTEGER NOT NULL REFERENCES event_field(field_id),
  value           TEXT
);

CREATE TABLE payment (
  payment_id      INTEGER PRIMARY KEY,
  registration_id INTEGER NOT NULL REFERENCES registration(registration_id),
  amount          NUMERIC NOT NULL,
  method          TEXT NOT NULL CHECK (method IN ('Online','Bank transfer','Cash')),
  reference       TEXT,
  evidence        TEXT,
  status          TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending','Verified','Failed','Waived')),  -- BR4
  recorded_by     INTEGER REFERENCES user(user_id),
  verified_by     INTEGER REFERENCES user(user_id),
  verified_at     TEXT,
  note            TEXT
);

CREATE TABLE receipt (
  receipt_id INTEGER PRIMARY KEY,
  payment_id INTEGER NOT NULL UNIQUE REFERENCES payment(payment_id),
  receipt_no TEXT NOT NULL UNIQUE,
  issued_at  TEXT NOT NULL DEFAULT (datetime('now','+1 hour'))
);

CREATE TABLE venue (
  venue_id INTEGER PRIMARY KEY,
  name     TEXT NOT NULL,
  location TEXT,
  capacity INTEGER
);

CREATE TABLE session (
  session_id   INTEGER PRIMARY KEY,
  event_id     INTEGER NOT NULL REFERENCES event(event_id),
  venue_id     INTEGER NOT NULL REFERENCES venue(venue_id),
  title        TEXT NOT NULL,
  session_date TEXT NOT NULL,
  start_time   TEXT NOT NULL,
  end_time     TEXT NOT NULL
);

CREATE TABLE speaker (
  speaker_id INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  role_type  TEXT NOT NULL CHECK (role_type IN ('Speaker','Guest')),
  biodata    TEXT,
  biography  TEXT,
  email      TEXT,
  phone      TEXT
);

CREATE TABLE session_speaker (
  session_id INTEGER NOT NULL REFERENCES session(session_id),
  speaker_id INTEGER NOT NULL REFERENCES speaker(speaker_id),
  PRIMARY KEY (session_id, speaker_id)
);

CREATE TABLE attendance (
  attendance_id   INTEGER PRIMARY KEY,
  registration_id INTEGER NOT NULL REFERENCES registration(registration_id),
  session_id      INTEGER NOT NULL REFERENCES session(session_id),
  checked_in_at   TEXT NOT NULL DEFAULT (datetime('now','+1 hour')),
  method          TEXT NOT NULL CHECK (method IN ('Name search','Reference','QR code')),
  recorded_by     INTEGER NOT NULL REFERENCES user(user_id),
  UNIQUE (registration_id, session_id)           -- BR7
);

CREATE TABLE certificate (
  certificate_id  INTEGER PRIMARY KEY,
  registration_id INTEGER NOT NULL UNIQUE REFERENCES registration(registration_id),  -- BR8
  certificate_no  TEXT NOT NULL UNIQUE,
  status          TEXT NOT NULL DEFAULT 'Issued',
  issued_at       TEXT NOT NULL DEFAULT (datetime('now','+1 hour'))
);

CREATE TABLE notification (
  notification_id INTEGER PRIMARY KEY,
  event_id        INTEGER NOT NULL REFERENCES event(event_id),
  subject         TEXT NOT NULL,
  message         TEXT NOT NULL,
  channel         TEXT NOT NULL CHECK (channel IN ('Email','SMS')),
  sent_by         INTEGER NOT NULL REFERENCES user(user_id),
  sent_at         TEXT NOT NULL DEFAULT (datetime('now','+1 hour'))
);

CREATE TABLE notification_recipient (
  recipient_id    INTEGER PRIMARY KEY,
  notification_id INTEGER NOT NULL REFERENCES notification(notification_id),
  participant_id  INTEGER REFERENCES participant(participant_id),
  speaker_id      INTEGER REFERENCES speaker(speaker_id),
  delivery_status TEXT NOT NULL DEFAULT 'Pending' CHECK (delivery_status IN ('Pending','Sent','Failed'))
);
