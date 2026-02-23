#!/bin/bash
# === Casa di Vino - Nginx Reverse Proxy Deploy Script ===
# Futtasd ezt a scriptet a szerveren (SRKHOST Discord bot-on keresztül):
#   chmod +x deploy_nginx.sh && sudo bash deploy_nginx.sh
#
# FONTOS: Cseréld ki a YOURDOMAIN.HU-t a saját domainedre!

DOMAIN="YOURDOMAIN.HU"

# 1. Nginx telepítés
apt update && apt install -y nginx

# 2. Nginx konfig létrehozása
cat > /etc/nginx/sites-available/casadivino <<EOF
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 60s;
        proxy_read_timeout 60s;
    }
}
EOF

# 3. Engedélyezés és default eltávolítása
ln -sf /etc/nginx/sites-available/casadivino /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# 4. Nginx újraindítás
nginx -t && systemctl restart nginx

# 5. SSL tanúsítvány (Let's Encrypt)
apt install -y certbot python3-certbot-nginx
certbot --nginx -d $DOMAIN -d www.$DOMAIN --non-interactive --agree-tos --register-unsafely-without-email

echo "=== Kész! A $DOMAIN most már elérhető HTTPS-en ==="
