import unittest
import os
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone

# Set up test database path before imports
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_stegsafe.db")
os.environ["STEG_DB_PATH"] = TEST_DB_PATH

# Import server code
# We must adjust Python path to import server.py which is in the parent directory
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import app, init_db, get_db_connection

class TestStegSafe(unittest.TestCase):
    
    def setUp(self):
        # Initialize test DB and clear stego_images table
        init_db()
        self.conn = get_db_connection()
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM stego_images")
        self.conn.commit()
        
        # Flask test client
        self.client = app.test_client()

    def tearDown(self):
        self.conn.close()
        # Clean up database file
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except PermissionError:
                pass

    def test_database_initialization(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        self.assertIn("stego_images", tables)

    def test_upload_and_retrieve_burn(self):
        # 1. Post a dummy PNG image payload
        dummy_image = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
        data = {
            "image": (io.BytesIO(dummy_image), "test.png"),
            "expiry": "60"
        }
        
        response = self.client.post(
            "/api/upload",
            data=data,
            content_type="multipart/form-data"
        )
        self.assertEqual(response.status_code, 201)
        res_data = json.loads(response.data)
        self.assertIn("id", res_data)
        image_id = res_data["id"]
        
        # Verify it exists in database
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM stego_images WHERE id = ?", (image_id,))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["image_data"], dummy_image)
        
        # 2. Retrieve the image (Burn on Read)
        response_get = self.client.get(f"/api/image/{image_id}")
        self.assertEqual(response_get.status_code, 200)
        self.assertEqual(response_get.content_type, "image/png")
        self.assertEqual(response_get.data, dummy_image)
        
        # 3. Try to retrieve again (Should be burned/deleted)
        response_burned = self.client.get(f"/api/image/{image_id}")
        self.assertEqual(response_burned.status_code, 404)
        
        # Verify it was physically deleted from SQLite
        cursor.execute("SELECT * FROM stego_images WHERE id = ?", (image_id,))
        self.assertIsNone(cursor.fetchone())

    def test_missing_image_payload(self):
        # Post without image field
        response = self.client.post(
            "/api/upload",
            data={"expiry": "60"},
            content_type="multipart/form-data"
        )
        self.assertEqual(response.status_code, 400)
        
        # Post empty file
        data = {
            "image": (io.BytesIO(b""), "empty.png"),
            "expiry": "60"
        }
        response = self.client.post(
            "/api/upload",
            data=data,
            content_type="multipart/form-data"
        )
        self.assertEqual(response.status_code, 400)

    def test_expired_image(self):
        # 1. Manually insert an expired image row
        dummy_image = b"dummy"
        conn = get_db_connection()
        cursor = conn.cursor()
        expires_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()  # Expired 10s ago
        cursor.execute(
            "INSERT INTO stego_images (id, image_data, created_at, expires_at) VALUES (?, ?, ?, ?)",
            ("expired-img-id", sqlite3.Binary(dummy_image), datetime.now(timezone.utc).isoformat(), expires_at)
        )
        conn.commit()
        conn.close()
        
        # 2. Retrieve expired image
        response = self.client.get("/api/image/expired-img-id")
        self.assertEqual(response.status_code, 404)
        
        # Verify it is deleted from database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM stego_images WHERE id = ?", ("expired-img-id",))
        self.assertIsNone(cursor.fetchone())
        conn.close()

if __name__ == "__main__":
    unittest.main()
