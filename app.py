import sqlite3
from flask import Flask, request, jsonify
import datetime
import json
import requests
import os

app = Flask(__name__)
DATABASE = "xp_data.db"

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
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
def update_xp():
    data = request.get_json()
    user_id = data.get('userId')
    username = data.get('username')
    xp = data.get('xp')
    offense_data = data.get('offenseData')
    if not user_id or not username or xp is None:
        return jsonify({'error': 'Missing required data'}), 400
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
def set_xp():
    data = request.get_json()
    user_id = data.get('userId')
    new_xp = data.get('xp')
    if not user_id or new_xp is None:
        return jsonify({'error': 'Missing userId or xp'}), 400
    last_updated = datetime.datetime.utcnow().isoformat()
    conn = get_db_connection()
    with conn:
        cur = conn.execute("SELECT xp FROM xp_data WHERE userId = ?", (str(user_id),))
        row = cur.fetchone()
        old_xp = row['xp'] if row else 0
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
def get_leaderboard():
    limit = int(request.args.get('limit', 10))
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT username, xp FROM xp_data ORDER BY xp DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    leaderboard = [{'username': row['username'], 'xp': row['xp']} for row in rows]
    return jsonify({'leaderboard': leaderboard})

@app.route('/user_stats', methods=['GET'])
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
def get_group_rank():
    user_id = request.args.get('userId')
    group_id = request.args.get('groupId')
    if not user_id or not group_id:
        return jsonify({'error': 'Missing userId or groupId'}), 400
    roblox_api_key = os.environ.get('ROBLOX_API_KEY')
    if not roblox_api_key:
        return jsonify({'error': 'ROBLOX_API_KEY not set'}), 500
    url = f"https://groups.roblox.com/v1/users/{user_id}/groups/roles"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {roblox_api_key}"}
    try:
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        for group_info in data.get("data", []):
            if group_info.get("group", {}).get("id") == int(group_id):
                return jsonify({'rank': group_info.get("role", {}).get("name", "Not in group"), 'roleId': group_info.get("role", {}).get("id", 0)})
        return jsonify({'rank': "Not in group", 'roleId': 0})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/set_group_rank', methods=['POST'])
def set_group_rank():
    data = request.get_json()
    user_id = data.get('userId')
    group_id = data.get('groupId')
    role_id = data.get('roleId')
    if not user_id or not group_id or not role_id:
        return jsonify({'error': 'Missing userId, groupId, or roleId'}), 400
    roblox_api_key = os.environ.get('ROBLOX_API_KEY')
    if not roblox_api_key:
        return jsonify({'error': 'ROBLOX_API_KEY not set'}), 500
    url = f"https://groups.roblox.com/v1/groups/{group_id}/roles/{role_id}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {roblox_api_key}"}
    payload = {"targetId": int(user_id)}
    try:
        resp = requests.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
