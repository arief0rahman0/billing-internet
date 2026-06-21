const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
} = require("@whiskeysockets/baileys");
const express = require("express");
const qrcode = require("qrcode");
const app = express();

app.use(express.urlencoded({ extended: true }));
app.use(express.json());

let sock;
let qrCodeText = null;
let isConnected = false;

async function connectToWhatsApp() {
  const { state, saveCreds } = await useMultiFileAuthState("auth_info_baileys");

  sock = makeWASocket({
    auth: state,
    logger: require("pino")({ level: "silent" }),
    printQRInTerminal: false,
    browser: ["Billing Internet", "Chrome", "1.0.0"],
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;

    // Simpan text QR ke dalam variabel global jika ada
    if (qr) {
      try {
        qrCodeText = await qrcode.toDataURL(qr);
      } catch (err) {
        qrCodeText = qr;
      }
      isConnected = false;
    }

    if (connection === "close") {
      isConnected = false;
      qrCodeText = null; // Reset QR agar barcode baru segera di-generate
      const statusCode = lastDisconnect.error?.output?.statusCode;
      const shouldReconnect =
        statusCode !== DisconnectReason.loggedOut && statusCode !== 401;

      console.log(
        `Koneksi terputus (Status: ${statusCode}), mencoba menghubungkan ulang dalam 3 detik...`,
        shouldReconnect,
      );

      if (shouldReconnect) {
        setTimeout(() => {
          connectToWhatsApp();
        }, 3000);
      } else {
        console.log(
          "Sesi tidak valid (Logged Out). Restart service untuk barcode baru...",
        );
        try {
          require("fs").rmSync("auth_info_baileys", {
            recursive: true,
            force: true,
          });
        } catch (e) {}
        setTimeout(() => process.exit(1), 500);
      }
    } else if (connection === "open") {
      isConnected = true;
      qrCodeText = null; // Hapus QR karena sudah sukses terhubung
      console.log("✅ WhatsApp Bot Berhasil Terhubung!");
    }
  });
}

// 🟢 ENDPOINT BARU 1: Cek status koneksi dan ambil text QR untuk Flask
app.get("/status", (req, res) => {
  res.json({ connected: isConnected, qr: qrCodeText });
});

// ========================================================
// REVISI ENDPOINT LOGOUT: PASTIKAN HAPUS FOLDER SAMPAI BERSIH
// ========================================================
app.post("/logout", async (req, res) => {
  const fs = require("fs"); // Panggil library file system
  try {
    qrCodeText = null;
    isConnected = false;

    if (sock) {
      await sock.logout().catch(() => {});
      sock.end();
    }
  } catch (err) {
    console.log("Error saat socket logout:", err.message);
  }

  // Paksa hapus folder auth secara fisik dari harddisk VPS
  try {
    fs.rmSync("auth_info_baileys", { recursive: true, force: true });
    console.log("🗑️ Folder auth_info_baileys berhasil dihapus bersih!");
    res.json({ status: true, message: "Berhasil keluar dan reset total sesi" });
  } catch (fsErr) {
    console.log("Gagal hapus folder:", fsErr.message);
    res.json({ status: true, message: "Reset selesai dengan catatan" });
  }

  // Matikan proses agar systemd me-restart dan langsung generate QR baru
  setTimeout(() => process.exit(1), 500);
});
// ========================================================

// Endpoint kirim pesan (Sama seperti sebelumnya)
app.post("/send", async (req, res) => {
  let target = req.body.target;
  const message = req.body.message;

  if (
    typeof target !== "string" ||
    typeof message !== "string" ||
    !target.trim() ||
    !message.trim()
  )
    return res
      .status(400)
      .json({ status: false, error: "Parameter tidak valid" });

  if (target.startsWith("0")) {
    target = "62" + target.slice(1);
  }
  if (!target.endsWith("@s.whatsapp.net")) {
    target = target + "@s.whatsapp.net";
  }

  try {
    await sock.sendMessage(target, { text: message });
    res.json({ status: true, message: "Pesan terkirim" });
  } catch (err) {
    res.status(500).json({ status: false, error: err.message });
  }
});

connectToWhatsApp();
app.listen(3000, "127.0.0.1", () => {
  console.log("API WA Gateway aktif di http://127.0.0.1:3000");
});
