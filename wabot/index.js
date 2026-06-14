const express = require('express');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode');

const app = express();
app.use(express.json());

let latestQrBase64 = null;
let isBotConnected = false;

// Mengaktifkan autentikasi tersimpan agar tidak perlu berkali-kali scan QR
const client = new Client({
    authStrategy: new LocalAuth({ dataPath: './session_data' }),
    puppeteer: { args: ['--no-sandbox', '--disable-setuid-sandbox'] }
});

client.on('qr', (qr) => {
    isBotConnected = false;
    qrcode.toDataURL(qr, (err, url) => {
        if (!err) latestQrBase64 = url;
    });
});

client.on('ready', () => {
    console.log('WhatsApp Bot Ready & Connected!');
    isBotConnected = true;
    latestQrBase64 = null;
});

client.on('disconnected', () => {
    console.log('WhatsApp Bot Disconnected!');
    isBotConnected = false;
    latestQrBase64 = null;
});

// Endpoint untuk cek status koneksi ke Flask
app.get('/status', (req, res) => {
    res.json({ connected: isBotConnected, qr: latestQrBase64 });
});

// Endpoint untuk kirim pesan WA otomatis
app.post('/send', async (req, res) => {
    const { target, message } = req.body;
    if (!isBotConnected) return res.status(500).json({ error: 'Bot Belum Terkoneksi' });
    
    try {
        // Format otomatis nomor HP ke format WhatsApp standar global
        let formattedTarget = target.replace(/[^0-9]/g, '');
        if (formattedTarget.startsWith('0')) formattedTarget = '62' + formattedTarget.slice(1);
        if (!formattedTarget.endsWith('@c.us')) formattedTarget += '@c.us';

        await client.sendMessage(formattedTarget, message);
        res.json({ status: 'Success' });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

// Endpoint untuk logout paksa
app.post('/logout', async (req, res) => {
    try {
        await client.logout();
        isBotConnected = false;
        latestQrBase64 = null;
        res.json({ status: 'Logged out successfully' });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

client.initialize();
app.listen(3000, () => console.log('WA Gateway Server running on port 3000'));
