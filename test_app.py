import os
import sqlite3

import app


def test_app_initialization():
    # Set to test database name
    test_db = "test_pembayaran_internet.db"
    if os.path.exists(test_db):
        os.remove(test_db)

    app.app.config["DATABASE"] = test_db

    # Test DB init
    app.init_db()

    # Check if DB file exists
    assert os.path.exists(test_db), "Database file was not created"

    # Verify tables exist
    conn = sqlite3.connect(test_db)
    cursor = conn.cursor()

    # Verify admin_user table
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='admin_user'"
    )
    assert cursor.fetchone() is not None, "admin_user table was not created"

    # Verify pelanggan table
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pelanggan'"
    )
    assert cursor.fetchone() is not None, "pelanggan table was not created"

    # Verify pembayaran table
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pembayaran'"
    )
    assert cursor.fetchone() is not None, "pembayaran table was not created"

    conn.close()

    # Clean up
    os.remove(test_db)


if __name__ == "__main__":
    try:
        test_app_initialization()
        print("Test passed!")
    except AssertionError as e:
        print(f"Test failed: {e}")
        exit(1)
    except Exception as e:
        print(f"Test errored: {e}")
        exit(1)
