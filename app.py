# app.py
import sqlite3
from flask import Flask, request, jsonify
import datetime
import json

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
    conn.close()

init_db()

@app.route('/update_xp', methods=['POST'])
def update_xp():
    data = request.get_json()
    user_id = data.get('userId')
    username = data.get('username')
    xp = data.get('xp')
    offense_data = data.get('offenseData')  # optional

    if not user_id or not username or xp is None:
        return jsonify({'error': 'Missing required data'}), 400

    last_updated = datetime.datetime.utcnow().isoformat()
    offense_json = json.dumps(offense_data) if offense_data is not None else None

    conn = get_db_connection()
    with conn:
        conn.execute("""
            INSERT INTO xp_data (userId, username, xp, offenseData, last_updated)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(userId) DO UPDATE SET
                username=excluded.username,
                xp=excluded.xp,
                offenseData=excluded.offenseData,
                last_updated=excluded.last_updated
        """, (str(user_id), username, xp, offense_json, last_updated))
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
        cursor = conn.execute("UPDATE xp_data SET xp = ?, last_updated = ? WHERE userId = ?",
                              (new_xp, last_updated, str(user_id)))
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'error': 'User not found in xp_data'}), 404
    conn.close()

    return jsonify({'status': 'success', 'newXp': new_xp})

if __name__ == '__main__':
    app.run(debug=True)
