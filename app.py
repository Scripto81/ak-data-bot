# app.py
from flask import Flask, request, jsonify

app = Flask(__name__)

# In-memory storage (use a real DB in production)
xp_data = {}

@app.route('/update_xp', methods=['POST'])
def update_xp():
    data = request.get_json()
    user_id = data.get('userId')
    username = data.get('username')
    xp = data.get('xp')
    offense_data = data.get('offenseData')  # optional

    # Validate required fields
    if not user_id or not username or xp is None:
        return jsonify({'error': 'Missing required data'}), 400

    # Store everything, including userId
    xp_data[user_id] = {
        'userId': user_id,
        'username': username,
        'xp': xp,
        'offenseData': offense_data
    }
    return jsonify({'status': 'success'})

@app.route('/get_user_data', methods=['GET'])
def get_user_data():
    username_query = request.args.get('username')
    if not username_query:
        return jsonify({'error': 'Username parameter is missing'}), 400

    # Search by username (case-insensitive)
    for entry in xp_data.values():
        if entry['username'].lower() == username_query.lower():
            return jsonify(entry)

    return jsonify({'error': 'User not found'}), 404

if __name__ == '__main__':
    # For local testing; on Render you'll use gunicorn via Procfile
    app.run(debug=True)
