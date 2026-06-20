import os
import sqlite3
from unittest.mock import patch

import pyotp
import pytest

import app as billing_app


@pytest.fixture
def client():
    test_db = "test_route_billing.db"
    if os.path.exists(test_db):
        os.remove(test_db)

    billing_app.app.config["TESTING"] = True
    billing_app.app.config["DATABASE"] = test_db
    billing_app.app.config["WTF_CSRF_ENABLED"] = False

    # Initialize the database
    billing_app.init_db()

    with billing_app.app.test_client() as client:
        yield client

    # Clean up test database
    if os.path.exists(test_db):
        try:
            os.remove(test_db)
        except OSError:
            pass


@pytest.fixture
def logged_in_client(client):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["role"] = "admin"
        sess["username"] = "admin"
    return client


@pytest.fixture
def operator_client(client):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["role"] = "operator"
        sess["username"] = "operator"
    return client


def test_login_page(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"Kode Keamanan 2FA" in response.data


def test_login_success(client):
    response = client.post(
        "/login",
        data={"username": "admin", "password": "admin123", "totp_code": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Kode Keamanan 2FA" not in response.data


def test_login_failure(client):
    response = client.post(
        "/login",
        data={"username": "admin", "password": "wrongpassword", "totp_code": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Username atau Password salah!" in response.data


def test_login_totp_failure(client):
    # Set TOTP secret for admin
    conn = billing_app.get_db_connection()
    conn.execute(
        "UPDATE admin_user SET totp_secret = 'BASE32SECRET3232' WHERE username = 'admin'"
    )
    conn.commit()
    conn.close()

    response = client.post(
        "/login",
        data={"username": "admin", "password": "admin123", "totp_code": "000000"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Kode Keamanan TOTP (6 Digit) Salah atau Kedaluwarsa!" in response.data


def test_login_totp_success(client):
    secret = pyotp.random_base32()
    conn = billing_app.get_db_connection()
    conn.execute(
        "UPDATE admin_user SET totp_secret = ? WHERE username = 'admin'", (secret,)
    )
    conn.commit()
    conn.close()

    totp = pyotp.TOTP(secret)
    valid_code = totp.now()

    response = client.post(
        "/login",
        data={"username": "admin", "password": "admin123", "totp_code": valid_code},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Kode Keamanan 2FA" not in response.data


def test_logout(logged_in_client):
    response = logged_in_client.get("/logout", follow_redirects=True)
    assert response.status_code == 200
    assert b"Kode Keamanan 2FA" in response.data


@patch("app.send_whatsapp", return_value=True)
def test_tambah_pelanggan(mock_send, logged_in_client):
    response = logged_in_client.post(
        "/tambah_pelanggan",
        data={"nama": "Test Pelanggan", "tagihan": "100000", "no_wa": "081234567890"},
        follow_redirects=True,
    )

    assert response.status_code == 200

    conn = billing_app.get_db_connection()
    pelanggan = conn.execute(
        "SELECT * FROM pelanggan WHERE nama = 'Test Pelanggan'"
    ).fetchone()
    assert pelanggan is not None
    assert pelanggan["tagihan_bulanan"] == 100000
    assert pelanggan["no_wa"] == "081234567890"

    pembayaran = conn.execute(
        "SELECT * FROM pembayaran WHERE pelanggan_id = ?", (pelanggan["id"],)
    ).fetchone()
    assert pembayaran is not None
    assert pembayaran["jumlah_bayar"] == 100000
    assert pembayaran["status"] == "Belum Bayar"
    conn.close()


def test_edit_pelanggan(logged_in_client):
    conn = billing_app.get_db_connection()
    conn.execute(
        "INSERT INTO pelanggan (nama, tagihan_bulanan, no_wa) VALUES ('Pelanggan Asli', 150000, '081234')"
    )
    conn.commit()
    pelanggan = conn.execute(
        "SELECT id FROM pelanggan WHERE nama = 'Pelanggan Asli'"
    ).fetchone()
    pelanggan_id = pelanggan["id"]
    conn.close()

    response = logged_in_client.post(
        f"/edit_pelanggan/{pelanggan_id}",
        data={"nama": "Pelanggan Diedit", "tagihan": "200000", "no_wa": "085678"},
        follow_redirects=True,
    )

    assert response.status_code == 200

    conn = billing_app.get_db_connection()
    p_updated = conn.execute(
        "SELECT * FROM pelanggan WHERE id = ?", (pelanggan_id,)
    ).fetchone()
    assert p_updated["nama"] == "Pelanggan Diedit"
    assert p_updated["tagihan_bulanan"] == 200000
    assert p_updated["no_wa"] == "085678"
    conn.close()


def test_set_lunas(logged_in_client):
    conn = billing_app.get_db_connection()
    conn.execute(
        "INSERT INTO pelanggan (nama, tagihan_bulanan, no_wa) VALUES ('Pelanggan Tagihan', 120000, '08123')"
    )
    conn.commit()
    pelanggan = conn.execute(
        "SELECT id FROM pelanggan WHERE nama = 'Pelanggan Tagihan'"
    ).fetchone()
    pelanggan_id = pelanggan["id"]

    conn.execute(
        "INSERT INTO pembayaran (pelanggan_id, bulan_tagihan, jumlah_bayar, tanggal_bayar, status) VALUES (?, '2026-06', 120000, '-', 'Belum Bayar')",
        (pelanggan_id,),
    )
    conn.commit()
    pembayaran = conn.execute(
        "SELECT id FROM pembayaran WHERE pelanggan_id = ?", (pelanggan_id,)
    ).fetchone()
    pembayaran_id = pembayaran["id"]
    conn.close()

    response = logged_in_client.post(f"/lunas/{pembayaran_id}", follow_redirects=True)
    assert response.status_code == 200

    conn = billing_app.get_db_connection()
    pembayaran_updated = conn.execute(
        "SELECT * FROM pembayaran WHERE id = ?", (pembayaran_id,)
    ).fetchone()
    assert pembayaran_updated["status"] == "Lunas"
    assert pembayaran_updated["tanggal_bayar"] != "-"
    conn.close()


def test_hapus_pelanggan(logged_in_client):
    conn = billing_app.get_db_connection()
    conn.execute(
        "INSERT INTO pelanggan (nama, tagihan_bulanan, no_wa) VALUES ('Hapus Aku', 50000, '0811')"
    )
    conn.commit()
    pelanggan = conn.execute(
        "SELECT id FROM pelanggan WHERE nama = 'Hapus Aku'"
    ).fetchone()
    pelanggan_id = pelanggan["id"]
    conn.close()

    response = logged_in_client.post(
        f"/hapus_pelanggan/{pelanggan_id}", follow_redirects=True
    )
    assert response.status_code == 200

    conn = billing_app.get_db_connection()
    p_deleted = conn.execute(
        "SELECT * FROM pelanggan WHERE id = ?", (pelanggan_id,)
    ).fetchone()
    assert p_deleted is None
    conn.close()


def test_hapus_pelanggan_denied_for_operator(operator_client):
    conn = billing_app.get_db_connection()
    conn.execute(
        "INSERT INTO pelanggan (nama, tagihan_bulanan, no_wa) VALUES ('Hapus Aku Op', 50000, '0811')"
    )
    conn.commit()
    pelanggan = conn.execute(
        "SELECT id FROM pelanggan WHERE nama = 'Hapus Aku Op'"
    ).fetchone()
    pelanggan_id = pelanggan["id"]
    conn.close()

    response = operator_client.post(
        f"/hapus_pelanggan/{pelanggan_id}", follow_redirects=True
    )
    assert response.status_code == 200

    conn = billing_app.get_db_connection()
    p_deleted = conn.execute(
        "SELECT * FROM pelanggan WHERE id = ?", (pelanggan_id,)
    ).fetchone()
    assert p_deleted is not None
    conn.close()
