import sqlite3
from flask import Flask, request, jsonify
import datetime
import json
import requests
import os
import logging
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('flask_api')

app = Flask(__name__)
DATABASE = "xp_data.db"

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

def get_db_connection():
    conn = sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS xp_data (
                userId TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                xp INTEGER NOT NULL,
                offenseData TEXT,
                last_updated TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS xp_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                userId TEXT,
                xp_change INTEGER,
                timestamp TEXT,
                FOREIGN KEY (userId) REFERENCES xp_data(userId)
            )
        """)
    conn.close()

init_db()

@app.route('/update_xp', methods=['POST'])
@limiter.limit("10 per minute")
def update_xp():
    data = request.get_json()
    user_id = data.get('userId')
    username = data.get('username')
    xp = data.get('xp')
    offense_data = data.get('offenseData')
    if not user_id or not username or xp is None:
        return jsonify({'error': 'Missing required data'}), 400
    if not isinstance(xp, int) or xp < 0:
        return jsonify({'error': 'XP must be a non-negative integer'}), 400
    last_updated = datetime.datetime.utcnow().isoformat()
    offense_json = json.dumps(offense_data) if offense_data is not None else None
    conn = get_db_connection()
    with conn:
        cur = conn.execute("SELECT xp FROM xp_data WHERE userId = ?", (str(user_id),))
        row = cur.fetchone()
        old_xp = row['xp'] if row else 0
        xp_change = xp - old_xp
        conn.execute("""
            INSERT INTO xp_data (userId, username, xp, offenseData, last_updated)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(userId) DO UPDATE SET
                username=excluded.username,
                xp=excluded.xp,
                offenseData=excluded.offenseData,
                last_updated=excluded.last_updated
        """, (str(user_id), username, xp, offense_json, last_updated))
        if xp_change != 0:
            conn.execute("INSERT INTO xp_history (userId, xp_change, timestamp) VALUES (?, ?, ?)",
                        (str(user_id), xp_change, last_updated))
    conn.close()
    return jsonify({'status': 'success'})

@app.route('/get_user_data', methods=['GET'])
@limiter.limit("20 per minute")
def get_user_data():
    username_query = request.args.get('username')
    if not username_query:
        return jsonify({'error': 'Username parameter is missing'}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM xp_data WHERE LOWER(username)=?", (username_query.lower(),))
    row = cur.fetchone()
    conn.close()
    if row:
        offense_data = json.loads(row['offenseData']) if row['offenseData'] else {}
        result = {
            'userId': row['userId'],
            'username': row['username'],
            'xp': row['xp'],
            'offenseData': offense_data,
            'last_updated': row['last_updated']
        }
        return jsonify(result)
    else:
        return jsonify({'error': 'User not found'}), 404

@app.route('/set_xp', methods=['POST'])
@limiter.limit("10 per minute")
def set_xp():
    data = request.get_json()
    user_id = data.get('userId')
    new_xp = data.get('xp')
    if not user_id or new_xp is None:
        return jsonify({'error': 'Missing userId or xp'}), 400
    if not isinstance(new_xp, int) or new_xp < 0:
        return jsonify({'error': 'XP must be a non-negative integer'}), 400
    last_updated = datetime.datetime.utcnow().isoformat()
    conn = get_db_connection()
    with conn:
        cur = conn.execute("SELECT xp FROM xp_data WHERE userId = ?", (str(user_id),))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'User not found in xp_data'}), 404
        old_xp = row['xp']
        xp_change = new_xp - old_xp
        cursor = conn.execute("UPDATE xp_data SET xp = ?, last_updated = ? WHERE userId = ?",
                              (new_xp, last_updated, str(user_id)))
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'error': 'User not found in xp_data'}), 404
        if xp_change != 0:
            conn.execute("INSERT INTO xp_history (userId, xp_change, timestamp) VALUES (?, ?, ?)",
                         (str(user_id), xp_change, last_updated))
    conn.close()
    return jsonify({'status': 'success', 'newXp': new_xp})

@app.route('/leaderboard', methods=['GET'])
@limiter.limit("20 per minute")
def get_leaderboard():
    limit = request.args.get('limit', 10)
    try:
        limit = int(limit)
        if limit <= 0 or limit > 50:
            return jsonify({'error': 'Limit must be between 1 and 50'}), 400
    except ValueError:
        return jsonify({'error': 'Limit must be an integer'}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT username, xp FROM xp_data ORDER BY xp DESC LIMIT {limit}")
    rows = cur.fetchall()
    conn.close()
    leaderboard = [{'username': row['username'], 'xp': row['xp']} for row in rows]
    return jsonify({'leaderboard': leaderboard})

@app.route('/user_stats', methods=['GET'])
@limiter.limit("20 per minute")
def get_user_stats():
    user_id = request.args.get('userId')
    if not user_id:
        return jsonify({'error': 'userId missing'}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT xp_change, timestamp FROM xp_history WHERE userId = ? ORDER BY timestamp DESC", (user_id,))
    history = [{'xp_change': row['xp_change'], 'timestamp': row['timestamp']} for row in cur.fetchall()]
    conn.close()
    return jsonify({'userId': user_id, 'xp_history': history})

@app.route('/get_group_rank', methods=['GET'])
@limiter.limit("20 per minute")
def get_group_rank_endpoint():
    user_id = request.args.get('userId')
    group_id = request.args.get('groupId')
    if not user_id or not group_id:
        return jsonify({'error': 'Missing userId or groupId'}), 400
    try:
        int(user_id)
        int(group_id)
    except ValueError:
        return jsonify({'error': 'userId and groupId must be integers'}), 400
    roblox_api_key = os.environ.get('ROBLOX_API_KEY')
    if not roblox_api_key:
        return jsonify({'error': 'ROBLOX_API_KEY not set'}), 500
    url = f"https://groups.roblox.com/v1/users/{user_id}/groups/roles"
    headers = {"Content-Type": "application/json", "Cookie": f"ROBLOSECURITY={roblox_api_key}"}
    try:
        logger.info(f"Fetching group rank for userId={user_id}, groupId={group_id}")
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"Roblox API response: {data}")
        for group_info in data.get("data", []):
            if group_info.get("group", {}).get("id") == int(group_id):
                return jsonify({'rank': group_info.get("role", {}).get("name", "Not in group"),
                                'roleId': group_info.get("role", {}).get("id", 0)})
        return jsonify({'rank': "Not in group", 'roleId': 0})
    except Exception as e:
        logger.error(f"Error fetching group rank: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/get_role_id', methods=['GET'])
@limiter.limit("20 per minute")
def get_role_id():
    group_id = request.args.get('groupId')
    rank_name = request.args.get('rankName')
    if not group_id or not rank_name:
        return jsonify({'error': 'Missing groupId or rankName'}), 400
    try:
        int(group_id)
    except ValueError:
        return jsonify({'error': 'groupId must be an integer'}), 400
    roblox_api_key = os.environ.get('ROBLOX_API_KEY')
    if not roblox_api_key:
        return jsonify({'error': 'ROBLOX_API_KEY not set'}), 500
    url = f"https://groups.roblox.com/v1/groups/{group_id}/roles"
    headers = {"Content-Type": "application/json", "Cookie": f"ROBLOSECURITY={roblox_api_key}"}
    try:
        logger.info(f"Fetching roles for groupId={group_id}")
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for role in data.get("roles", []):
            if role.get("name").lower() == rank_name.lower():
                return jsonify({'roleId': role.get("id")})
        return jsonify({'error': 'Rank name not found in group'}), 404
    except Exception as e:
        logger.error(f"Error fetching role ID: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/set_group_rank', methods=['POST'])
@limiter.limit("10 per minute")
def set_group_rank():
    data = request.get_json()
    user_id = data.get('userId')
    group_id = data.get('groupId')
    role_id = data.get('roleId')
    if not user_id or not group_id or not role_id:
        return jsonify({'error': 'Missing userId, groupId, or roleId'}), 400
    try:
        int(user_id)
        int(group_id)
        int(role_id)
    except ValueError:
        return jsonify({'error': 'userId, groupId, and roleId must be integers'}), 400
    roblox_api_key = os.environ.get('ROBLOX_API_KEY')
    if not roblox_api_key:
        return jsonify({'error': 'ROBLOX_API_KEY not set'}), 500
    url = f"https://groups.roblox.com/v1/groups/{group_id}/users/{user_id}"
    headers = {"Content-Type": "application/json", "Cookie": f"ROBLOSECURITY={roblox_api_key}"}
    payload = {"roleId": int(role_id)}
    try:
        logger.info(f"Setting group rank: userId={user_id}, groupId={group_id}, roleId={role_id}")
        resp = requests.patch(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"Roblox API response status: {resp.status_code}")
        logger.info(f"Roblox API response text: {resp.text}")
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error setting group rank: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
