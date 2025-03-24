import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import datetime
import json
import requests

app = FastAPI()
security = HTTPBearer()
SECRET_KEY = os.getenv("JWT_SECRET")

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

def verify_token(auth: HTTPAuthorizationCredentials = Depends(security)):
    try:
        jwt.decode(auth.credentials, SECRET_KEY, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

@app.get('/')
async def health_check():
    return {"status": "ok"}

@app.post('/update_xp')
async def update_xp(data: dict):
    try:
        user_id = data.get('userId')
        username = data.get('username')
        xp = data.get('xp')
        offense_data = data.get('offenseData')
        timestamp = data.get('timestamp')
        if not all([user_id, username, xp is not None, timestamp is not None]):
            raise HTTPException(status_code=400, detail='Missing required data')
        if not isinstance(xp, int) or xp < 0:
            raise HTTPException(status_code=400, detail='XP must be a non-negative integer')
        if not isinstance(timestamp, int):
            raise HTTPException(status_code=400, detail='Timestamp must be an integer')
        offense_json = json.dumps(offense_data) if offense_data is not None else None
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT xp, last_updated FROM xp_data WHERE userId = %s", (str(user_id),))
                row = cur.fetchone()
                old_xp = row['xp'] if row else 0
                old_timestamp = row['last_updated'] if row else 0
                if timestamp <= old_timestamp:
                    return {"status": "ignored", "reason": "Older timestamp"}
                xp_change = xp - old_xp
                cur.execute("""
                    INSERT INTO xp_data (userId, username, xp, offenseData, last_updated)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (userId) DO UPDATE SET
                        username=excluded.username,
                        xp=excluded.xp,
                        offenseData=excluded.offenseData,
                        last_updated=excluded.last_updated
                """, (str(user_id), username, xp, offense_json, timestamp))
                if xp_change != 0:
                    cur.execute("INSERT INTO xp_history (userId, xp_change, timestamp) VALUES (%s, %s, %s)",
                                (str(user_id), xp_change, timestamp))
        conn.close()
        return {"status": "success", "xp": xp, "timestamp": timestamp}
    except Exception as e:
        return {"error": "Internal server error", "details": str(e)}

@app.get('/get_user_data')
async def get_user_data(request: Request):
    try:
        username = request.query_params.get('username')
        if not username:
            raise HTTPException(status_code=400, detail='Username parameter is missing')
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM xp_data WHERE LOWER(username)=%s", (username.lower(),))
                row = cur.fetchone()
        if row:
            offense_data = json.loads(row['offenseData']) if row['offenseData'] else {}
            return {
                'userId': row['userId'],
                'username': row['username'],
                'xp': row['xp'],
                'offenseData': offense_data,
                'timestamp': row['last_updated']
            }
        raise HTTPException(status_code=404, detail='User not found')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.post('/set_xp', dependencies=[Depends(verify_token)])
async def set_xp(data: dict):
    try:
        user_id = data.get('userId')
        new_xp = data.get('xp')
        if not all([user_id, new_xp is not None]):
            raise HTTPException(status_code=400, detail='Missing userId or xp')
        if not isinstance(new_xp, int) or new_xp < 0:
            raise HTTPException(status_code=400, detail='XP must be a non-negative integer')
        timestamp = int(datetime.datetime.utcnow().timestamp())
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT xp, username FROM xp_data WHERE userId = %s", (str(user_id),))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail='User not found')
                old_xp = row["xp"]
                username = row["username"]
                xp_change = new_xp - old_xp
                cur.execute("UPDATE xp_data SET xp = %s, last_updated = %s WHERE userId = %s",
                            (new_xp, timestamp, str(user_id)))
                if xp_change != 0:
                    cur.execute("INSERT INTO xp_history (userId, xp_change, timestamp) VALUES (%s, %s, %s)",
                                (str(user_id), xp_change, timestamp))
        conn.close()
        return {"status": "success", "newXp": new_xp, "timestamp": timestamp}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get('/leaderboard')
async def get_leaderboard(request: Request):
    try:
        limit = min(int(request.query_params.get('limit', 10)), 50)
        if limit <= 0:
            raise HTTPException(status_code=400, detail='Limit must be positive')
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT username, xp FROM xp_data ORDER BY xp DESC LIMIT %s", (limit,))
                leaderboard = [dict(row) for row in cur.fetchall()]
        conn.close()
        return {'leaderboard': leaderboard}
    except ValueError:
        raise HTTPException(status_code=400, detail='Limit must be an integer')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get('/get_group_rank')
async def get_group_rank(request: Request):
    try:
        user_id = request.query_params.get('userId')
        group_id = request.query_params.get('groupId')
        if not all([user_id, group_id]):
            raise HTTPException(status_code=400, detail='Missing userId or groupId')
        roblox_api_key = os.getenv("ROBLOX_API_KEY")
        if not roblox_api_key:
            raise HTTPException(status_code=500, detail='Server configuration error: ROBLOX_API_KEY not set')
        url = f"https://groups.roblox.com/v1/users/{user_id}/groups/roles"
        headers = {"Cookie": f".ROBLOSECURITY={roblox_api_key}"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for group in data.get("data", []):
            if str(group["group"]["id"]) == str(group_id):
                return {"rank": group["role"]["name"], "roleId": group["role"]["id"]}
        return {"rank": "Not in group", "roleId": 0}
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch group data: {e.response.text if e.response else 'No response'}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get('/get_role_id')
async def get_role_id(request: Request):
    try:
        group_id = request.query_params.get('groupId')
        rank_name = request.query_params.get('rankName')
        if not all([group_id, rank_name]):
            raise HTTPException(status_code=400, detail='Missing groupId or rankName')
        roblox_api_key = os.getenv("ROBLOX_API_KEY")
        if not roblox_api_key:
            raise HTTPException(status_code=500, detail='Server configuration error: ROBLOX_API_KEY not set')
        url = f"https://groups.roblox.com/v1/groups/{group_id}/roles"
        headers = {"Cookie": f".ROBLOSECURITY={roblox_api_key}"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for role in data.get("roles", []):
            if role["name"].lower() == rank_name.lower():
                return {"roleId": role["id"]}
        raise HTTPException(status_code=404, detail='Rank not found')
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch role data: {e.response.text if e.response else 'No response'}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.post('/set_group_rank')
async def set_group_rank(data: dict):
    try:
        user_id = data.get('userId')
        group_id = data.get('groupId')
        role_id = data.get('roleId')
        if not all([user_id, group_id, role_id]):
            raise HTTPException(status_code=400, detail='Missing parameters')
        user_id = int(user_id)
        group_id = int(group_id)
        role_id = int(role_id)
        roblox_api_key = os.getenv("ROBLOX_API_KEY")
        if not roblox_api_key:
            raise HTTPException(status_code=500, detail='Server configuration error: ROBLOX_API_KEY not set')
        url = f"https://groups.roblox.com/v1/groups/{group_id}/users/{user_id}"
        headers = {"Cookie": f".ROBLOSECURITY={roblox_api_key}"}
        payload = {"roleId": role_id}
        resp = requests.patch(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        return {'status': 'success'}
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to set rank: {e.response.text if e.response else 'No response'}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.post("/store_xp", dependencies=[Depends(verify_token)])
async def store_xp(data: dict):
    try:
        discord_id = data.get("discord_id")
        xp_to_store = data.get("xp")
        if not all([discord_id, xp_to_store is not None]):
            raise HTTPException(status_code=400, detail="Missing discord_id or xp")
        if not isinstance(xp_to_store, int) or xp_to_store < 0:
            raise HTTPException(status_code=400, detail="XP must be non-negative")
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO stored_xp (discord_id, stored_xp) VALUES (%s, %s) "
                    "ON CONFLICT (discord_id) DO UPDATE SET stored_xp = stored_xp + %s",
                    (discord_id, xp_to_store, xp_to_store)
                )
        conn.close()
        return {"status": "success", "stored_xp": xp_to_store}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.post("/redeem_xp", dependencies=[Depends(verify_token)])
async def redeem_xp(data: dict):
    try:
        discord_id = data.get("discord_id")
        conn = get_db_connection()
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
        conn.close()
        return {"status": "success", "redeemed_xp": stored_xp, "new_xp": new_xp}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.post("/redeem_xp_ingame")
async def redeem_xp_ingame(data: dict):
    try:
        roblox_user_id = data.get("roblox_user_id")
        if not roblox_user_id:
            raise HTTPException(status_code=400, detail="Missing roblox_user_id")
        conn = get_db_connection()
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
        conn.close()
        return {"status": "success", "redeemed_xp": stored_xp, "new_xp": new_xp}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
