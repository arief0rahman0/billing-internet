import base64
import io
import os
from dotenv import load_dotenv
load_dotenv()
import sqlite3
import urllib.parse
from datetime import datetime
from functools import wraps

import re
import requests

from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash


from werkzeug.middleware.proxy_fix import ProxyFix

from database import get_db_connection, init_db, get_setting, set_setting, log_action, generate_tagihan_otomatis
from utils import send_whatsapp, send_whatsapp_pdf, generate_pdf_nota
from auth import login_required, admin_required, password_change_required


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
    # Untuk domain app1.billing-internet.web.id dengan reverse proxy
    # Sementara di-nonaktifkan untuk testing dark mode
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' app1.billing-internet.web.id; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com app1.billing-internet.web.id; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com app1.billing-internet.web.id; "
        "font-src 'self' https://fonts.gstatic.com app1.billing-internet.web.id; "
        "img-src 'self' data: https://api.qrserver.com /static/ app1.billing-internet.web.id; "
        "connect-src 'self' app1.billing-internet.web.id; "
        "upgrade-insecure-requests;"
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
        if request.form.get("hp_field"):
            error = "Aktivitas mencurigakan (bot) terdeteksi!"
            return render_template("login.html", error=error)

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            error = "Username dan password harus diisi."
        else:
            conn = get_db_connection()
            user = conn.execute(
                "SELECT * FROM admin_user WHERE username = ?", (username,)
            ).fetchone()
            conn.close()

            if user and check_password_hash(user["password"], password):
                # Jika user belum mendaftarkan nomor WA, arahkan ke setup OTP
                if not user["no_wa"]:
                    session["setup_user_id"] = user["id"]
                    return redirect(url_for("setup_otp"))

                # Generate 6-digit OTP
                import time
                import random
                otp = "".join([str(random.randint(0, 9)) for _ in range(6)])
                
                # Simpan di session sementara (5 menit)
                session["otp"] = otp
                session["otp_expiry"] = time.time() + 300
                session["pending_user_id"] = user["id"]
                session["pending_username"] = user["username"]
                session["pending_role"] = user["role"]
                session["force_password_change"] = user["force_password_change"] if user["force_password_change"] else 0

                # Kirim OTP via WhatsApp
                pesan = f"🔒 *KODE OTP LOGIN*\n\nKode OTP Anda adalah: *{otp}*\n\nBerlaku selama 5 menit. Jangan berikan kode ini kepada siapapun."
                from utils import send_whatsapp
                send_whatsapp(user["no_wa"], pesan)
                
                return redirect(url_for("verify_otp"))
            else:
                error = "Username atau Password salah!"

    return render_template("login.html", error=error)

@app.route("/setup_otp", methods=["GET", "POST"])
def setup_otp():
    if "setup_user_id" not in session:
        return redirect(url_for("login"))
        
    error = None
    if request.method == "POST":
        no_wa = request.form.get("no_wa", "").strip()
        if not no_wa:
            error = "Nomor WhatsApp harus diisi."
        else:
            conn = get_db_connection()
            conn.execute("UPDATE admin_user SET no_wa = ? WHERE id = ?", (no_wa, session["setup_user_id"]))
            conn.commit()
            conn.close()
            session.pop("setup_user_id", None)
            return redirect(url_for("login"))
            
    return render_template("setup_otp.html", error=error)

@app.route("/verify_otp", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def verify_otp():
    if "pending_user_id" not in session:
        return redirect(url_for("login"))
        
    error = None
    if request.method == "POST":
        import time
        otp_input = request.form.get("otp", "").strip()
        
        if time.time() > session.get("otp_expiry", 0):
            error = "Kode OTP sudah kedaluwarsa. Silakan login kembali."
            session.pop("pending_user_id", None)
            session.pop("otp", None)
        elif otp_input == session.get("otp"):
            session.permanent = True
            session["logged_in"] = True
            session["username"] = session["pending_username"]
            session["role"] = session["pending_role"]
            
            log_action(session["username"], "Login", "Berhasil login ke sistem dengan OTP")
            
            # Check if user is required to change password
            force_change = session.pop("force_password_change", 0)
            
            session.pop("pending_user_id", None)
            session.pop("pending_username", None)
            session.pop("pending_role", None)
            session.pop("otp", None)
            session.pop("otp_expiry", None)
            
            if force_change == 1:
                flash("Anda wajib mengubah password default sebelum melanjutkan.", "warning")
                return redirect(url_for("ganti_password"))
            
            return redirect(url_for("index"))
        else:
            error = "Kode OTP salah!"
            
    return render_template("verify_otp.html", error=error)



@app.route("/logout")
def logout():
    """Hapus semua session dan redirect ke login."""
    if "username" in session:
        log_action(session["username"], "Logout", "Keluar dari sistem")
    session.clear()
    return redirect(url_for("login"))


# =============================================================================
# ROUTES: PWA (Progressive Web App)
# =============================================================================
@app.route("/manifest.json")
def manifest():
    return send_file(os.path.join(app.root_path, "static", "manifest.json"), mimetype="application/manifest+json")

@app.route("/sw.js")
def service_worker():
    return send_file(os.path.join(app.root_path, "static", "sw.js"), mimetype="application/javascript")

@app.route("/debug_login")
def debug_login():
    """Debug page for testing login dark mode functionality."""
    return send_file(os.path.join(app.root_path, "debug_login.html"))


# =============================================================================
# ROUTES: DASHBOARD UTAMA
# =============================================================================
@app.route("/")
@login_required
@password_change_required
def index():
    """Redirect root ke dashboard."""
    return redirect(url_for("dashboard"))


# =============================================================================
# ROUTES: DASHBOARD PAGES
# =============================================================================
@app.route("/dashboard")
@login_required
@password_change_required
def dashboard():
    """Dashboard overview dengan statistik dan chart."""
    generate_tagihan_otomatis()
    bulan_sekarang = datetime.now().strftime("%Y-%m")

    # Pagination parameters
    page_pelanggan = request.args.get('page_pelanggan', 1, type=int)
    page_transaksi = request.args.get('page_transaksi', 1, type=int)
    per_page = 10  # Items per page

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

    chart_raw = conn.execute("""
        SELECT bulan_tagihan, COALESCE(SUM(jumlah_bayar), 0) AS total
        FROM pembayaran WHERE status = 'Lunas'
        GROUP BY bulan_tagihan ORDER BY bulan_tagihan ASC LIMIT 12
    """).fetchall()
    chart_labels = [row["bulan_tagihan"] for row in chart_raw]
    chart_data = [row["total"] for row in chart_raw]

    # Get total count for pelanggan
    total_pelanggan = conn.execute(
        "SELECT COUNT(*) FROM pelanggan"
    ).fetchone()[0]
    
    # Get paginated pelanggan
    offset_pelanggan = (page_pelanggan - 1) * per_page
    daftar_pelanggan = conn.execute(
        "SELECT * FROM pelanggan ORDER BY nama ASC LIMIT ? OFFSET ?",
        (per_page, offset_pelanggan)
    ).fetchall()

    # Get total count for transaksi
    total_transaksi = conn.execute(
        "SELECT COUNT(*) FROM pembayaran"
    ).fetchone()[0]
    
    # Get paginated transaksi terbaru
    offset_transaksi = (page_transaksi - 1) * per_page
    transaksi_terbaru = conn.execute("""
        SELECT pembayaran.id, pelanggan.id AS pelanggan_id,
               pelanggan.nama AS nama_pelanggan, pelanggan.no_wa,
               pembayaran.bulan_tagihan, pembayaran.jumlah_bayar,
               pembayaran.tanggal_bayar, pembayaran.status, pembayaran.catatan
        FROM pembayaran
        JOIN pelanggan ON pembayaran.pelanggan_id = pelanggan.id
        ORDER BY pembayaran.bulan_tagihan DESC, pembayaran.id DESC
        LIMIT ? OFFSET ?
    """, (per_page, offset_transaksi)).fetchall()
    
    conn.close()

    # Calculate pagination info
    total_pages_pelanggan = (total_pelanggan + per_page - 1) // per_page
    total_pages_transaksi = (total_transaksi + per_page - 1) // per_page

    return render_template(
        "dashboard.html",
        pelanggan=daftar_pelanggan,
        transaksi=transaksi_terbaru,
        total_pemasukan=total_pemasukan,
        pemasukan_bulan_ini=pemasukan_bulan_ini,
        piutang_bulan_ini=piutang_bulan_ini,
        chart_labels=chart_labels,
        chart_data=chart_data,
        # Pagination info for pelanggan
        page_pelanggan=page_pelanggan,
        total_pages_pelanggan=total_pages_pelanggan,
        total_pelanggan=total_pelanggan,
        # Pagination info for transaksi
        page_transaksi=page_transaksi,
        total_pages_transaksi=total_pages_transaksi,
        total_transaksi=total_transaksi,
        per_page=per_page,
    )


@app.route("/transaksi")
@login_required
@password_change_required
def transaksi():
    """Halaman kelola transaksi/pembayaran."""
    generate_tagihan_otomatis()
    search_query = request.args.get("search", "").strip()

    conn = get_db_connection()

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

    conn.close()

    return render_template(
        "transaksi.html",
        data=data_pembayaran,
        search_query=search_query,
    )


@app.route("/pelanggan")
@login_required
@password_change_required
def pelanggan():
    """Halaman kelola pelanggan."""
    conn = get_db_connection()
    daftar_pelanggan = conn.execute(
        "SELECT * FROM pelanggan ORDER BY nama ASC"
    ).fetchall()
    conn.close()

    return render_template(
        "pelanggan.html",
        pelanggan=daftar_pelanggan,
    )


@app.route("/laporan")
@login_required
@password_change_required
def laporan():
    """Halaman laporan keuangan."""
    conn = get_db_connection()

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

    conn.close()

    return render_template(
        "laporan.html",
        rekap_bulanan=rekap_bulanan,
        chart_labels=chart_labels,
        chart_data=chart_data,
    )


# =============================================================================
# ROUTES: MANAJEMEN PELANGGAN
# =============================================================================
@app.route("/tambah_pelanggan", methods=["POST"])
@limiter.limit("5 per minute")
@login_required
@password_change_required
def tambah_pelanggan():
    """Tambah pelanggan baru dan buat tagihan bulan berjalan."""
    if request.form.get("hp_field"):
        flash("Aktivitas mencurigakan (bot) terdeteksi!", "error")
        return redirect(url_for("pelanggan"))

    nama = request.form.get("nama", "").strip()
    tagihan_raw = request.form.get("tagihan", "0").strip()
    no_wa = request.form.get("no_wa", "").strip()

    # Validasi input
    if not nama:
        flash("Nama pelanggan tidak boleh kosong.", "error")
        return redirect(url_for("pelanggan"))
    try:
        tagihan = int(tagihan_raw)
        if tagihan <= 0:
            raise ValueError
    except ValueError:
        flash("Tagihan bulanan harus berupa angka positif.", "error")
        return redirect(url_for("pelanggan"))

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
    log_action(session.get("username", "system"), "Tambah Pelanggan", f"Menambahkan pelanggan baru: {nama}")
    flash("Anggota berhasil disimpan.", "success")
    return redirect(url_for("pelanggan"))


@app.route("/edit_pelanggan/<int:id>", methods=["GET", "POST"])
@login_required
@password_change_required
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
        log_action(session.get("username", "system"), "Edit Pelanggan", f"Mengubah data pelanggan ID {id}: {nama_baru}")
        flash("Perubahan data berhasil disimpan.", "success")
        return redirect(url_for("index"))

    conn.close()
    return render_template("edit_pelanggan.html", p=pelanggan)


@app.route("/hapus_pelanggan/<int:id>", methods=["POST"])
@login_required
@admin_required
@password_change_required
def hapus_pelanggan(id):
    """Hapus pelanggan beserta semua data pembayarannya (CASCADE)."""
    conn = get_db_connection()
    conn.execute("DELETE FROM pelanggan WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    log_action(session.get("username", "system"), "Hapus Pelanggan", f"Menghapus pelanggan ID {id}")
    flash("Pelanggan berhasil dihapus.", "success")
    return redirect(url_for("index"))


# =============================================================================
# ROUTES: PEMBAYARAN
# =============================================================================
@app.route("/update_catatan/<int:id>", methods=["POST"])
@login_required
@password_change_required
def update_catatan(id):
    """Update field catatan pada record pembayaran."""
    catatan_baru = request.form.get("catatan", "").strip()
    conn = get_db_connection()
    conn.execute("UPDATE pembayaran SET catatan = ? WHERE id = ?", (catatan_baru, id))
    conn.commit()
    conn.close()
    flash("Catatan berhasil disimpan.", "success")
    return redirect(url_for("transaksi"))


@app.route("/lunas/<int:id>", methods=["POST"])
@login_required
@password_change_required
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
        return redirect(url_for("transaksi"))

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
        log_action(session.get("username", "system"), "Update Pembayaran", f"Set Lunas {len(payment_ids)} bulan untuk pelanggan ID {pelanggan_id}")

    conn.close()
    flash(f"Pembayaran {len(payment_ids)} bulan berhasil diupdate ke Lunas.", "success")
    return redirect(url_for("transaksi"))


# =============================================================================
# ROUTES: NOTA
# =============================================================================
@app.route("/nota/<int:id>")
@login_required
@password_change_required
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
@password_change_required
def kirim_wa_lunas(id):
    """Kirim nota PDF via WA ke pelanggan yang sudah Lunas."""
    conn = get_db_connection()
    info = conn.execute(
        """
        SELECT pembayaran.id, pelanggan.nama, pelanggan.no_wa,
               pembayaran.bulan_tagihan, pembayaran.jumlah_bayar,
               pembayaran.tanggal_bayar, pembayaran.catatan
        FROM pembayaran
        JOIN pelanggan ON pembayaran.pelanggan_id = pelanggan.id
        WHERE pembayaran.id = ?
    """,
        (id,),
    ).fetchone()
    conn.close()

    if info and info["no_wa"]:
        nota_data = {
            "id": info["id"],
            "nama_pelanggan": info["nama"],
            "bulan_tagihan": info["bulan_tagihan"],
            "jumlah_bayar": info["jumlah_bayar"],
            "tanggal_bayar": info["tanggal_bayar"],
            "catatan": info["catatan"],
        }

        # Generate PDF nota
        pdf_bytes = generate_pdf_nota(nota_data, app.root_path)

        template_wa = get_setting("pesan_lunas")
        caption_wa = template_wa.replace("{nama}", str(info['nama'])) \
                                .replace("{bulan_tagihan}", str(info['bulan_tagihan'])) \
                                .replace("{jumlah_bayar}", f"{info['jumlah_bayar']:,}") \
                                .replace("{tanggal_bayar}", str(info['tanggal_bayar']))

        no_nota = f"NOTA-{info['id']:04d}-{datetime.now().strftime('%m%Y')}"
        filename = f"{no_nota}.pdf"

        if pdf_bytes:
            # Kirim PDF sebagai dokumen WA
            send_whatsapp_pdf(info["no_wa"], pdf_bytes, filename, caption_wa)
        else:
            # Fallback: kirim pesan teks jika PDF gagal
            send_whatsapp(info["no_wa"], caption_wa)

    flash("Nota PDF WA berhasil terkirim.", "success")
    return redirect(url_for("index"))


@app.route("/kirim_wa_pengingat/<int:id>", methods=["POST"])
@login_required
@password_change_required
def kirim_wa_pengingat(id):
    """Kirim pesan pengingat tagihan jatuh tempo ke pelanggan."""
    conn = get_db_connection()
    info = conn.execute(
        """
        SELECT pembayaran.id, pelanggan.nama, pelanggan.no_wa,
               pembayaran.bulan_tagihan, pembayaran.jumlah_bayar
        FROM pembayaran
        JOIN pelanggan ON pembayaran.pelanggan_id = pelanggan.id
        WHERE pembayaran.id = ?
    """,
        (id,),
    ).fetchone()
    conn.close()

    if info and info["no_wa"]:
        template_wa = get_setting("pesan_pengingat")
        pesan_wa = template_wa.replace("{nama}", str(info['nama'])) \
                              .replace("{bulan_tagihan}", str(info['bulan_tagihan'])) \
                              .replace("{jumlah_bayar}", f"{info['jumlah_bayar']:,}")
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
@password_change_required
def backup_page():
    return render_template("backup.html")


@app.route("/backup/download")
@login_required
@admin_required
@password_change_required
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
@password_change_required
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
            "UPDATE admin_user SET password = ?, force_password_change = 0 WHERE username = ?",
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
# ROUTES: GANTI WHATSAPP
# =============================================================================
@app.route("/ganti_wa", methods=["GET", "POST"])
@login_required
@password_change_required
def ganti_wa():
    """Endpoint untuk mengubah nomor WhatsApp."""
    if request.method == "POST":
        no_wa_baru = request.form.get("no_wa", "").strip()
        
        if not no_wa_baru:
            flash("Nomor WhatsApp harus diisi.", "error")
            return redirect(url_for("ganti_wa"))
            
        conn = get_db_connection()
        conn.execute(
            "UPDATE admin_user SET no_wa = ? WHERE username = ?",
            (no_wa_baru, session["username"]),
        )
        conn.commit()
        conn.close()
        
        flash("Nomor WhatsApp berhasil diubah.", "success")
        return redirect(url_for("index"))
        
    conn = get_db_connection()
    user = conn.execute("SELECT no_wa FROM admin_user WHERE username = ?", (session["username"],)).fetchone()
    conn.close()
    current_wa = user["no_wa"] if user and user["no_wa"] else ""
    
    return render_template("ganti_wa.html", current_wa=current_wa)

# =============================================================================
# ROUTES: WHATSAPP GATEWAY
# =============================================================================
@app.route("/whatsapp")
@login_required
@admin_required
@password_change_required
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
@password_change_required
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
@password_change_required
def wa_status_api():
    """Endpoint JSON ringan untuk polling cepat status koneksi WA dari frontend."""
    try:
        response = requests.get("http://127.0.0.1:3000/status", timeout=3)
        data = response.json()
    except Exception:
        data = {"connected": False, "qr": None}
    return jsonify(data)


# =============================================================================
# ROUTES: AUDIT LOG
# =============================================================================
@app.route("/audit_log")
@login_required
@admin_required
@password_change_required
def audit_log():
    """Menampilkan log aktivitas."""
    conn = get_db_connection()
    logs = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 500").fetchall()
    conn.close()
    return render_template("audit_log.html", logs=logs)


# =============================================================================
# ROUTES: PENGATURAN PESAN WA
# =============================================================================
@app.route("/pengaturan_pesan", methods=["GET", "POST"])
@login_required
@admin_required
@password_change_required
def pengaturan_pesan():
    """Dashboard untuk mengedit template pesan WA tagihan & pelunasan."""
    if request.method == "POST":
        pesan_lunas = request.form.get("pesan_lunas", "").strip()
        pesan_pengingat = request.form.get("pesan_pengingat", "").strip()

        if pesan_lunas:
            set_setting("pesan_lunas", pesan_lunas)
        if pesan_pengingat:
            set_setting("pesan_pengingat", pesan_pengingat)

        log_action(session.get("username", "system"), "Edit Template Pesan", "Mengubah template pesan WA")
        flash("Template pesan berhasil disimpan.", "success")
        return redirect(url_for("pengaturan_pesan"))

    current_lunas = get_setting("pesan_lunas")
    current_pengingat = get_setting("pesan_pengingat")
    return render_template(
        "pengaturan_pesan.html",
        pesan_lunas=current_lunas,
        pesan_pengingat=current_pengingat,
    )


# =============================================================================
# ROUTES: MANAJEMEN PENGGUNA (RBAC)
# =============================================================================
@app.route("/manajemen_pengguna")
@login_required
@admin_required
@password_change_required
def manajemen_pengguna():
    """Halaman manajemen pengguna (RBAC) — hanya untuk admin."""
    conn = get_db_connection()
    users = conn.execute("SELECT id, username, role, no_wa, force_password_change FROM admin_user ORDER BY username ASC").fetchall()
    conn.close()
    return render_template("manajemen_pengguna.html", users=users)

@app.route("/tambah_pengguna", methods=["POST"])
@login_required
@admin_required
@password_change_required
def tambah_pengguna():
    """Tambah pengguna baru."""
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "operator")
    no_wa = request.form.get("no_wa", "").strip()

    if not username or not password:
        flash("Username dan password harus diisi.", "error")
        return redirect(url_for("manajemen_pengguna"))

    conn = get_db_connection()
    existing = conn.execute("SELECT id FROM admin_user WHERE username = ?", (username,)).fetchone()
    if existing:
        conn.close()
        flash("Username sudah digunakan.", "error")
        return redirect(url_for("manajemen_pengguna"))

    conn.execute(
        "INSERT INTO admin_user (username, password, role, no_wa, force_password_change) VALUES (?, ?, ?, ?, ?)",
        (username, generate_password_hash(password), role, no_wa, 1),
    )
    conn.commit()
    conn.close()
    flash(f"Pengguna '{username}' berhasil ditambahkan sebagai {role}.", "success")
    log_action(session["username"], "Tambah Pengguna", f"Menambahkan {username} ({role})")
    return redirect(url_for("manajemen_pengguna"))

@app.route("/edit_pengguna/<int:id>", methods=["POST"])
@login_required
@admin_required
@password_change_required
def edit_pengguna(id):
    """Edit data pengguna."""
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "operator")
    no_wa = request.form.get("no_wa", "").strip()
    force_password_change = 1 if request.form.get("force_password_change") else 0

    conn = get_db_connection()
    if password:
        conn.execute(
            "UPDATE admin_user SET username = ?, password = ?, role = ?, no_wa = ?, force_password_change = ? WHERE id = ?",
            (username, generate_password_hash(password), role, no_wa, force_password_change, id),
        )
    else:
        conn.execute(
            "UPDATE admin_user SET username = ?, role = ?, no_wa = ?, force_password_change = ? WHERE id = ?",
            (username, role, no_wa, force_password_change, id),
        )
    conn.commit()
    conn.close()
    flash("Data pengguna berhasil diperbarui.", "success")
    log_action(session["username"], "Edit Pengguna", f"Mengubah data user ID {id}")
    return redirect(url_for("manajemen_pengguna"))

@app.route("/hapus_pengguna/<int:id>", methods=["POST"])
@login_required
@admin_required
@password_change_required
def hapus_pengguna(id):
    """Hapus pengguna — tidak bisa menghapus diri sendiri."""
    conn = get_db_connection()
    current_user = conn.execute("SELECT id FROM admin_user WHERE username = ?", (session.get("username"),)).fetchone()
    
    if current_user and id == current_user["id"]:
        conn.close()
        flash("Tidak dapat menghapus akun Anda sendiri.", "error")
        return redirect(url_for("manajemen_pengguna"))

    user = conn.execute("SELECT username FROM admin_user WHERE id = ?", (id,)).fetchone()
    deleted_name = user["username"] if user else f"ID {id}"
    conn.execute("DELETE FROM admin_user WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    flash(f"Pengguna '{deleted_name}' berhasil dihapus.", "success")
    log_action(session["username"], "Hapus Pengguna", f"Menghapus {deleted_name}")
    return redirect(url_for("manajemen_pengguna"))


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
