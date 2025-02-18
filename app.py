from flask import Flask, request, jsonify

app = Flask(__name__)

# In-memory storage for demonstration (replace with a persistent database for production)
xp_data = {}

@app.route('/update_xp', methods=['POST'])
def update_xp():
    data = request.get_json()
    user_id = data.get('userId')
    username = data.get('username')
    xp = data.get('xp')
    
    if not user_id or not username or xp is None:
        return jsonify({'error': 'Missing data'}), 400

    # Store the data (for production, save this in a real database)
    xp_data[user_id] = {'username': username, 'xp': xp}
    return jsonify({'status': 'success'})

@app.route('/get_user_data', methods=['GET'])
def get_user_data():
    username_query = request.args.get('username')
    if not username_query:
        return jsonify({'error': 'Username parameter is missing'}), 400
    
    # Search for the user by username
    for entry in xp_data.values():
        if entry['username'].lower() == username_query.lower():
            return jsonify(entry)
    return jsonify({'error': 'User not found'}), 404

if __name__ == '__main__':
    app.run(debug=True)
