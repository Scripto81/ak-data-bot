import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import datetime
import requests

app = FastAPI()
security = HTTPBearer()
SECRET_KEY = os.getenv("JWT_SECRET")

# Database connection function
def get_db_connection():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
        cursor_factory=RealDictCursor,
        sslmode="require"
    )

# Initialize database tables
def init_db():
    conn = get_db_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS xp_data (
                    userId TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    xp INTEGER NOT NULL,
                    offenseData JSONB,
                    last_updated BIGINT
                );
                CREATE TABLE IF NOT EXISTS xp_history (
                    id SERIAL PRIMARY KEY,
                    userId TEXT REFERENCES xp_data(userId),
                    xp_change INTEGER,
                    timestamp BIGINT
                );
                CREATE TABLE IF NOT EXISTS stored_xp (
                    discord_id TEXT PRIMARY KEY,
                    stored_xp INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS user_mappings (
                    discord_id TEXT PRIMARY KEY,
                    roblox_user_id TEXT NOT NULL,
                    roblox_username TEXT NOT NULL,
                    verified_at BIGINT
                );
                CREATE INDEX IF NOT EXISTS idx_username ON xp_data (LOWER(username));
            """)
    conn.close()

init_db()

# JWT token verification
def verify_token(auth: HTTPAuthorizationCredentials = Depends(security)):
    try:
        jwt.decode(auth.credentials, SECRET_KEY, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Verify user endpoint
@app.post("/verify_user", dependencies=[Depends(verify_token)])
async def verify_user(data: dict):
    discord_id = data.get("discord_id")
    roblox_user_id = data.get("roblox_user_id")
    verification_code = data.get("verification_code")
    if not all([discord_id, roblox_user_id, verification_code]):
        raise HTTPException(status_code=400, detail="Missing parameters")
    
    url = f"https://users.roblox.com/v1/users/{roblox_user_id}"
    resp = requests.get(url, timeout=10)
    if resp.status_code != 200 or verification_code not in resp.json().get("description", ""):
        raise HTTPException(status_code=400, detail="Verification code not found in bio")
    
    roblox_username = resp.json().get("name")
    timestamp = int(datetime.datetime.utcnow().timestamp())
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO user_mappings (discord_id, roblox_user_id, roblox_username, verified_at) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (discord_id) DO UPDATE SET roblox_user_id = %s, roblox_username = %s, verified_at = %s",
                    (discord_id, roblox_user_id, roblox_username, timestamp, roblox_user_id, roblox_username, timestamp)
                )
        return {"status": "success", "roblox_username": roblox_username}
    finally:
        conn.close()

# Set XP endpoint
@app.post("/set_xp", dependencies=[Depends(verify_token)])
async def set_xp(data: dict):
    user_id = data.get("userId")
    new_xp = data.get("xp")
    if not all([user_id, new_xp is not None]):
        raise HTTPException(status_code=400, detail="Missing userId or xp")
    if not isinstance(new_xp, int) or new_xp < 0:
        raise HTTPException(status_code=400, detail="XP must be non-negative")
    timestamp = int(datetime.datetime.utcnow().timestamp())
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT xp, username FROM xp_data WHERE userId = %s", (str(user_id),))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="User not found")
                old_xp = row["xp"]
                cur.execute(
                    "UPDATE xp_data SET xp = %s, last_updated = %s WHERE userId = %s",
                    (new_xp, timestamp, str(user_id))
                )
                if new_xp != old_xp:
                    cur.execute(
                        "INSERT INTO xp_history (userId, xp_change, timestamp) VALUES (%s, %s, %s)",
                        (str(user_id), new_xp - old_xp, timestamp)
                    )
        return {"status": "success", "newXp": new_xp}
    finally:
        conn.close()

# Store XP endpoint
@app.post("/store_xp", dependencies=[Depends(verify_token)])
async def store_xp(data: dict):
    discord_id = data.get("discord_id")
    xp_to_store = data.get("xp")
    if not all([discord_id, xp_to_store is not None]):
        raise HTTPException(status_code=400, detail="Missing discord_id or xp")
    if not isinstance(xp_to_store, int) or xp_to_store < 0:
        raise HTTPException(status_code=400, detail="XP must be non-negative")
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO stored_xp (discord_id, stored_xp) VALUES (%s, %s) "
                    "ON CONFLICT (discord_id) DO UPDATE SET stored_xp = stored_xp + %s",
                    (discord_id, xp_to_store, xp_to_store)
                )
        return {"status": "success", "stored_xp": xp_to_store}
    finally:
        conn.close()

# Redeem XP endpoint (via Discord)
@app.post("/redeem_xp", dependencies=[Depends(verify_token)])
async def redeem_xp(data: dict):
    discord_id = data.get("discord_id")
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT roblox_user_id FROM user_mappings WHERE discord_id = %s", (discord_id,))
                mapping = cur.fetchone()
                if not mapping:
                    raise HTTPException(status_code=400, detail="User not verified")
                roblox_user_id = mapping["roblox_user_id"]
                
                cur.execute("SELECT stored_xp FROM stored_xp WHERE discord_id = %s", (discord_id,))
                row = cur.fetchone()
                if not row or row["stored_xp"] <= 0:
                    raise HTTPException(status_code=404, detail="No stored XP")
                stored_xp = row["stored_xp"]
                
                cur.execute("SELECT xp FROM xp_data WHERE userId = %s", (roblox_user_id,))
                xp_row = cur.fetchone()
                if not xp_row:
                    raise HTTPException(status_code=404, detail="User not found in xp_data")
                current_xp = xp_row["xp"]
                new_xp = current_xp + stored_xp
                timestamp = int(datetime.datetime.utcnow().timestamp())
                
                cur.execute(
                    "UPDATE xp_data SET xp = %s, last_updated = %s WHERE userId = %s",
                    (new_xp, timestamp, roblox_user_id)
                )
                cur.execute(
                    "INSERT INTO xp_history (userId, xp_change, timestamp) VALUES (%s, %s, %s)",
                    (roblox_user_id, stored_xp, timestamp)
                )
                cur.execute("DELETE FROM stored_xp WHERE discord_id = %s", (discord_id,))
        return {"status": "success", "redeemed_xp": stored_xp, "new_xp": new_xp}
    finally:
        conn.close()

# Redeem XP in-game endpoint (via Roblox)
@app.post("/redeem_xp_ingame")
async def redeem_xp_ingame(data: dict):
    roblox_user_id = data.get("roblox_user_id")
    if not roblox_user_id:
        raise HTTPException(status_code=400, detail="Missing roblox_user_id")
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT discord_id FROM user_mappings WHERE roblox_user_id = %s", (roblox_user_id,))
                mapping = cur.fetchone()
                if not mapping:
                    raise HTTPException(status_code=400, detail="User not verified")
                discord_id = mapping["discord_id"]
                
                cur.execute("SELECT stored_xp FROM stored_xp WHERE discord_id = %s", (discord_id,))
                row = cur.fetchone()
                if not row or row["stored_xp"] <= 0:
                    raise HTTPException(status_code=404, detail="No stored XP")
                stored_xp = row["stored_xp"]
                
                cur.execute("SELECT xp FROM xp_data WHERE userId = %s", (roblox_user_id,))
                xp_row = cur.fetchone()
                if not xp_row:
                    raise HTTPException(status_code=404, detail="User not found in xp_data")
                current_xp = xp_row["xp"]
                new_xp = current_xp + stored_xp
                timestamp = int(datetime.datetime.utcnow().timestamp())
                
                cur.execute(
                    "UPDATE xp_data SET xp = %s, last_updated = %s WHERE userId = %s",
                    (new_xp, timestamp, roblox_user_id)
                )
                cur.execute(
                    "INSERT INTO xp_history (userId, xp_change, timestamp) VALUES (%s, %s, %s)",
                    (roblox_user_id, stored_xp, timestamp)
                )
                cur.execute("DELETE FROM stored_xp WHERE discord_id = %s", (discord_id,))
        return {"status": "success", "redeemed_xp": stored_xp, "new_xp": new_xp}
    finally:
        conn.close()
