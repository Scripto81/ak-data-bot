# app.py
from flask import Flask, request, jsonify

app = Flask(__name__)

# In-memory storage (use a real database in production)
xp_data = {}

@app.route('/update_xp', methods=['POST'])
def update_xp():
    data = request.get_json()
    user_id = data.get('userId')
    username = data.get('username')
    xp = data.get('xp')
    offense_data = data.get('offenseData')  # optional

    if not user_id or not username or xp is None:
        return jsonify({'error': 'Missing required data'}), 400

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

    # Search xp_data for a matching username (case-insensitive)
    for entry in xp_data.values():
        if entry['username'].lower() == username_query.lower():
            return jsonify(entry)

    return jsonify({'error': 'User not found'}), 404

@app.route('/set_xp', methods=['POST'])
def set_xp():
    """
    The Discord bot calls this to change a user's XP.
    JSON payload: { "userId": <number>, "xp": <number> }
    """
    data = request.get_json()
    user_id = data.get('userId')
    new_xp = data.get('xp')

    if not user_id or new_xp is None:
        return jsonify({'error': 'Missing userId or xp'}), 400

    if user_id not in xp_data:
        return jsonify({'error': 'User not found in xp_data'}), 404

    xp_data[user_id]['xp'] = new_xp
    return jsonify({'status': 'success', 'newXp': new_xp})

if __name__ == '__main__':
    # For local testing. On Render or other hosts, you'd use gunicorn + a Procfile.
    app.run(debug=True)
