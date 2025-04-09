import os
from flask import Flask
import requests

app = Flask(__name__)

# Get Discord token from Render environment variables
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = "1337553147861926003"

def send_farewell_message():
    if not DISCORD_TOKEN:
        print("Error: DISCORD_TOKEN not found in Render environment variables")
        return
    
    url = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"
    headers = {
        "Authorization": f"Bot {DISCORD_TOKEN}",
        "Content-Type": "application/json"
    }
    
    embed = {
        "title": "Farewell Announcement",
        "description": "Since my creator **ItsJustMeFriendly1** is sadly leaving this amazing community, I will now be erased from the API. It was nice serving everyone, but it's my time to go. Alaskans Kingdom gave me life, and now I shall leave. Thank_you all for supporting my development. Goodbye!",
        "color": 0xFF0000,  # Red color
        "fields": [
            {
                "name": "Creator",
                "value": "ItsJustMeFriendly1",
                "inline": True
            },
            {
                "name": "Community",
                "value": "Alaskans Kingdom",
                "inline": True
            }
        ],
        "footer": {
            "text": "Bot Termination Notice | April 09, 2025"
        }
    }
    
    payload = {
        "embeds": [embed]
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        print("Farewell message sent successfully!")
    except requests.RequestException as e:
        print(f"Failed to send farewell message: {str(e)}")

@app.route('/', methods=['GET'])
def announce_and_shutdown():
    send_farewell_message()
    return {"status": "Bot terminated, farewell message sent"}, 200

if __name__ == "__main__":
    # Render uses PORT environment variable by default
    port = int(os.getenv("PORT", 10000))  # Render default is typically 10000 if not specified
    app.run(host="0.0.0.0", port=port)
