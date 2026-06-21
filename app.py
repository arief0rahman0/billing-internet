import os
import sqlite3
import urllib.parse
from datetime import datetime
from functools import wraps

import pyotp
import requests
from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash

from werkzeug.middleware.proxy_fix import ProxyFix

# =============================================================================
# INISIALISASI APLIKASI
# =============================================================================
app = Flask(__name__)
# Amankan pembacaan IP Client jika aplikasi berjalan di balik Nginx / Reverse Proxy
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
csrf = CSRFProtect(app)

# SECRET_KEY: Wajib diset via environment variable di produksi.
# Gunakan: export SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise RuntimeError(
        "SECRET_KEY environment variable belum diset! "
        'Jalankan: export SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")'
        " lalu tambahkan ke /etc/systemd/system/billing.service"
    )

# =============================================================================
# KONFIGURASI KEAMANAN SESSION & COOKIE
# =============================================================================
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,  # JS tidak bisa akses cookie session
    SESSION_COOKIE_SAMESITE="Lax",  # Proteksi CSRF dasar
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV")
    == "production",  # Otomatis Secure di production
    PERMANENT_SESSION_LIFETIME=3600,  # Session expired setelah 1 jam idle
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # Max upload: 10 MB
)


@app.after_request
def add_security_headers(response):
    """Tambahkan security headers ke setiap response HTTP."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    response.headers["Server"] = "Secure Server"
    # Content-Security-Policy: izinkan CDN yang digunakan
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https://api.qrserver.com; "
        "connect-src 'self';"
    )
    return response


# =============================================================================
# RATE LIMITER — Proteksi Brute Force
# =============================================================================
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["300 per day", "60 per hour"],
    storage_uri="memory://",
)


# =============================================================================
# HELPER: KIRIM WHATSAPP VIA BOT LOKAL (PORT 3000)
# =============================================================================
def send_whatsapp(no_hp, pesan):
    """Kirim pesan WA via bot lokal Baileys. Return True jika sukses."""
    if not no_hp:
        return False
    url = "http://127.0.0.1:3000/send"
    data = {"target": no_hp, "message": pesan}
    try:
        response = requests.post(url, json=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"[WA] Gagal kirim WhatsApp ke {no_hp}: {e}")
        return False


# =============================================================================
# DATABASE
# =============================================================================
def get_db_connection():
    """Buka koneksi SQLite. Selalu tutup setelah selesai."""
    db_path = app.config.get("DATABASE", "pembayaran_internet.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # Aktifkan foreign key constraint
    return conn


def init_db():
    """Inisialisasi schema database dan buat akun default jika belum ada."""
    conn = get_db_connection()

    # Tabel admin_user
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'admin',
            totp_secret TEXT DEFAULT ''
        )
    """)
    # Migrasi kolom legacy jika belum ada
    for col_def in [
        "ALTER TABLE admin_user ADD COLUMN role TEXT DEFAULT 'admin'",
        "ALTER TABLE admin_user ADD COLUMN totp_secret TEXT DEFAULT ''",
    ]:
        try:
            conn.execute(col_def)
        except sqlite3.OperationalError:
            pass

    # Akun admin default (WAJIB ganti password setelah deploy pertama)
    if not conn.execute(
        "SELECT id FROM admin_user WHERE username = ?", ("admin",)
    ).fetchone():
        conn.execute(
            "INSERT INTO admin_user (username, password, role) VALUES (?, ?, ?)",
            ("admin", generate_password_hash("admin123"), "admin"),
        )

    # Akun operator default
    if not conn.execute(
        "SELECT id FROM admin_user WHERE username = ?", ("operator",)
    ).fetchone():
        conn.execute(
            "INSERT INTO admin_user (username, password, role) VALUES (?, ?, ?)",
            ("operator", generate_password_hash("operator123"), "operator"),
        )

    # Tabel pelanggan
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pelanggan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT NOT NULL,
            tagihan_bulanan INTEGER NOT NULL
        )
    """)
    try:
        conn.execute('ALTER TABLE pelanggan ADD COLUMN no_wa TEXT DEFAULT ""')
    except sqlite3.OperationalError:
        pass

    # Tabel pembayaran
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pembayaran (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pelanggan_id INTEGER NOT NULL,
            bulan_tagihan TEXT NOT NULL,
            jumlah_bayar INTEGER NOT NULL,
            tanggal_bayar TEXT,
            status TEXT NOT NULL,
            catatan TEXT DEFAULT '',
            FOREIGN KEY (pelanggan_id) REFERENCES pelanggan (id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()


# =============================================================================
# DEKORATOR AUTH
# =============================================================================
def login_required(f):
    """Redirect ke halaman login jika belum login."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "logged_in" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


def admin_required(f):
    """Tolak akses jika bukan role admin."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("role") != "admin":
            flash("Akses ditolak! Fitur ini hanya untuk akun admin.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)

    return decorated_function


# =============================================================================
# HELPER: GENERATE TAGIHAN OTOMATIS
# =============================================================================
def generate_tagihan_otomatis():
    """Buat tagihan bulan berjalan untuk semua pelanggan yang belum punya tagihan."""
    conn = get_db_connection()
    bulan_sekarang = datetime.now().strftime("%Y-%m")
    semua_pelanggan = conn.execute(
        "SELECT id, tagihan_bulanan FROM pelanggan"
    ).fetchall()
    for p in semua_pelanggan:
        existing = conn.execute(
            "SELECT id FROM pembayaran WHERE pelanggan_id = ? AND bulan_tagihan = ?",
            (p["id"], bulan_sekarang),
        ).fetchone()
        if not existing:
            conn.execute(
                """INSERT INTO pembayaran
                   (pelanggan_id, bulan_tagihan, jumlah_bayar, tanggal_bayar, status, catatan)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (p["id"], bulan_sekarang, p["tagihan_bulanan"], "-", "Belum Bayar", ""),
            )
    conn.commit()
    conn.close()


# =============================================================================
# ROUTES: AUTH
# =============================================================================
@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    """Halaman login dengan validasi password + TOTP 2FA."""
    if "logged_in" in session:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        totp_code = request.form.get("totp_code", "").strip()

        if not username or not password:
            error = "Username dan password harus diisi."
        else:
            conn = get_db_connection()
            user = conn.execute(
                "SELECT * FROM admin_user WHERE username = ?", (username,)
            ).fetchone()
            conn.close()

            if user and check_password_hash(user["password"], password):
                if user["totp_secret"]:
                    totp = pyotp.TOTP(user["totp_secret"])
                    if totp.verify(totp_code):
                        session.permanent = True
                        session["logged_in"] = True
                        session["username"] = user["username"]
                        session["role"] = user["role"]
                        return redirect(url_for("index"))
                    else:
                        error = "Kode Keamanan TOTP (6 Digit) Salah atau Kedaluwarsa!"
                else:
                    # 2FA belum aktif — ijinkan login langsung
                    session.permanent = True
                    session["logged_in"] = True
                    session["username"] = user["username"]
                    session["role"] = user["role"]
                    return redirect(url_for("index"))
            else:
                error = "Username atau Password salah!"

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    """Hapus semua session dan redirect ke login."""
    session.clear()
    return redirect(url_for("login"))


# =============================================================================
# ROUTES: DASHBOARD UTAMA
# =============================================================================
@app.route("/")
@login_required
def index():
    """Dashboard utama: daftar pembayaran, statistik, chart."""
    generate_tagihan_otomatis()
    search_query = request.args.get("search", "").strip()
    bulan_sekarang = datetime.now().strftime("%Y-%m")

    conn = get_db_connection()

    total_pemasukan = conn.execute(
        "SELECT COALESCE(SUM(jumlah_bayar), 0) FROM pembayaran WHERE status = 'Lunas'"
    ).fetchone()[0]

    pemasukan_bulan_ini = conn.execute(
        "SELECT COALESCE(SUM(jumlah_bayar), 0) FROM pembayaran WHERE status = 'Lunas' AND bulan_tagihan = ?",
        (bulan_sekarang,),
    ).fetchone()[0]

    piutang_bulan_ini = conn.execute(
        "SELECT COALESCE(SUM(jumlah_bayar), 0) FROM pembayaran WHERE status = 'Belum Bayar' AND bulan_tagihan = ?",
        (bulan_sekarang,),
    ).fetchone()[0]

    rekap_bulanan = conn.execute("""
        SELECT bulan_tagihan, COALESCE(SUM(jumlah_bayar), 0) AS total
        FROM pembayaran WHERE status = 'Lunas'
        GROUP BY bulan_tagihan ORDER BY bulan_tagihan DESC
    """).fetchall()

    chart_raw = conn.execute("""
        SELECT bulan_tagihan, COALESCE(SUM(jumlah_bayar), 0) AS total
        FROM pembayaran WHERE status = 'Lunas'
        GROUP BY bulan_tagihan ORDER BY bulan_tagihan ASC LIMIT 12
    """).fetchall()
    chart_labels = [row["bulan_tagihan"] for row in chart_raw]
    chart_data = [row["total"] for row in chart_raw]

    base_query = """
        SELECT pembayaran.id, pelanggan.id AS pelanggan_id,
               pelanggan.nama AS nama_pelanggan, pelanggan.no_wa,
               pembayaran.bulan_tagihan, pembayaran.jumlah_bayar,
               pembayaran.tanggal_bayar, pembayaran.status, pembayaran.catatan
        FROM pembayaran
        JOIN pelanggan ON pembayaran.pelanggan_id = pelanggan.id
    """
    if search_query:
        data_pembayaran = conn.execute(
            base_query
            + " WHERE pelanggan.nama LIKE ? ORDER BY pembayaran.bulan_tagihan DESC, pembayaran.id DESC",
            (f"%{search_query}%",),
        ).fetchall()
    else:
        data_pembayaran = conn.execute(
            base_query + " ORDER BY pembayaran.bulan_tagihan DESC, pembayaran.id DESC"
        ).fetchall()

    daftar_pelanggan = conn.execute(
        "SELECT * FROM pelanggan ORDER BY nama ASC"
    ).fetchall()
    conn.close()

    return render_template(
        "index.html",
        data=data_pembayaran,
        pelanggan=daftar_pelanggan,
        search_query=search_query,
        total_pemasukan=total_pemasukan,
        pemasukan_bulan_ini=pemasukan_bulan_ini,
        piutang_bulan_ini=piutang_bulan_ini,
        rekap_bulanan=rekap_bulanan,
        chart_labels=chart_labels,
        chart_data=chart_data,
    )


# =============================================================================
# ROUTES: MANAJEMEN PELANGGAN
# =============================================================================
@app.route("/tambah_pelanggan", methods=["POST"])
@login_required
def tambah_pelanggan():
    """Tambah pelanggan baru dan buat tagihan bulan berjalan."""
    nama = request.form.get("nama", "").strip()
    tagihan_raw = request.form.get("tagihan", "0").strip()
    no_wa = request.form.get("no_wa", "").strip()

    # Validasi input
    if not nama:
        flash("Nama pelanggan tidak boleh kosong.", "error")
        return redirect(url_for("index"))
    try:
        tagihan = int(tagihan_raw)
        if tagihan <= 0:
            raise ValueError
    except ValueError:
        flash("Tagihan bulanan harus berupa angka positif.", "error")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO pelanggan (nama, tagihan_bulanan, no_wa) VALUES (?, ?, ?)",
        (nama, tagihan, no_wa),
    )
    pelanggan_id = cursor.lastrowid
    bulan_sekarang = datetime.now().strftime("%Y-%m")
    conn.execute(
        """INSERT INTO pembayaran
           (pelanggan_id, bulan_tagihan, jumlah_bayar, tanggal_bayar, status, catatan)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (pelanggan_id, bulan_sekarang, tagihan, "-", "Belum Bayar", ""),
    )
    conn.commit()
    conn.close()
    flash("Anggota berhasil disimpan.", "success")
    return redirect(url_for("index"))


@app.route("/edit_pelanggan/<int:id>", methods=["GET", "POST"])
@login_required
def edit_pelanggan(id):
    """Edit data pelanggan (nama, tagihan, nomor WA)."""
    conn = get_db_connection()
    pelanggan = conn.execute("SELECT * FROM pelanggan WHERE id = ?", (id,)).fetchone()

    if not pelanggan:
        conn.close()
        return "Pelanggan tidak ditemukan.", 404

    if request.method == "POST":
        nama_baru = request.form.get("nama", "").strip()
        tagihan_raw = request.form.get("tagihan", "0").strip()
        no_wa_baru = request.form.get("no_wa", "").strip()

        # Validasi
        if not nama_baru:
            conn.close()
            flash("Nama tidak boleh kosong.", "error")
            return redirect(url_for("edit_pelanggan", id=id))
        try:
            tagihan_baru = int(tagihan_raw)
            if tagihan_baru <= 0:
                raise ValueError
        except ValueError:
            conn.close()
            flash("Tagihan harus berupa angka positif.", "error")
            return redirect(url_for("edit_pelanggan", id=id))

        conn.execute(
            "UPDATE pelanggan SET nama = ?, tagihan_bulanan = ?, no_wa = ? WHERE id = ?",
            (nama_baru, tagihan_baru, no_wa_baru, id),
        )
        conn.execute(
            "UPDATE pembayaran SET jumlah_bayar = ? WHERE pelanggan_id = ? AND status = 'Belum Bayar'",
            (tagihan_baru, id),
        )
        conn.commit()
        conn.close()
        flash("Perubahan data berhasil disimpan.", "success")
        return redirect(url_for("index"))

    conn.close()
    return render_template("edit_pelanggan.html", p=pelanggan)


@app.route("/hapus_pelanggan/<int:id>", methods=["POST"])
@login_required
@admin_required
def hapus_pelanggan(id):
    """Hapus pelanggan beserta semua data pembayarannya (CASCADE)."""
    conn = get_db_connection()
    conn.execute("DELETE FROM pelanggan WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    flash("Pelanggan berhasil dihapus.", "success")
    return redirect(url_for("index"))


# =============================================================================
# ROUTES: PEMBAYARAN
# =============================================================================
@app.route("/update_catatan/<int:id>", methods=["POST"])
@login_required
def update_catatan(id):
    """Update field catatan pada record pembayaran."""
    catatan_baru = request.form.get("catatan", "").strip()
    conn = get_db_connection()
    conn.execute("UPDATE pembayaran SET catatan = ? WHERE id = ?", (catatan_baru, id))
    conn.commit()
    conn.close()
    flash("Catatan berhasil disimpan.", "success")
    return redirect(url_for("index"))


@app.route("/lunas/<int:id>", methods=["POST"])
@login_required
def set_lunas(id):
    """Set 1-6 tagihan paling lama menjadi Lunas untuk pelanggan terkait."""
    months = request.args.get("months", default=1, type=int)
    months = max(1, min(6, months))  # Clamp antara 1-6

    tanggal_sekarang = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db_connection()

    # Verifikasi payment ID milik user yang valid
    payment = conn.execute(
        "SELECT pelanggan_id FROM pembayaran WHERE id = ?", (id,)
    ).fetchone()

    if not payment:
        conn.close()
        flash("Data pembayaran tidak ditemukan.", "error")
        return redirect(url_for("index"))

    pelanggan_id = payment["pelanggan_id"]
    unpaid_payments = conn.execute(
        "SELECT id FROM pembayaran WHERE pelanggan_id = ? AND status = 'Belum Bayar' ORDER BY bulan_tagihan ASC LIMIT ?",
        (pelanggan_id, months),
    ).fetchall()

    payment_ids = [p["id"] for p in unpaid_payments]
    if payment_ids:
        placeholders = ",".join(["?"] * len(payment_ids))
        conn.execute(
            f"UPDATE pembayaran SET status = ?, tanggal_bayar = ? WHERE id IN ({placeholders})",
            ("Lunas", tanggal_sekarang, *payment_ids),
        )
        conn.commit()

    conn.close()
    flash(f"Pembayaran {len(payment_ids)} bulan berhasil diupdate ke Lunas.", "success")
    return redirect(url_for("index"))


# =============================================================================
# ROUTES: NOTA
# =============================================================================
@app.route("/nota/<int:id>")
@login_required
def cetak_nota(id):
    """Cetak nota digital untuk pembayaran yang sudah Lunas."""
    conn = get_db_connection()
    nota = conn.execute(
        """
        SELECT pembayaran.id, pelanggan.nama AS nama_pelanggan,
               pembayaran.bulan_tagihan, pembayaran.jumlah_bayar,
               pembayaran.tanggal_bayar, pembayaran.status, pembayaran.catatan
        FROM pembayaran
        JOIN pelanggan ON pembayaran.pelanggan_id = pelanggan.id
        WHERE pembayaran.id = ?
    """,
        (id,),
    ).fetchone()
    conn.close()

    if nota and nota["status"] == "Lunas":
        return render_template("nota.html", nota=nota)
    return "Nota tidak ditemukan atau tagihan belum dilunasi.", 404


# =============================================================================
# ROUTES: KIRIM WHATSAPP
# =============================================================================
@app.route("/kirim_wa_lunas/<int:id>", methods=["POST"])
@login_required
def kirim_wa_lunas(id):
    """Kirim pesan kuitansi/nota WA ke pelanggan yang sudah Lunas."""
    conn = get_db_connection()
    info = conn.execute(
        """
        SELECT pelanggan.nama, pelanggan.no_wa,
               pembayaran.bulan_tagihan, pembayaran.jumlah_bayar, pembayaran.tanggal_bayar
        FROM pembayaran
        JOIN pelanggan ON pembayaran.pelanggan_id = pelanggan.id
        WHERE pembayaran.id = ?
    """,
        (id,),
    ).fetchone()
    conn.close()

    if info and info["no_wa"]:
        link_nota = f"{request.host_url}nota/{id}"
        pesan_wa = (
            f"🟢 *BUKTI PEMBAYARAN INTERNET LUNAS*\n\n"
            f"Yth. Bapak/Ibu *{info['nama']}*,\n"
            f"Terima kasih, pembayaran tagihan internet Anda telah kami terima.\n\n"
            f"📦 *Detail Transaksi:*\n"
            f"• Periode Bulan: {info['bulan_tagihan']}\n"
            f"• Jumlah Bayar: Rp {info['jumlah_bayar']:,}\n"
            f"• Waktu Sukses: {info['tanggal_bayar']} WIB\n\n"
            f"Status Tagihan Anda saat ini dinyatakan: *LUNAS/PAID*.\n\n"
            f"📄 *Link Nota Digital Resmi (Bisa Di-download/Print):*\n"
            f"{link_nota}\n\n"
            f"📱 _Pesan ini dikirim oleh sistem Billing Internet._"
        )
        send_whatsapp(info["no_wa"], pesan_wa)

    flash("Nota WA berhasil terkirim.", "success")
    return redirect(url_for("index"))


@app.route("/kirim_wa_pengingat/<int:id>", methods=["POST"])
@login_required
def kirim_wa_pengingat(id):
    """Kirim pesan pengingat tagihan jatuh tempo ke pelanggan."""
    conn = get_db_connection()
    info = conn.execute(
        """
        SELECT pelanggan.nama, pelanggan.no_wa,
               pembayaran.bulan_tagihan, pembayaran.jumlah_bayar
        FROM pembayaran
        JOIN pelanggan ON pembayaran.pelanggan_id = pelanggan.id
        WHERE pembayaran.id = ?
    """,
        (id,),
    ).fetchone()
    conn.close()

    if info and info["no_wa"]:
        pesan_wa = (
            f"⚠️ *PENGINGAT JATUH TEMPO PEMBAYARAN INTERNET*\n\n"
            f"Yth. Bapak/Ibu *{info['nama']}*,\n"
            f"Kami menginfokan bahwa tagihan internet Anda untuk bulan ini sudah terbit.\n\n"
            f"📦 *Detail Tagihan:*\n"
            f"• Nama: {info['nama']}\n"
            f"• Periode Bulan: {info['bulan_tagihan']}\n"
            f"• Total Tagihan: Rp {info['jumlah_bayar']:,}\n"
            f"• Jatuh Tempo: Tanggal 10 Setiap Awal Bulan\n"
            f"• Status: *BELUM DIBAYAR*\n\n"
            f"💳 *Metode Pembayaran:*\n"
            f"Pembayaran dapat dilakukan secara Cash atau Transfer melalui jalur resmi berikut:\n\n"
            f"1. *Cash / Tunai* langsung ke Admin\n"
            f"2. *Transfer Bank BCA*\n"
            f"   • No. Rekening: *0284105318*\n"
            f"   • A.N. Muh. Samsul Maarif\n"
            f"3. *E-Wallet DANA*\n"
            f"   • No. HP: *081542115429*\n"
            f"   • A.N. Muh. Samsul Maarif\n"
            f"4. *QRIS Standar Nasional*\n"
            f"   • A.N. *MUH SAMSUL MAARIF, PULSA & INTERNET*\n"
            f"   _(Barcode QRIS bisa meminta langsung ke Admin / Scan saat penagihan)_\n\n"
            f"Mohon untuk melakukan pembayaran sebelum jatuh tempo agar layanan internet Anda tetap berjalan lancar. Terima kasih.\n\n"
            f"📱 _Pesan ini dikirim otomatis oleh sistem Billing Internet._"
        )
        send_whatsapp(info["no_wa"], pesan_wa)

    flash("Pesan pengingat jatuh tempo berhasil dikirim via WhatsApp.", "success")
    return redirect(url_for("index"))


# =============================================================================
# ROUTES: BACKUP & RESTORE DATABASE
# =============================================================================
ALLOWED_EXTENSIONS = {".db"}


def _is_valid_sqlite(filepath):
    """Cek apakah file adalah database SQLite valid (bukan file berbahaya)."""
    try:
        conn = sqlite3.connect(filepath)
        conn.execute("SELECT name FROM sqlite_master LIMIT 1")
        conn.close()
        return True
    except Exception:
        return False


@app.route("/backup")
@login_required
@admin_required
def backup_page():
    return render_template("backup.html")


@app.route("/backup/download")
@login_required
@admin_required
def backup_database():
    """Download backup database SQLite."""
    try:
        tanggal_hari_ini = datetime.now().strftime("%Y-%m-%d")
        db_path = app.config.get("DATABASE", "pembayaran_internet.db")
        return send_file(
            db_path,
            as_attachment=True,
            download_name=f"backup_billing_{tanggal_hari_ini}.db",
        )
    except Exception as e:
        flash(f"Gagal melakukan backup database: {e}", "error")
        return redirect(url_for("backup_page"))


@app.route("/backup/restore", methods=["POST"])
@login_required
@admin_required
def restore_database():
    """Restore database dari file .db yang diupload. Validasi ketat."""
    if "db_file" not in request.files:
        flash("Tidak ada file yang diunggah.", "error")
        return redirect(url_for("backup_page"))

    file = request.files["db_file"]
    if not file or file.filename == "":
        flash("Tidak ada file yang dipilih.", "error")
        return redirect(url_for("backup_page"))

    # Validasi ekstensi file
    _, ext = os.path.splitext(file.filename)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        flash("Format file tidak valid. Hanya file .db yang diizinkan.", "error")
        return redirect(url_for("backup_page"))

    # Simpan ke temporary file, validasi isi SQLite, baru timpa database
    tmp_path = app.config.get("DATABASE", "pembayaran_internet.db") + ".tmp"
    try:
        file.save(tmp_path)
        if not _is_valid_sqlite(tmp_path):
            os.remove(tmp_path)
            flash("File bukan database SQLite yang valid.", "error")
            return redirect(url_for("backup_page"))

        db_path = app.config.get("DATABASE", "pembayaran_internet.db")
        os.replace(tmp_path, db_path)
        flash("Database berhasil di-restore!", "success")
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        flash(f"Gagal restore database: {e}", "error")

    return redirect(url_for("backup_page"))


# =============================================================================
# ROUTES: SETUP 2FA
# =============================================================================
@app.route("/setup_2fa", methods=["GET", "POST"])
@login_required
def setup_2fa():
    """Setup atau reset TOTP 2FA untuk akun yang sedang login."""
    conn = get_db_connection()
    user = conn.execute(
        "SELECT totp_secret FROM admin_user WHERE username = ?", (session["username"],)
    ).fetchone()

    if request.method == "POST":
        if "reset" in request.form:
            conn.execute(
                "UPDATE admin_user SET totp_secret = '' WHERE username = ?",
                (session["username"],),
            )
            conn.commit()
            conn.close()
            flash("Keamanan 2FA berhasil dinonaktifkan.", "success")
            return redirect(url_for("setup_2fa"))

        temp_secret = session.get("temp_totp_secret")
        if not temp_secret:
            flash("Sesi telah berakhir, silakan muat ulang halaman.", "error")
            conn.close()
            return redirect(url_for("setup_2fa"))

        kode_verifikasi = request.form.get("totp_code", "").strip()
        totp = pyotp.TOTP(temp_secret)
        if totp.verify(kode_verifikasi):
            conn.execute(
                "UPDATE admin_user SET totp_secret = ? WHERE username = ?",
                (temp_secret, session["username"]),
            )
            conn.commit()
            session.pop("temp_totp_secret", None)
            conn.close()
            flash(
                "Keamanan 2FA berhasil diaktifkan! Gunakan aplikasi Authenticator saat login berikutnya.",
                "success",
            )
            return redirect(url_for("index"))
        else:
            conn.close()
            flash(
                "Kode verifikasi salah! Pastikan Anda memasukkan kode yang tepat dari aplikasi.",
                "error",
            )
            return redirect(url_for("setup_2fa"))

    sudah_aktif = bool(user["totp_secret"])
    conn.close()

    qr_url = None
    if not sudah_aktif:
        if "temp_totp_secret" not in session:
            session["temp_totp_secret"] = pyotp.random_base32()
        totp_uri = pyotp.TOTP(session["temp_totp_secret"]).provisioning_uri(
            name=session["username"], issuer_name="Billing Internet"
        )
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={urllib.parse.quote(totp_uri)}"

    return render_template(
        "setup_2fa.html",
        sudah_aktif=sudah_aktif,
        qr_url=qr_url,
        secret=session.get("temp_totp_secret"),
    )


# =============================================================================
# ROUTES: GANTI PASSWORD
# =============================================================================
@app.route("/ganti_password", methods=["GET", "POST"])
@login_required
def ganti_password():
    """Endpoint untuk mengubah password default."""
    if request.method == "POST":
        password_lama = request.form.get("password_lama", "")
        password_baru = request.form.get("password_baru", "")
        konfirmasi_password = request.form.get("konfirmasi_password", "")

        if not password_lama or not password_baru or not konfirmasi_password:
            flash("Semua kolom harus diisi.", "error")
            return redirect(url_for("ganti_password"))

        if password_baru != konfirmasi_password:
            flash("Password baru dan konfirmasi tidak cocok.", "error")
            return redirect(url_for("ganti_password"))

        conn = get_db_connection()
        user = conn.execute(
            "SELECT password FROM admin_user WHERE username = ?", (session["username"],)
        ).fetchone()

        if not user or not check_password_hash(user["password"], password_lama):
            conn.close()
            flash("Password lama salah.", "error")
            return redirect(url_for("ganti_password"))

        conn.execute(
            "UPDATE admin_user SET password = ? WHERE username = ?",
            (generate_password_hash(password_baru), session["username"]),
        )
        conn.commit()
        conn.close()

        flash(
            "Password berhasil diubah. Silakan login kembali dengan password baru.",
            "success",
        )
        return redirect(url_for("logout"))

    return render_template("ganti_password.html")


# =============================================================================
# ROUTES: WHATSAPP GATEWAY
# =============================================================================
@app.route("/whatsapp")
@login_required
@admin_required
def whatsapp_status():
    """Halaman panel WA Gateway — tampil QR atau status koneksi."""
    try:
        response = requests.get("http://127.0.0.1:3000/status", timeout=5)
        status_data = response.json()
    except Exception:
        status_data = {
            "connected": False,
            "qr": None,
            "error": "Server Bot Node.js tidak merespon. Cek: systemctl status wabot.service",
        }
    return render_template("whatsapp.html", status=status_data)


@app.route("/whatsapp/logout")
@login_required
@admin_required
def whatsapp_logout():
    """Putus koneksi WA dan hapus sesi autentikasi Baileys."""
    try:
        requests.post("http://127.0.0.1:3000/logout", timeout=5)
    except Exception:
        pass
    return redirect(url_for("whatsapp_status"))


@app.route("/api/wa-status")
@login_required
@admin_required
def wa_status_api():
    """Endpoint JSON ringan untuk polling cepat status koneksi WA dari frontend."""
    try:
        response = requests.get("http://127.0.0.1:3000/status", timeout=3)
        data = response.json()
    except Exception:
        data = {"connected": False, "qr": None}
    return jsonify(data)


# Custom handler untuk error 500 agar tidak membocorkan detail internal request
@app.errorhandler(500)
def internal_server_error(e):
    return (
        "Terjadi kesalahan internal pada server. Silakan coba beberapa saat lagi.",
        500,
    )


@app.errorhandler(404)
def page_not_found(e):
    return "Halaman tidak ditemukan.", 404


# =============================================================================
# ENTRY POINT
# =============================================================================
init_db()

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
