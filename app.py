from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'kunci_rahasia_aplikasi_internet_lokal'

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
    
    # 3. TABEL TRANSAKSI TAGIHAN (Ditambahkan kolom catatan)
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
    conn = get_db_connection()
    
    # Query mengambil kolom pembayaran.catatan
    query = '''
        SELECT pembayaran.id, pelanggan.nama AS nama_pelanggan, pembayaran.bulan_tagihan, 
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
    return render_template('index.html', data=data_pembayaran, pelanggan=daftar_pelanggan, search_query=search_query)

# ROUTE BARU: Update Catatan secara realtime/inline
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
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO pelanggan (nama, tagihan_bulanan) VALUES (?, ?)', (nama, tagihan))
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
    conn.execute('UPDATE pembayaran SET status = ?, tanggal_bayar = ? WHERE id = ?', ('Lunas', tanggal_sekarang, id))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/hapus_pelanggan/<int:id>')
@login_required
def hapus_pelanggan(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM pelanggan WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/nota/<int:id>')
@login_required
def cetak_nota(id):
    conn = get_db_connection()
    query = '''
        SELECT pembayaran.id, pelanggan.nama AS nama_pelanggan, pembayaran.bulan_tagihan, 
               pembayaran.jumlah_bayar, pembayaran.tanggal_bayar, pembayaran.status, pembayaran.catatan
        FROM pembayaran
        JOIN pelanggan ON pembayaran.pelanggan_id = pelanggan.id
        WHERE pembayaran.id = ?
    '''
    nota = conn.execute(query, (id,)).fetchone()
    conn.close()
    if nota and nota['status'] == 'Lunas':
        return render_template('nota.html', nota=nota)
    else:
        return "Nota tidak ditemukan atau tagihan belum dilunasi.", 404

#if __name__ == '__main__':
#    init_db()
#    app.run(debug=True)
# PINDAHKAN KE SINI (Di luar if __name__)
# Supaya Gunicorn di Render otomatis membuat database saat aplikasi dinyalakan
init_db()

if __name__ == '__main__':
    app.run(debug=False)
