import os
import sqlite3
from flask import Flask, request, jsonify
import datetime
import json
import requests
import logging
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, filename='flask_api.log', format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('flask_api')

app = Flask(__name__)
DATABASE = os.getenv("DATABASE_PATH", "xp_data.db")

def get_db_connection():
    try:
        conn = sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.error(f"Database connection error: {str(e)}")
        raise

def init_db():
    try:
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
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {str(e)}")
        raise

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
        if not all([user_id, username, xp is not None]):
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
        logger.info(f"Updated XP for user {user_id}: {xp}")
        return jsonify({'status': 'success', 'xp': xp, 'last_updated': last_updated})
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
            return jsonify(dict(row) | {'offenseData': offense_data})
        return jsonify({'error': 'User not found'}), 404
    except Exception as e:
        logger.error(f"Error in get_user_data: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/get_multiple_user_data', methods=['POST'])
def get_multiple_user_data():
    try:
        data = request.get_json()
        usernames = data.get('usernames', [])
        if not usernames or not isinstance(usernames, list):
            return jsonify({'error': 'Usernames must be a non-empty list'}), 400
        conn = get_db_connection()
        placeholders = ','.join(['?' for _ in usernames])
        cur = conn.execute(f"SELECT * FROM xp_data WHERE LOWER(username) IN ({placeholders})", [u.lower() for u in usernames])
        rows = cur.fetchall()
        conn.close()
        result = {}
        for row in rows:
            offense_data = json.loads(row['offenseData']) if row['offenseData'] else {}
            result[row['username']] = dict(row) | {'offenseData': offense_data}
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in get_multiple_user_data: {str(e)}")
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
        last_updated = datetime.datetime.utcnow().isoformat()
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
                         (new_xp, last_updated, str(user_id)))
            if xp_change != 0:
                conn.execute("INSERT INTO xp_history (userId, xp_change, timestamp) VALUES (?, ?, ?)",
                             (str(user_id), xp_change, last_updated))
        conn.close()
        logger.info(f"Set XP for user {user_id} to {new_xp}")
        return jsonify({'status': 'success', 'newXp': new_xp, 'last_updated': last_updated, 'username': username})
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
        logger.error(f"Error fetching group rank: {str(e)}, response={e.response.text if e.response else 'No response'}")
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
        logger.error(f"Error fetching role ID: {str(e)}, response={e.response.text if e.response else 'No response'}")
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
            return jsonify({'error': 'Missing parameters', 'details': 'userId, groupId, or roleId missing'}), 400
        try:
            user_id = int(user_id)
            group_id = int(group_id)
            role_id = int(role_id)
        except ValueError:
            logger.error(f"Invalid integer parameters: userId={user_id}, groupId={group_id}, roleId={role_id}")
            return jsonify({'error': 'Invalid parameters', 'details': 'userId, groupId, and roleId must be integers'}), 400
        roblox_api_key = os.getenv("ROBLOX_API_KEY")
        if not roblox_api_key:
            logger.error("ROBLOX_API_KEY not set in environment variables")
            return jsonify({'error': 'Server configuration error', 'details': 'ROBLOX_API_KEY not set'}), 500
        url = f"https://groups.roblox.com/v1/groups/{group_id}/users/{user_id}"
        headers = {"Cookie": f".ROBLOSECURITY={roblox_api_key}"}
        payload = {"roleId": role_id}
        logger.info(f"Sending request to Roblox API: {url}, payload={payload}")
        resp = requests.patch(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"Successfully set rank for user {user_id} in group {group_id} to role {role_id}")
        return jsonify({'status': 'success'})
    except requests.RequestException as e:
        error_msg = str(e)
        response_text = e.response.text if e.response else "No response from Roblox API"
        logger.error(f"Roblox API error: {error_msg}, response={response_text}")
        status_code = e.response.status_code if e.response else 500
        if status_code == 403:
            error_type = 'Permission denied'
        elif status_code == 400:
            error_type = 'Invalid request'
        elif status_code == 404:
            error_type = 'Resource not found'
        else:
            error_type = 'Failed to set rank'
        return jsonify({'error': error_type, 'details': response_text}), status_code
    except Exception as e:
        logger.error(f"Unexpected error in set_group_rank: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "False") == "True")
