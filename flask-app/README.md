# Flask Discord OAuth App

## Setup

### 1. Create a Discord Application
1. Go to https://discord.com/developers/applications
2. Click **New Application** → name it → **Save**
3. Go to **OAuth2** → copy **Client ID** and **Client Secret**
4. Under **Redirects**, add: `http://localhost:5000/callback`

### 2. Install Dependencies
```bash
cd flask-app
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Set Environment Variables
```bash
export DISCORD_CLIENT_ID="your-client-id"
export DISCORD_CLIENT_SECRET="your-client-secret"
export SECRET_KEY="a-strong-random-secret-key"
# Windows: use `set` instead of `export`
```

### 4. Run
```bash
python app.py
```
Open http://localhost:5000

### 5. Configure Admins
Edit `ADMIN_DISCORD_IDS` in `app.py` with your Discord user ID(s).

## Project Structure
```
flask-app/
├── app.py                 # Main application
├── requirements.txt
├── templates/
│   ├── base.html          # Shared navbar layout
│   ├── visitor.html       # Home / prices page
│   ├── profile.html       # User profile page
│   ├── admin.html         # Admin panel
│   ├── fraction.html      # Borászat page
│   └── 403.html           # Forbidden error page
└── static/
    └── uploads/           # Custom avatar uploads
```
