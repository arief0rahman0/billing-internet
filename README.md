# 🌐 Sistem Manajemen & Pembayaran Billing Internet

Sistem Manajemen & Pembayaran Billing Internet adalah aplikasi web berbasis **Flask (Python)** yang dirancang untuk mempermudah administrasi billing pelanggan internet. Aplikasi ini dilengkapi dengan pengiriman notifikasi otomatis serta tanda terima PDF ke pelanggan melalui **WhatsApp Gateway** (berbasis Node.js & Baileys), keamanan dua faktor (2FA OTP), proteksi bot, sistem tema gelap/terang, dan manajemen database mandiri.

---

## 🚀 Fitur Utama Aplikasi

1. **Dashboard Overview**: 
   * Statistik ringkasan pendapatan bulanan, jumlah pelanggan aktif, tagihan belum terbayar, dan total transaksi.
   * Grafik visual interaktif perkembangan pembayaran menggunakan Chart.js.

2. **Manajemen Pelanggan**:
   * Menambahkan, mengedit, melihat detail, dan menghapus data pelanggan.
   * Pencarian pelanggan terintegrasi dan filter status aktif.

3. **Manajemen Transaksi & Pembayaran**:
   * Pencatatan riwayat tagihan bulanan pelanggan secara otomatis/manual.
   * Pencatatan status pembayaran (Lunas / Belum Bayar).

4. **WhatsApp Gateway & Otomatisasi Notifikasi**:
   * Mengirim pesan pengingat tagihan bulanan langsung ke nomor WhatsApp pelanggan.
   * Mengirim notifikasi pelunasan disertai lampiran dokumen **PDF Nota/Tanda Terima** yang dibuat secara dinamis menggunakan ReportLab.

5. **Keamanan Tingkat Tinggi (Production Ready)**:
   * **2FA OTP WhatsApp**: Autentikasi ganda menggunakan kode OTP yang dikirimkan ke nomor WhatsApp admin sebelum masuk ke dashboard.
   * **Honeypot Anti-Bot**: Proteksi tersembunyi dari spamming bot otomatis pada form login.
   * **Rate Limiter**: Batasan frekuensi request login/OTP untuk mencegah serangan Brute Force.
   * **CSRF Protection**: Menggunakan Flask-WTF untuk menghindari eksploitasi Cross-Site Request Forgery.
   * **Content Security Policy (CSP) & Security Headers**: Terkonfigurasi penuh dengan perlindungan XSS, clickjacking, MIME-sniffing, dan HSTS.

6. **Responsive Design & Dark Mode**:
   * Antarmuka modern yang sepenuhnya responsif di semua perangkat (ponsel, tablet, desktop).
   * Fitur toggle mode gelap/terang yang tersimpan secara lokal pada browser.

7. **Sistem Backup & Restore**:
   * Fitur backup database SQLite langsung dari halaman admin dan pemulihan data yang aman.

---

## 🛠️ Persyaratan Sistem
Sebelum menginstal, pastikan perangkat Anda memiliki:
* **Python 3.10** ke atas
* **Node.js v16** ke atas (untuk WhatsApp Gateway)
* **Git**

---

## 💻 Panduan Instalasi Lokal

### 1. Windows
1. **Clone Repositori**:
   ```bash
   git clone https://github.com/arief0rahman0/billing-internet.git
   cd billing-internet
   ```
2. **Setup Virtual Environment & Install Dependensi**:
   ```cmd
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. **Setup File Environment**:
   Salin berkas `.env.example` menjadi `.env` dan sesuaikan nilainya:
   ```cmd
   copy .env.example .env
   ```
4. **Jalankan WhatsApp Gateway**:
   ```bash
   cd wa-gateway
   npm install
   npm start
   ```
   *Pindai (scan) QR Code yang muncul di terminal menggunakan WhatsApp Anda.*
5. **Jalankan Aplikasi Web** (Buka terminal baru di folder utama):
   ```cmd
   venv\Scripts\activate
   python app.py
   ```
   Aplikasi akan berjalan di `http://127.0.0.1:5000`.

### 2. Linux (Ubuntu/Debian)
1. **Install Dependensi Sistem**:
   ```bash
   sudo apt update
   sudo apt install python3 python3-venv python3-pip nodejs npm git -y
   ```
2. **Clone & Setup Virtual Environment**:
   ```bash
   git clone https://github.com/arief0rahman0/billing-internet.git
   cd billing-internet
   python3 -m venv venv
   source venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
3. **Konfigurasi Environment**:
   ```bash
   cp .env.example .env
   # Edit file .env dan masukkan SECRET_KEY serta kredensial lainnya
   nano .env
   ```
4. **Jalankan WhatsApp Gateway**:
   ```bash
   cd wa-gateway
   npm install
   npm start
   ```
5. **Jalankan Aplikasi Flask**:
   Buka terminal baru, aktifkan venv, dan jalankan:
   ```bash
   source venv/bin/activate
   python3 app.py
   ```

### 3. macOS
1. **Install Python & Node.js** (Rekomendasi menggunakan Homebrew):
   ```bash
   brew install python node git
   ```
2. **Clone & Setup Virtual Environment**:
   ```bash
   git clone https://github.com/arief0rahman0/billing-internet.git
   cd billing-internet
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Konfigurasi berkas `.env`**:
   ```bash
   cp .env.example .env
   nano .env
   ```
4. **Jalankan WhatsApp Gateway**:
   ```bash
   cd wa-gateway
   npm install
   npm start
   ```
5. **Jalankan Aplikasi Flask**:
   Buka terminal baru, aktifkan venv, dan jalankan:
   ```bash
   source venv/bin/activate
   python3 app.py
   ```

---

## ☁️ Panduan Deploy di VPS (Ubuntu Server + Gunicorn + Nginx + Systemd)

Untuk menjalankan aplikasi ini secara permanen di server/VPS dengan domain dan SSL HTTPS:

### Langkah 1: Persiapan Awal VPS
Update package lists dan install library pendukung:
```bash
sudo apt update
sudo apt install python3-pip python3-venv nginx curl git -y
```

### Langkah 2: Mengatur Hak Akses Direktori
Karena Nginx berjalan sebagai user `www-data`, pastikan Nginx dapat membaca file di folder `/static/` aplikasi tanpa melanggar keamanan folder home:
```bash
sudo chmod o+x /home/arief0rahman
chmod o+rx /home/arief0rahman/billing-internet
chmod -R o+rx /home/arief0rahman/billing-internet/static
```

### Langkah 3: Konfigurasi Service Systemd (Gunicorn)
Buat berkas unit systemd agar aplikasi Flask berjalan di latar belakang secara otomatis saat booting.
```bash
sudo nano /etc/systemd/system/billing.service
```
Tempelkan konfigurasi berikut:
```ini
[Unit]
Description=Gunicorn instance to serve Billing Internet App
After=network.target

[Service]
User=arief0rahman
Group=www-data
WorkingDirectory=/home/arief0rahman/billing-internet
Environment="PATH=/home/arief0rahman/billing-internet/venv/bin"
Environment="FLASK_ENV=production"
ExecStart=/home/arief0rahman/billing-internet/venv/bin/gunicorn --workers 3 --bind 127.0.0.1:5000 wsgi:app

[Install]
WantedBy=multi-user.target
```
Simpan, lalu jalankan service:
```bash
sudo systemctl daemon-reload
sudo systemctl start billing
sudo systemctl enable billing
```

### Langkah 4: Konfigurasi Reverse Proxy Nginx
Buat berkas konfigurasi Nginx baru untuk menangani request HTTP/HTTPS dan melayani static files secara langsung:
```bash
sudo nano /etc/nginx/sites-available/billing
```
Isi dengan konfigurasi server block berikut:
```nginx
server {
    listen 80;
    server_name app1.billing-internet.web.id;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Optimasi serving static file langsung oleh Nginx
    location /static/ {
        alias /home/arief0rahman/billing-internet/static/;
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }
}
```
Aktifkan konfigurasi dan restart Nginx:
```bash
sudo ln -sf /etc/nginx/sites-available/billing /etc/nginx/sites-enabled/
sudo systemctl restart nginx
```

### Langkah 5: Pasang SSL HTTPS (Certbot / Let's Encrypt)
Jalankan Certbot untuk mengamankan komunikasi dengan SSL:
```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d app1.billing-internet.web.id
```
Ikuti instruksi di layar, dan pilih opsi untuk memaksa redirect lalu lintas HTTP ke HTTPS.

### Langkah 6: Jalankan WhatsApp Gateway di Background (PM2)
Gunakan PM2 (Process Manager) agar daemon WhatsApp Gateway Node.js berjalan terus menerus di VPS:
```bash
sudo npm install -y -g pm2
cd /home/arief0rahman/billing-internet/wa-gateway
npm install
pm2 start index.js --name "wa-gateway"
pm2 save
pm2 startup
```

---

## 🔒 Manajemen Keamanan & Pembaruan
Setiap kali Anda memperbarui template HTML atau logika server di VPS, jalankan perintah reload tanpa menghentikan total koneksi user:
```bash
kill -HUP $(pgrep -f "gunicorn.*wsgi:app")
```
Hal ini akan memaksa Gunicorn memuat ulang template dan script Python terbaru secara instan.
