from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import sqlite3
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import requests
import pyotp

app = Flask(__name__)
app.secret_key = 'kunci_rahasia_aplikasi_internet_lokal'

# ==========================================
# KONFIGURASI WHATSAPP BOT LOKAL (PORT 3000)
# ==========================================
def send_whatsapp(no_hp, pesan):
    if not no_hp:
        return False
    url = "http://127.0.0.1:3000/send"
    data = {"target": no_hp, "message": pesan}
    try:
        response = requests.post(url, json=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Gagal mengirim WhatsApp Lokal: {e}")
        return False

def get_db_connection():
    conn = sqlite3.connect('pembayaran_internet.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS admin_user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    try:
        conn.execute("ALTER TABLE admin_user ADD COLUMN role TEXT DEFAULT 'admin'")
    except sqlite3.OperationalError:
        pass
        
    try:
        conn.execute("ALTER TABLE admin_user ADD COLUMN totp_secret TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    
    admin_exists = conn.execute('SELECT * FROM admin_user WHERE username = ?', ('admin',)).fetchone()
    if not admin_exists:
        hashed_password = generate_password_hash('admin123')
        conn.execute('INSERT INTO admin_user (username, password, role) VALUES (?, ?, ?)', ('admin', hashed_password, 'admin'))
        
    operator_exists = conn.execute('SELECT * FROM admin_user WHERE username = ?', ('operator',)).fetchone()
    if not operator_exists:
        hashed_op_password = generate_password_hash('operator123')
        conn.execute('INSERT INTO admin_user (username, password, role) VALUES (?, ?, ?)', ('operator', hashed_op_password, 'operator'))
        
    conn.execute('''
        CREATE TABLE IF NOT EXISTS pelanggan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT NOT NULL,
            tagihan_bulanan INTEGER NOT NULL
        )
    ''')
    try:
        conn.execute('ALTER TABLE pelanggan ADD COLUMN no_wa TEXT DEFAULT ""')
    except sqlite3.OperationalError:
        pass 

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

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'admin':
            flash('Akses ditolak! Fitur ini hanya untuk tingkat Admin.', 'error')
            return redirect(url_for('index'))
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
        totp_code = request.form['totp_code']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM admin_user WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            if user['totp_secret']:
                totp = pyotp.TOTP(user['totp_secret'])
                if totp.verify(totp_code):
                    session['logged_in'] = True
                    session['username'] = user['username']
                    session['role'] = user['role']
                    return redirect(url_for('index'))
                else:
                    error = "Kode Keamanan TOTP (6 Digit) Salah atau Kedaluwarsa!"
            else:
                session['logged_in'] = True
                session['username'] = user['username']
                session['role'] = user['role']
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
    total_pemasukan = conn.execute("SELECT COALESCE(SUM(jumlah_bayar), 0) FROM pembayaran WHERE status = 'Lunas'").fetchone()[0]
    pemasukan_bulan_ini = conn.execute("SELECT COALESCE(SUM(jumlah_bayar), 0) FROM pembayaran WHERE status = 'Lunas' AND bulan_tagihan = ?", (bulan_sekarang,)).fetchone()[0]
    piutang_bulan_ini = conn.execute("SELECT COALESCE(SUM(jumlah_bayar), 0) FROM pembayaran WHERE status = 'Belum Bayar' AND bulan_tagihan = ?", (bulan_sekarang,)).fetchone()[0]
    
    rekap_bulanan = conn.execute('''
        SELECT bulan_tagihan, COALESCE(SUM(jumlah_bayar), 0) AS total 
        FROM pembayaran WHERE status = 'Lunas' 
        GROUP BY bulan_tagihan ORDER BY bulan_tagihan DESC
    ''').fetchall()
    
    chart_raw = conn.execute('''
        SELECT bulan_tagihan, COALESCE(SUM(jumlah_bayar), 0) AS total 
        FROM pembayaran WHERE status = 'Lunas' 
        GROUP BY bulan_tagihan ORDER BY bulan_tagihan ASC LIMIT 12
    ''').fetchall()
    chart_labels = [row['bulan_tagihan'] for row in chart_raw]
    chart_data = [row['total'] for row in chart_raw]
    
    query = '''
        SELECT pembayaran.id, pelanggan.id AS pelanggan_id, pelanggan.nama AS nama_pelanggan, pelanggan.no_wa, pembayaran.bulan_tagihan, 
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
        total_pemasukan=total_pemasukan, pemasukan_bulan_ini=pemasukan_bulan_ini, piutang_bulan_ini=piutang_bulan_ini, 
        rekap_bulanan=rekap_bulanan, chart_labels=chart_labels, chart_data=chart_data
    )

@app.route('/update_catatan/<int:id>', methods=['POST'])
@login_required
def update_catatan(id):
    catatan_baru = request.form['catatan']
    conn = get_db_connection()
    conn.execute('UPDATE pembayaran SET catatan = ? WHERE id = ?', (catatan_baru, id))
    conn.commit()
    conn.close()
    flash('Catatan berhasil disimpan', 'success')
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
    flash('Anggota baru berhasil ditambahkan', 'success')
    return redirect(url_for('index'))

@app.route('/edit_pelanggan/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_pelanggan(id):
    conn = get_db_connection()
    if request.method == 'POST':
        nama_baru = request.form['nama']
        tagihan_baru = request.form['tagihan']
        no_wa_baru = request.form['no_wa']
        conn.execute('UPDATE pelanggan SET nama = ?, tagihan_bulanan = ?, no_wa = ? WHERE id = ?', (nama_baru, tagihan_baru, no_wa_baru, id))
        conn.execute("UPDATE pembayaran SET jumlah_bayar = ? WHERE pelanggan_id = ? AND status = 'Belum Bayar'", (tagihan_baru, id))
        conn.commit()
        conn.close()
        flash('Perubahan data berhasil disimpan', 'success')
        return redirect(url_for('index'))
    pelanggan = conn.execute('SELECT * FROM pelanggan WHERE id = ?', (id,)).fetchone()
    conn.close()
    if pelanggan:
        return render_template('edit_pelanggan.html', p=pelanggan)
    return "Pelanggan tidak ditemukan", 404

@app.route('/lunas/<int:id>')
@login_required
def set_lunas(id):
    tanggal_sekarang = datetime.now().strftime('%Y-%m-%d %H:%M')
    conn = get_db_connection()
    conn.execute('UPDATE pembayaran SET status = ?, tanggal_bayar = ? WHERE id = ?', ('Lunas', tanggal_sekarang, id))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/kirim_wa_lunas/<int:id>')
@login_required
def kirim_wa_lunas(id):
    conn = get_db_connection()
    query_info = '''
        SELECT pelanggan.nama, pelanggan.no_wa, pembayaran.bulan_tagihan, pembayaran.jumlah_bayar, pembayaran.tanggal_bayar 
        FROM pembayaran JOIN pelanggan ON pembayaran.pelanggan_id = pelanggan.id WHERE pembayaran.id = ?
    '''
    info = conn.execute(query_info, (id,)).fetchone()
    conn.close()
    if info and info['no_wa']:
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
            f"📄 *Link Nota Digital Resmi:*\n"
            f"{link_nota}\n\n"
            f"📱 _Pesan ini dikirim otomatis oleh sistem Billing Internet._"
        )
        send_whatsapp(info['no_wa'], pesan_wa)
    flash('Kuitansi lunas berhasil dikirim via WhatsApp', 'success')
    return redirect(url_for('index'))

@app.route('/kirim_wa_pengingat/<int:id>')
@login_required
def kirim_wa_pengingat(id):
    conn = get_db_connection()
    # REVISI: Disederhanakan kembali tanpa menarik ID pelanggan dan catatan paket
    query_info = '''
        SELECT pelanggan.nama, pelanggan.no_wa, 
               pembayaran.bulan_tagihan, pembayaran.jumlah_bayar
        FROM pembayaran 
        JOIN pelanggan ON pembayaran.pelanggan_id = pelanggan.id 
        WHERE pembayaran.id = ?
    '''
    info = conn.execute(query_info, (id,)).fetchone()
    conn.close()
    
    if info and info['no_wa']:
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
        send_whatsapp(info['no_wa'], pesan_wa)
        
    flash('Pesan pengingat jatuh tempo berhasil dikirim via WhatsApp', 'success')
    return redirect(url_for('index'))

@app.route('/backup')
@login_required
@admin_required
def backup_database():
    try:
        tanggal_hari_ini = datetime.now().strftime('%Y-%m-%d')
        return send_file('pembayaran_internet.db', as_attachment=True, download_name=f'backup_billing_{tanggal_hari_ini}.db')
    except Exception as e:
        flash(f'Gagal melakukan backup database: {e}', 'error')
        return redirect(url_for('index'))

@app.route('/hapus_pelanggan/<int:id>')
@login_required
@admin_required
def hapus_pelanggan(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM pelanggan WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/nota/<int:id>')
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
    if nota and nota['status'] == 'Lunas':
        return render_template('nota.html', nota=nota)
    else:
        return "Nota tidak ditemukan atau tagihan belum dilunasi.", 404

@app.route('/whatsapp')
@login_required
@admin_required
def whatsapp_status():
    try:
        response = requests.get("http://127.0.0.1:3000/status", timeout=5)
        status_data = response.json()
    except Exception:
        status_data = {"connected": False, "qr": None, "error": "Server Bot Node.js tidak merespon"}
    return render_template('whatsapp.html', status=status_data)

@app.route('/whatsapp/logout')
@login_required
@admin_required
def whatsapp_logout():
    try:
        requests.post("http://127.0.0.1:3000/logout", timeout=5)
    except Exception:
        pass
    return redirect(url_for('whatsapp_status'))

init_db()

if __name__ == '__main__':
    app.run(debug=False)
