# Production deployment

Bind the application to localhost and terminate TLS on a reverse proxy.

## Environment

```
APP_ENV=production
APP_HOST=127.0.0.1
APP_PORT=8000
SESSION_SECRET=<output of python -c "import secrets; print(secrets.token_urlsafe(48))">
ALLOWED_HOSTS=research.example.com
TRUSTED_PROXY_IPS=127.0.0.1
HTTPS_REDIRECT=false
```

Never set `TRUSTED_PROXY_IPS=*` in production.

## Nginx example

```nginx
server {
    listen 443 ssl http2;
    server_name research.example.com;

    ssl_certificate     /etc/ssl/certs/research.example.com.pem;
    ssl_certificate_key /etc/ssl/private/research.example.com.key;

    client_max_body_size 32m;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}
```

## Updates

```bash
python main.py backup
alembic upgrade head
# then restart PM2 / systemd
```

## First-run

1. Start the app and copy the bootstrap token from the server log.
2. Open `/setup` and create the administrator.
3. Or run `python main.py create-admin`.
