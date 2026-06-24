# Laporan Perubahan & Analisa Sistem (Changelog)
**Tanggal:** 22 Juni 2026

Dokumen ini merangkum semua pembaruan keamanan, perbaikan *bug*, serta hasil analisa menyeluruh (*static analysis*) yang telah dilakukan pada kode *Billing Internet*.

---

## 1. Peningkatan Fitur Keamanan (Proteksi Form)
Langkah-langkah di bawah ini ditambahkan untuk mencegah penyalahgunaan sistem oleh *bot* maupun input berganda oleh pengguna:

*   **Honeypot Anti-Bot:** 
    *   **Lokasi:** `templates/login.html` & `templates/index.html` (Form Tambah Anggota).
    *   **Deskripsi:** Menambahkan kolom input tersembunyi (`hp_field`). Pengguna biasa tidak akan melihatnya, tetapi *script* otomatis (*bot*) umumnya akan mengisinya. Jika sistem mendeteksi input pada kolom ini, *request* otomatis ditolak (`app.py`).
*   **Pencegahan Double-Submission:**
    *   **Lokasi:** `templates/login.html` & `templates/index.html` (Form Tambah Anggota).
    *   **Deskripsi:** Menambahkan script bawaan (`onsubmit`) untuk menonaktifkan (*disable*) tombol *submit* setelah diklik pertama kali, sehingga tidak terjadi *request* ganda secara beruntun.
*   **Penambahan Rate Limiter:**
    *   **Lokasi:** `app.py` pada fungsi `tambah_pelanggan`.
    *   **Deskripsi:** Mengaplikasikan `@limiter.limit("5 per minute")` khusus untuk endpoint tambah pelanggan agar terhindar dari *spam* masif.

## 2. Perbaikan Bug (Kode Rusak)
Analisis dilakukan pada *frontend* maupun *backend*, dan ditemukan satu *bug* kritikal terkait interaksi JavaScript yang telah diperbaiki.

*   **Perbaikan Konflik Konfirmasi "Hapus Pelanggan" (Double-Dialog Bug):**
    *   **Lokasi:** `templates/index.html`.
    *   **Masalah Awal:** Tombol hapus memiliki *inline event* `onclick="return confirm(...)"` yang berbenturan dengan *event listener* global yang juga mengeksekusi `confirm()`. Hal ini menyebabkan konfirmasi ganda, dan pembatalan dialog pertama bisa tertimpa oleh dialog kedua sehingga data tetap terhapus.
    *   **Solusi:** Kode `onclick="return confirm(...)"` dihapus dan diganti menjadi atribut `data-confirm`. Logika *event listener global* diperbarui untuk mengenali atribut *data-confirm* ini sehingga dialog konfirmasi yang muncul hanya satu kali dengan penanganan "Batal" yang solid dan aman.

## 3. Hasil Analisa Kode Keseluruhan (*Clean Code*)
Berdasarkan hasil pemindaian kode mendalam (*vulture* dan *flake8*) pada seluruh arsip proyek:

*   **Bebas Dead Code:** Seluruh *route* di `app.py`, *helper function* (seperti `generate_tagihan_otomatis()`), serta *template html* secara aktif dipanggil dan berfungsi dengan benar. Tidak ditemukan file "sampah".
*   **Stabilitas Basis Data:** Relasi dan *foreign key constraints* pada basis data SQLite dipastikan menyala dengan perintah `PRAGMA foreign_keys = ON;`. Fitur hapus berantai (`ON DELETE CASCADE`) untuk sinkronisasi pembayaran berjalan stabil tanpa *orphan data*.
*   **Manajemen Socket WA Gateway:** Endpoint `/logout` pada `server.js` terbukti kuat melakukan pembersihan memori (*hard force remove* direktori) untuk mengatasi kebocoran *memory* (atau file *session* yang *corrupt*) pada *library* Baileys.

*Semua pembaruan ini sudah diterapkan dan tersimpan langsung ke dalam source code tanpa mengganggu jalannya sistem.*
