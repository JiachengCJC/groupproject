"""Password hashing, database-backed sessions and small-team membership.

Session tokens are random, stored hashed, and checked against active users on
all requests. Credentials are never returned by the API.
"""
import hashlib
import hmac
import re
import secrets
import time

from .database import now, uid

ROLES = {"admin", "analyst", "reviewer", "viewer"}
COOKIE = "ml_session"


def password_hash(password):
    if not 12 <= len(password) <= 128:
        raise ValueError("Use a password of 12–128 characters.")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, maxmem=64*1024*1024, dklen=32)
    return "scrypt$16384$8$1$" + salt.hex() + "$" + derived.hex()


def password_matches(password, encoded):
    try:
        method, n, r, p, salt, expected = encoded.split("$")
        if method != "scrypt" or int(n) != 16384 or int(r) != 8 or int(p) != 1 or len(password) > 128:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), maxmem=64*1024*1024, dklen=32)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def email_address(email):
    email = email.strip().lower()
    if len(email)>254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email):
        raise ValueError("Enter a valid email address.")
    return email


def public_user(user):
    return {key:user[key] for key in ("id","email","name","role","workspace_id")}


class Auth:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        # Same password work for unknown users prevents obvious timing enumeration.
        self._dummy_hash = password_hash(secrets.token_urlsafe(24))

    def setup_required(self):
        with self.db.connect() as con:
            return con.execute("SELECT id FROM users LIMIT 1").fetchone() is None

    def bootstrap(self):
        if not self.settings.auth_enabled or not self.settings.admin_email or not self.setup_required():
            return
        if not self.settings.admin_password:
            raise ValueError("ADMIN_PASSWORD is required when ADMIN_EMAIL is configured.")
        self.create_user(self.settings.admin_email,self.settings.admin_password,"Administrator","admin",setup=True)

    def create_user(self,email,password,name,role="analyst",*,setup=False,workspace_id=None):
        email=email_address(email)
        if role not in ROLES: raise ValueError("Unknown role.")
        if not 2 <= len(name.strip()) <= 100: raise ValueError("Name must contain 2–100 characters.")
        encoded=password_hash(password); user_id=uid("user")
        workspace_id=workspace_id or self.db.workspace_id
        with self.db.connect() as con:
            self.db._lock(con)
            if self.db.dialect=="postgresql":con.execute("SELECT pg_advisory_xact_lock(21112027)")
            if setup and con.execute("SELECT id FROM users LIMIT 1").fetchone():raise ValueError("Initial setup has already been completed.")
            if con.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone():raise ValueError("That email address is already in use.")
            con.execute("INSERT INTO workspaces(id,name,created_at) VALUES (?,?,?) ON CONFLICT(id) DO NOTHING",(workspace_id,"Market Lens",now()))
            con.execute("INSERT INTO users(id,email,name,password_hash,role,workspace_id,created_at) VALUES (?,?,?,?,?,?,?)",(user_id,email,name.strip(),encoded,role,workspace_id,now()))
        return {"id":user_id,"email":email,"name":name.strip(),"role":role,"workspace_id":workspace_id}

    def users(self):
        with self.db.connect() as con:
            return [public_user(r)|{"disabled":bool(r["disabled"])} for r in con.execute("SELECT * FROM users WHERE workspace_id=? ORDER BY created_at",(self.db.workspace_id,))]

    def update_user(self,user_id,role=None,disabled=None,password=None):
        encoded=password_hash(password) if password is not None else None
        with self.db.connect() as con:
            self.db._lock(con)
            if self.db.dialect=="postgresql":con.execute("SELECT pg_advisory_xact_lock(21112027)")
            row=con.execute("SELECT * FROM users WHERE id=? AND workspace_id=?",(user_id,self.db.workspace_id)).fetchone()
            if not row:raise ValueError("User not found.")
            role=role or row["role"]
            disabled=bool(row["disabled"]) if disabled is None else disabled
            if role not in ROLES:raise ValueError("Unknown role.")
            if row["role"]=="admin" and not row["disabled"] and (role!="admin" or disabled):
                admins=con.execute("SELECT COUNT(*) AS n FROM users WHERE workspace_id=? AND role='admin' AND disabled=0",(self.db.workspace_id,)).fetchone()["n"]
                if admins<=1:raise ValueError("The last active administrator cannot be removed.")
            con.execute("UPDATE users SET role=?,disabled=? WHERE id=?",(role,int(disabled),user_id))
            if encoded:con.execute("UPDATE users SET password_hash=? WHERE id=?",(encoded,user_id))
            con.execute("DELETE FROM sessions WHERE user_id=?",(user_id,))
            if disabled or role not in {"admin","analyst"}:
                con.execute("UPDATE tasks SET status='cancelled',lease_token=NULL,updated_at=? WHERE owner_id=? AND workspace_id=? AND status IN ('queued','running')",(now(),user_id,self.db.workspace_id))
                con.execute("UPDATE runs SET status='cancelled',error='Cancelled because the submitting account lost execution access.' WHERE owner_id=? AND workspace_id=? AND status IN ('queued','running')",(user_id,self.db.workspace_id))
        return {"id":user_id,"role":role,"disabled":disabled,"password_reset":encoded is not None}

    def _throttle(self,key):
        key_hash=hashlib.sha256(key.encode()).hexdigest();stamp=time.time()
        with self.db.connect() as con:
            self.db._lock(con)
            if self.db.dialect=="postgresql":con.execute("SELECT pg_advisory_xact_lock(21112028)")
            row=con.execute("SELECT * FROM auth_attempts WHERE key_hash=?",(key_hash,)).fetchone()
            attempts=row["attempts"] if row and row["reset_at"]>stamp else 0
            reset_at=row["reset_at"] if row and row["reset_at"]>stamp else stamp+900
            if attempts>=10:return False
            con.execute("INSERT INTO auth_attempts(key_hash,attempts,reset_at) VALUES (?,?,?) ON CONFLICT(key_hash) DO UPDATE SET attempts=excluded.attempts,reset_at=excluded.reset_at",(key_hash,attempts+1,reset_at))
        return True

    def login(self,email,password,client=""):
        email=email_address(email)
        if not self._throttle(client+"|"+email):raise PermissionError("Too many login attempts. Try again after 15 minutes.")
        with self.db.connect() as con:
            row=con.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
        valid=password_matches(password,row["password_hash"] if row else self._dummy_hash)
        if not row or not valid or row["disabled"]:return None
        with self.db.connect() as con:
            con.execute("DELETE FROM auth_attempts WHERE key_hash=?",(hashlib.sha256((client+"|"+email).encode()).hexdigest(),))
        return public_user(row),self.issue_session(row["id"])

    def issue_session(self,user_id):
        token=secrets.token_urlsafe(32)
        with self.db.connect() as con:
            con.execute("DELETE FROM sessions WHERE expires_at<?",(time.time(),))
            con.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES (?,?,?,?)",(hashlib.sha256(token.encode()).hexdigest(),user_id,time.time()+3600*self.settings.session_hours,now()))
        return token

    def session_user(self,token):
        if not token or len(token)>128:return None
        with self.db.connect() as con:
            row=con.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0",(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
        return public_user(row) if row else None

    def logout(self,token):
        if token:
            with self.db.connect() as con:
                con.execute("DELETE FROM sessions WHERE token_hash=?",(hashlib.sha256(token.encode()).hexdigest(),))
