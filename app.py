import sqlite3
from flask import Flask, request, jsonify
import datetime
import json
import requests
import os
import logging
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, filename='flask_api.log', format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('flask_api')

app = Flask(__name__)
DATABASE = os.getenv("DATABASE_PATH", "xp_data.db")

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
                last_updated INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS xp_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                userId TEXT,
                xp_change INTEGER,
                timestamp INTEGER,
                FOREIGN KEY (userId) REFERENCES xp_data(userId)
            )
        """)
    conn.close()

init_db()

@app.route('/', methods=['HEAD', 'GET'])
def health_check():
    return jsonify({"status": "ok"}), 200

@app.route('/update_xp', methods=['POST'])
def update_xp():
    try:
        data = request.get_json()
        user_id = data.get('userId')
        username = data.get('username')
        xp = data.get('xp')
        offense_data = data.get('offenseData')
        timestamp = data.get('timestamp')
        if not all([user_id, username, xp is not None, timestamp is not None]):
            return jsonify({'error': 'Missing required data'}), 400
        if not isinstance(xp, int) or xp < 0:
            return jsonify({'error': 'XP must be a non-negative integer'}), 400
        if not isinstance(timestamp, int):
            return jsonify({'error': 'Timestamp must be an integer'}), 400
        offense_json = json.dumps(offense_data) if offense_data is not None else None
        conn = get_db_connection()
        with conn:
            cur = conn.execute("SELECT xp, last_updated FROM xp_data WHERE userId = ?", (str(user_id),))
            row = cur.fetchone()
            old_xp = row['xp'] if row else 0
            old_timestamp = row['last_updated'] if row else 0
            if timestamp <= old_timestamp:
                return jsonify({'status': 'ignored', 'reason': 'Older timestamp'}), 200
            xp_change = xp - old_xp
            conn.execute("""
                INSERT INTO xp_data (userId, username, xp, offenseData, last_updated)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(userId) DO UPDATE SET
                    username=excluded.username,
                    xp=excluded.xp,
                    offenseData=excluded.offenseData,
                    last_updated=excluded.last_updated
            """, (str(user_id), username, xp, offense_json, timestamp))
            if xp_change != 0:
                conn.execute("INSERT INTO xp_history (userId, xp_change, timestamp) VALUES (?, ?, ?)",
                             (str(user_id), xp_change, timestamp))
        conn.close()
        logger.info(f"Updated XP for user {user_id}: {xp} at {timestamp}")
        return jsonify({'status': 'success', 'xp': xp, 'timestamp': timestamp})
    except Exception as e:
        logger.error(f"Error in update_xp: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/get_user_data', methods=['GET'])
def get_user_data():
    try:
        username = request.args.get('username')
        if not username:
            return jsonify({'error': 'Username parameter is missing'}), 400
        conn = get_db_connection()
        cur = conn.execute("SELECT * FROM xp_data WHERE LOWER(username)=?", (username.lower(),))
        row = cur.fetchone()
        conn.close()
        if row:
            offense_data = json.loads(row['offenseData']) if row['offenseData'] else {}
            return jsonify({'userId': row['userId'], 'username': row['username'], 'xp': row['xp'], 'offenseData': offense_data, 'timestamp': row['last_updated']})
        return jsonify({'error': 'User not found'}), 404
    except Exception as e:
        logger.error(f"Error in get_user_data: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/set_xp', methods=['POST'])
def set_xp():
    try:
        data = request.get_json()
        user_id = data.get('userId')
        new_xp = data.get('xp')
        if not all([user_id, new_xp is not None]):
            return jsonify({'error': 'Missing userId or xp'}), 400
        if not isinstance(new_xp, int) or new_xp < 0:
            return jsonify({'error': 'XP must be a non-negative integer'}), 400
        timestamp = int(datetime.datetime.utcnow().timestamp())
        conn = get_db_connection()
        with conn:
            cur = conn.execute("SELECT xp, username FROM xp_data WHERE userId = ?", (str(user_id),))
            row = cur.fetchone()
            if not row:
                conn.close()
                return jsonify({'error': 'User not found'}), 404
            old_xp = row['xp']
            username = row['username']
            xp_change = new_xp - old_xp
            conn.execute("UPDATE xp_data SET xp = ?, last_updated = ? WHERE userId = ?",
                         (new_xp, timestamp, str(user_id)))
            if xp_change != 0:
                conn.execute("INSERT INTO xp_history (userId, xp_change, timestamp) VALUES (?, ?, ?)",
                             (str(user_id), xp_change, timestamp))
        conn.close()
        logger.info(f"Set XP for user {user_id} to {new_xp} at {timestamp}")
        return jsonify({'status': 'success', 'newXp': new_xp, 'timestamp': timestamp})
    except Exception as e:
        logger.error(f"Error in set_xp: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/leaderboard', methods=['GET'])
def get_leaderboard():
    try:
        limit = min(int(request.args.get('limit', 10)), 50)
        if limit <= 0:
            return jsonify({'error': 'Limit must be positive'}), 400
        conn = get_db_connection()
        cur = conn.execute("SELECT username, xp FROM xp_data ORDER BY xp DESC LIMIT ?", (limit,))
        leaderboard = [dict(row) for row in cur.fetchall()]
        conn.close()
        return jsonify({'leaderboard': leaderboard})
    except ValueError:
        return jsonify({'error': 'Limit must be an integer'}), 400
    except Exception as e:
        logger.error(f"Error in get_leaderboard: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/get_group_rank', methods=['GET'])
def get_group_rank():
    try:
        user_id = request.args.get('userId')
        group_id = request.args.get('groupId')
        if not all([user_id, group_id]):
            return jsonify({'error': 'Missing userId or groupId'}), 400
        roblox_api_key = os.getenv("ROBLOX_API_KEY")
        if not roblox_api_key:
            logger.error("ROBLOX_API_KEY not set in environment variables")
            return jsonify({'error': 'Server configuration error', 'details': 'ROBLOX_API_KEY not set'}), 500
        url = f"https://groups.roblox.com/v1/users/{user_id}/groups/roles"
        headers = {"Cookie": f".ROBLOSECURITY={roblox_api_key}"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for group in data.get("data", []):
            if str(group["group"]["id"]) == str(group_id):
                return jsonify({"rank": group["role"]["name"], "roleId": group["role"]["id"]})
        return jsonify({"rank": "Not in group", "roleId": 0})
    except requests.RequestException as e:
        logger.error(f"Error fetching group rank: {str(e)}")
        return jsonify({'error': 'Failed to fetch group data', 'details': e.response.text if e.response else 'No response'}), 500
    except Exception as e:
        logger.error(f"Error in get_group_rank: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/get_role_id', methods=['GET'])
def get_role_id():
    try:
        group_id = request.args.get('groupId')
        rank_name = request.args.get('rankName')
        if not all([group_id, rank_name]):
            return jsonify({'error': 'Missing groupId or rankName'}), 400
        roblox_api_key = os.getenv("ROBLOX_API_KEY")
        if not roblox_api_key:
            logger.error("ROBLOX_API_KEY not set in environment variables")
            return jsonify({'error': 'Server configuration error', 'details': 'ROBLOX_API_KEY not set'}), 500
        url = f"https://groups.roblox.com/v1/groups/{group_id}/roles"
        headers = {"Cookie": f".ROBLOSECURITY={roblox_api_key}"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for role in data.get("roles", []):
            if role["name"].lower() == rank_name.lower():
                return jsonify({"roleId": role["id"]})
        return jsonify({'error': 'Rank not found'}), 404
    except requests.RequestException as e:
        logger.error(f"Error fetching role ID: {str(e)}")
        return jsonify({'error': 'Failed to fetch role data', 'details': e.response.text if e.response else 'No response'}), 500
    except Exception as e:
        logger.error(f"Error in get_role_id: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/set_group_rank', methods=['POST'])
def set_group_rank():
    try:
        data = request.get_json()
        user_id = data.get('userId')
        group_id = data.get('groupId')
        role_id = data.get('roleId')
        if not all([user_id, group_id, role_id]):
            logger.error(f"Missing parameters: userId={user_id}, groupId={group_id}, roleId={role_id}")
            return jsonify({'error': 'Missing parameters'}), 400
        user_id = int(user_id)
        group_id = int(group_id)
        role_id = int(role_id)
        roblox_api_key = os.getenv("ROBLOX_API_KEY")
        if not roblox_api_key:
            logger.error("ROBLOX_API_KEY not set in environment variables")
            return jsonify({'error': 'Server configuration error', 'details': 'ROBLOX_API_KEY not set'}), 500
        url = f"https://groups.roblox.com/v1/groups/{group_id}/users/{user_id}"
        headers = {"Cookie": f".ROBLOSECURITY={roblox_api_key}"}
        payload = {"roleId": role_id}
        resp = requests.patch(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"Successfully set rank for user {user_id} in group {group_id} to role {role_id}")
        return jsonify({'status': 'success'})
    except requests.RequestException as e:
        logger.error(f"Roblox API error: {str(e)}")
        return jsonify({'error': 'Failed to set rank', 'details': e.response.text if e.response else 'No response'}), 500
    except Exception as e:
        logger.error(f"Unexpected error in set_group_rank: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "False") == "True")
