import os
import sys
import sqlite3
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Enable CORS for local cross-origin development

# Port configuration (default 5005)
PORT = int(os.environ.get("PORTAL_PORT", "5005"))

# Database path
DB_PATH = os.environ.get("STEG_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "stegsafe.db"))

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database schema."""
    conn = get_db_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stego_images (
                id TEXT PRIMARY KEY,
                image_data BLOB NOT NULL,
                created_at TIMESTAMP NOT NULL,
                expires_at TIMESTAMP NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()

def cleanup_daemon():
    """Background thread to delete expired stego images."""
    print("[CLEANUP] Started background expiration sweep daemon.")
    while True:
        try:
            time.sleep(60)  # Sweep every minute
            conn = get_db_connection()
            now = datetime.now(timezone.utc).isoformat()
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM stego_images WHERE expires_at < ?", (now,))
                deleted = cursor.rowcount
                if deleted > 0:
                    conn.commit()
                    print(f"[CLEANUP] Deleted {deleted} expired stego-image(s) at {now}")
            except Exception as e:
                print(f"[CLEANUP ERROR] Failed to delete expired entries: {e}")
            finally:
                conn.close()
        except Exception as e:
            print(f"[CLEANUP ERROR] Daemon loop encountered error: {e}")

@app.route("/api/upload", methods=["POST"])
def upload_image():
    """Receives a stego PNG image, saves it, and returns a unique ID."""
    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400
        
    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400
        
    # Read image binary data
    image_bytes = file.read()
    if len(image_bytes) == 0:
        return jsonify({"error": "Empty image file"}), 400
        
    # Parse custom expiration from form (in minutes, default 1440 = 24 hours)
    try:
        expiry_minutes = int(request.form.get("expiry", 1440))
        if expiry_minutes <= 0:
            expiry_minutes = 1440
    except ValueError:
        expiry_minutes = 1440
        
    # Generate unique 16-character hex ID
    image_id = secrets.token_hex(8)
    
    # Calculate dates
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=expiry_minutes)
    
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO stego_images (id, image_data, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (image_id, sqlite3.Binary(image_bytes), now.isoformat(), expires.isoformat())
        )
        conn.commit()
    except Exception as e:
        return jsonify({"error": f"Database write failed: {str(e)}"}), 500
    finally:
        conn.close()
        
    return jsonify({"id": image_id}), 201

@app.route("/api/image/<image_id>", methods=["GET"])
def get_image(image_id):
    """Retrieves a stego image by ID and immediately deletes it (burn-on-read)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT image_data, expires_at FROM stego_images WHERE id = ?", (image_id,))
        row = cursor.fetchone()
        
        if not row:
            return jsonify({"error": "Image not found or already burned"}), 404
            
        # Check expiration
        expires_at = datetime.fromisoformat(row["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            # Clean it up immediately
            cursor.execute("DELETE FROM stego_images WHERE id = ?", (image_id,))
            conn.commit()
            return jsonify({"error": "Image has expired"}), 404
            
        # Burn-on-read: Delete from database immediately
        cursor.execute("DELETE FROM stego_images WHERE id = ?", (image_id,))
        conn.commit()
        
        # Send raw binary bytes back as PNG
        from io import BytesIO
        return send_file(
            BytesIO(row["image_data"]),
            mimetype="image/png",
            download_name=f"stego_{image_id}.png"
        )
        
    except Exception as e:
        return jsonify({"error": f"Database read failed: {str(e)}"}), 500
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()
    
    # Start cleanup daemon as background thread
    daemon = threading.Thread(target=cleanup_daemon, daemon=True)
    daemon.start()
    
    print(f"[*] StegSafe API Server running on port {PORT}...")
    app.run(host="0.0.0.0", port=PORT)
