
## Pembelajaran dari Sesi Keamanan dan WA Gateway (20 Juni 2026)
1. **Keamanan Aplikasi (CSRF)**: Semua rute yang mengubah status (seperti `/lunas/<id>`, `/hapus_pelanggan/<id>`) harus menggunakan method `POST` yang dilindungi oleh `Flask-WTF` (CSRFProtect). Jangan gunakan method `GET` untuk tindakan modifikasi data karena rentan terhadap peretasan CSRF. Di sisi frontend, jika ada aksi berupa tautan (link), intercept dengan JavaScript dan buat *form* dinamis untuk mem-POST token CSRF.
2. **Penanganan Barcode (QR Code) WA Gateway**: *Service* Baileys pada Node.js mengembalikan QR dalam bentuk gambar dengan format Base64 (`data:image/png;base64,...`). Pada file `whatsapp.html`, jangan melempar output Base64 mentah ke `qrcode.js` karena akan error. Sebaliknya, injeksi dengan `{{ status.qr | tojson | safe }}` dan tampilkan menggunakan tag `<img>` standar. Filter `tojson | safe` sangat penting agar *newlines* dan karakter khusus tidak merusak *syntax* JavaScript.

