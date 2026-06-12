#!/bin/bash
echo "Memulai update aplikasi dari GitHub..."
git pull origin main
sudo systemctl restart billing
echo "Selesai! Aplikasi berhasil diperbarui dan dijalankan ulang."
