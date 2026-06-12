from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import requests

app = Flask(__name__)
app.secret_key = 'kunci_rahasia_aplikasi_internet_lokal'

# ==========================================
# KONFIGURASI WHATSAPP BOT LOKAL (PORT 3000)
# ==========================================
def send_whatsapp(no_hp, pesan):
    """Mengirim pesan WA via Server Bot Lokal buatan sendiri di Port 3000"""
    if not no_hp:
        return False
        
    url = "http://127.0.0.1:3000/send"
    data = {
        "target": no_hp,
        "message": pesan
    }
    try:
        # Menembak API internal Node.js di VPS yang sama
        response = requests.post(url, data=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Gagal mengirim WhatsApp Lokal: {e}")
        return False
# ==========================================

def get_db_connection():
    conn = sqlite3.connect('pembayaran_internet.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    
    # 1. TABEL ADMIN
    conn.execute('''
        CREATE TABLE IF NOT EXISTS admin_user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    
    admin_exists = conn.execute('SELECT * FROM admin_user WHERE username = ?', ('admin',)).fetchone()
    if not admin_exists:
        hashed_password = generate_password_hash('admin123')
        conn.execute('INSERT INTO admin_user (username, password) VALUES (?, ?)', ('admin', hashed_password))
        
    # 2. TABEL MASTER PELANGGAN
    conn.execute('''
        CREATE TABLE IF NOT EXISTS pelanggan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT NOT NULL,
            tagihan_bulanan INTEGER NOT NULL
        )
    ''')
    
    # Migrasi Otomatis: Tambah kolom no_wa jika belum ada di database lama
    try:
        conn.execute('ALTER TABLE pelanggan ADD COLUMN no_wa TEXT DEFAULT ""')
    except sqlite3.OperationalError:
        pass 

    # 3. TABEL TRANSAKSI TAGIHAN
    conn.execute('''
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
    ''')
    conn.commit()
    conn.close()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def generate_tagihan_otomatis():
    conn = get_db_connection()
    bulan_sekarang = datetime.now().strftime('%Y-%m')
    semua_pelanggan = conn.execute('SELECT * FROM pelanggan').fetchall()
    
    for p in semua_pelanggan:
        existing = conn.execute(
            'SELECT id FROM pembayaran WHERE pelanggan_id = ? AND bulan_tagihan = ?',
            (p['id'], bulan_sekarang)
        ).fetchone()
        
        if not existing:
            conn.execute('''
                INSERT INTO pembayaran (pelanggan_id, bulan_tagihan, jumlah_bayar, tanggal_bayar, status, catatan)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (p['id'], bulan_sekarang, p['tagihan_bulanan'], '-', 'Belum Bayar', ''))
            
    conn.commit()
    conn.close()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'logged_in' in session:
        return redirect(url_for('index'))
    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM admin_user WHERE username = ?', (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            session['logged_in'] = True
            session['username'] = user['username']
            return redirect(url_for('index'))
        else:
            error = "Username atau Password salah!"
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    generate_tagihan_otomatis()
    search_query = request.args.get('search', '')
    bulan_sekarang = datetime.now().strftime('%Y-%m')
    
    conn = get_db_connection()
    
    # HITUNG REKAP KEUANGAN
    total_pemasukan = conn.execute("SELECT COALESCE(SUM(jumlah_bayar), 0) FROM pembayaran WHERE status = 'Lunas'").fetchone()[0]
    pemasukan_bulan_ini = conn.execute("SELECT COALESCE(SUM(jumlah_bayar), 0) FROM pembayaran WHERE status = 'Lunas' AND bulan_tagihan = ?", (bulan_sekarang,)).fetchone()[0]
    piutang_bulan_ini = conn.execute("SELECT COALESCE(SUM(jumlah_bayar), 0) FROM pembayaran WHERE status = 'Belum Bayar' AND bulan_tagihan = ?", (bulan_sekarang,)).fetchone()[0]
    
    rekap_bulanan = conn.execute('''
        SELECT bulan_tagihan, COALESCE(SUM(jumlah_bayar), 0) AS total 
        FROM pembayaran WHERE status = 'Lunas' 
        GROUP BY bulan_tagihan ORDER BY bulan_tagihan DESC
    ''').fetchall()
    
    query = '''
        SELECT pembayaran.id, pelanggan.nama AS nama_pelanggan, pelanggan.no_wa, pembayaran.bulan_tagihan, 
               pembayaran.jumlah_bayar, pembayaran.tanggal_bayar, pembayaran.status, pembayaran.catatan
        FROM pembayaran
        JOIN pelanggan ON pembayaran.pelanggan_id = pelanggan.id
    '''
    
    if search_query:
        query += ' WHERE pelanggan.nama LIKE ? ORDER BY pembayaran.bulan_tagihan DESC, pembayaran.id DESC'
        data_pembayaran = conn.execute(query, ('%' + search_query + '%',)).fetchall()
    else:
        query += ' ORDER BY pembayaran.bulan_tagihan DESC, pembayaran.id DESC'
        data_pembayaran = conn.execute(query).fetchall()
        
    daftar_pelanggan = conn.execute('SELECT * FROM pelanggan ORDER BY nama ASC').fetchall()
    conn.close()
    
    return render_template(
        'index.html', data=data_pembayaran, pelanggan=daftar_pelanggan, search_query=search_query,
        total_pemasukan=total_pemasukan, pemasukan_bulan_ini=pemasukan_bulan_ini, piutang_bulan_ini=piutang_bulan_ini, rekap_bulanan=rekap_bulanan
    )

@app.route('/update_catatan/<int:id>', methods=['POST'])
@login_required
def update_catatan(id):
    catatan_baru = request.form['catatan']
    conn = get_db_connection()
    conn.execute('UPDATE pembayaran SET catatan = ? WHERE id = ?', (catatan_baru, id))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/tambah_pelanggan', methods=['POST'])
@login_required
def tambah_pelanggan():
    nama = request.form['nama']
    tagihan = request.form['tagihan']
    no_wa = request.form['no_wa']
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO pelanggan (nama, tagihan_bulanan, no_wa) VALUES (?, ?, ?)', (nama, tagihan, no_wa))
    pelanggan_id = cursor.lastrowid
    
    bulan_sekarang = datetime.now().strftime('%Y-%m')
    conn.execute('''
        INSERT INTO pembayaran (pelanggan_id, bulan_tagihan, jumlah_bayar, tanggal_bayar, status, catatan)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (pelanggan_id, bulan_sekarang, tagihan, '-', 'Belum Bayar', ''))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/lunas/<int:id>')
@login_required
def set_lunas(id):
    tanggal_sekarang = datetime.now().strftime('%Y-%m-%d %H:%M')
    conn = get_db_connection()
    
    query_info = '''
        SELECT pelanggan.nama, pelanggan.no_wa, pembayaran.bulan_tagihan, pembayaran.jumlah_bayar 
        FROM pembayaran JOIN pelanggan ON pembayaran.pelanggan_id = pelanggan.id WHERE pembayaran.id = ?
    '''
    info = conn.execute(query_info, (id,)).fetchone()
    
    conn.execute('UPDATE pembayaran SET status = ?, tanggal_bayar = ? WHERE id = ?', ('Lunas', tanggal_sekarang, id))
    conn.commit()
    conn.close()
    
    # TRIGGER NOTIFIKASI BOT WA LOKAL + LINK NOTA DIGITAL
    if info and info['no_wa']:
        # Mengambil alamat IP VPS kamu secara otomatis (contoh: http://103.x.x.x/nota/5)
        link_nota = f"{request.host_url}nota/{id}"
        
        pesan_wa = (
            f"🟢 *BUKTI PEMBAYARAN INTERNET LUNAS*\n\n"
            f"Yth. Bapak/Ibu *{info['nama']}*,\n"
            f"Terima kasih, pembayaran tagihan internet Anda telah kami terima.\n\n"
            f"📦 *Detail Transaksi:*\n"
            f"• Periode Bulan: {info['bulan_tagihan']}\n"
            f"• Jumlah Bayar: Rp {info['jumlah_bayar']:,}\n"
            f"• Waktu Sukses: {tanggal_sekarang} WIB\n\n"
            f"Status Tagihan Anda saat ini dinyatakan: *LUNAS/PAID*.\n\n"
            f"📄 *Link Nota Digital Resmi (Bisa Di-download/Print):*\n"
            f"{link_nota}\n\n"
            f"📱 _Pesan ini dikirim otomatis oleh sistem Billing Internet._"
        )
        send_whatsapp(info['no_wa'], pesan_wa)
        
    return redirect(url_for('index'))

@app.route('/hapus_pelanggan/<int:id>')
@login_required
def hapus_pelanggan(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM pelanggan WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

# SEBELUMNYA:
# @app.route('/nota/<int:id>')
# @login_required
# def cetak_nota(id):

# DIUBAH MENJADI:
@app.route('/nota/<int:id>')
# 🚫 Baris @login_required di sini sudah dihapus agar pelanggan bisa langsung buka
def cetak_nota(id):
    conn = get_db_connection()
    query = '''
        SELECT pembayaran.id, pelanggan.nama AS nama_pelanggan, pembayaran.bulan_tagihan, 
               pembayaran.jumlah_bayar, pembayaran.tanggal_bayar, pembayaran.status, pembayaran.catatan
        FROM pembayaran
        JOIN pelanggan ON pembayaran.pelanggan_id = pelanggan.id WHERE pembayaran.id = ?
    '''
    nota = conn.execute(query, (id,)).fetchone()
    conn.close()
    
    # Keamanan tambahan: Nota hanya bisa dibuka jika statusnya memang sudah 'Lunas'
    if nota and nota['status'] == 'Lunas':
        return render_template('nota.html', nota=nota)
    else:
        return "Nota tidak ditemukan atau tagihan belum dilunasi.", 404

@app.route('/whatsapp')
@login_required
def whatsapp_status():
    """Halaman panel kontrol status WhatsApp dan Scan Barcode"""
    try:
        # Minta data status dan QR dari bot Node.js port 3000
        response = requests.get("http://127.0.0.1:3000/status", timeout=5)
        status_data = response.json()
    except Exception as e:
        status_data = {"connected": False, "qr": None, "error": "Server Bot Node.js tidak merespon"}
        
    return render_template('whatsapp.html', status=status_data)

@app.route('/whatsapp/logout')
@login_required
def whatsapp_logout():
    """Rute untuk memicu putus koneksi / ganti nomor baru"""
    try:
        requests.post("http://127.0.0.1:3000/logout", timeout=5)
    except Exception:
        pass
    return redirect(url_for('whatsapp_status'))

# Inisialisasi diletakkan di luar blok '__main__' agar terbaca Gunicorn VPS
init_db()

if __name__ == '__main__':
    app.run(debug=False)
