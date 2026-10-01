# استقرار روی Render

دو چیز مستقر می‌شود: **پنل** روی Render و یک یا چند **ایجنت نود** روی VPS.
اگر می‌خواهید پنل سبز بالا بیاید، اول نود را راه بیندازید.

> اگر ترجیح می‌دهید همه‌چیز روی یک پلتفرم باشد، [`DEPLOY_RAILWAY.md`](DEPLOY_RAILWAY.md)
> را ببینید: Railway با TCP Proxy می‌تواند نود را هم میزبانی کند (فقط TCP).

---

## بخش الف — ایجنت نود (VPS)

به میزبانی با IP عمومی و یک پورت TCP باز نیاز دارید. فرض بر Ubuntu 22.04/24.04 است.

### الف-۱. TLS برای API ایجنت

API ایجنت نباید روی HTTP خام برود (توکن Bearer لو می‌رود).

```bash
sudo apt update && sudo apt install -y nginx certbot python3-certbot-nginx
sudo certbot certonly --standalone -d node1.example.com --agree-tos -m you@example.com --non-interactive
```

`/etc/nginx/sites-available/node-agent`:

```nginx
server {
    listen 443 ssl http2;
    server_name node1.example.com;

    ssl_certificate     /etc/letsencrypt/live/node1.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/node1.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
        client_max_body_size 4m;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/node-agent /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo ufw allow 443/tcp
```

> اگر CDN جلوی اینباند WebSocket/gRPC دارید، **API ایجنت را روی دامنه‌ی جدا**
> نگه دارید تا قواعد کش CDN هرگز ترافیک کنترلی را دست نزنند.

### الف-۲. اجرای ایجنت

```bash
git clone <your-repo> && cd xray-panel

export NODE_TOKEN="$(openssl rand -hex 32)"     # این را برای پنل نگه دارید
export NODE_NAME="eu-1"
export PUBLIC_HOST="node1.example.com"

docker build -t xray-node ./node-agent
docker run -d --name xray-node \
  --restart unless-stopped \
  --network host \
  -e NODE_TOKEN="$NODE_TOKEN" \
  -e NODE_NAME="$NODE_NAME" \
  -e PUBLIC_HOST="$PUBLIC_HOST" \
  -e AGENT_PORT=8081 \
  -v /etc/xray:/etc/xray \
  -v /var/log/xray:/var/log/xray \
  xray-node
```

`--network host` باعث می‌شود Xray هر پورتی را بدون `-p` اضافه bind کند.

بررسی:

```bash
curl -s http://127.0.0.1:8081/health | jq
# {"ok":true,"node":"eu-1","xray_running":true,"xray_version":"Xray 1.8.24 ..."}
```

### الف-۳. کلیدهای Reality (فقط برای VLESS+Reality)

```bash
docker exec xray-node /usr/local/bin/xray x25519
# Private key: ...   Public key: ...
```

کلید خصوصی را در اینباند (`reality_private_key`) و کلید عمومی را در
`reality_public_key` بگذارید — پنل نیمه‌ی عمومی را در لینک کلاینت می‌نشاند.

---

## بخش ب — پنل روی Render

### ب-۱. Blueprint (توصیه‌شده)

1. مخزن را در GitHub/GitLab پوش کنید.
2. داشبورد Render → **New → Blueprint** → مخزن را انتخاب کنید.
3. Render فایل `render.yaml` را می‌خواند و این‌ها را می‌سازد:
   * `xpanel-db` — PostgreSQL 16 مدیریت‌شده
   * `xray-panel` — سرویس وب Docker (healthcheck روی `/api/v1/health/live`، دیسک ۱ گیگ روی `/app/data`)
   * `xray-panel-backup` — Cron روزانه ساعت ۰۳:۰۰ UTC
   * `xray-panel-sweep` — Cron هر ۱۵ دقیقه

4. Render برای متغیرهای `sync: false` مقدار می‌خواهد:

| متغیر | مقدار |
|---|---|
| `SUPERADMIN_PASSWORD` | یک رمز قوی (بعد از اولین ورود عوضش کنید) |
| `TELEGRAM_BOT_TOKEN` | از BotFather |
| `TELEGRAM_BOT_USERNAME` | نام کاربری ربات بدون `@` |
| `TELEGRAM_ADMIN_IDS` | آیدی عددی ادمین‌ها با کاما |

`SECRET_KEY`، `ENCRYPTION_KEY` و `TELEGRAM_WEBHOOK_SECRET` را Render خودش
می‌سازد. `PANEL_BASE_URL` و `CORS_ORIGINS` به `RENDER_EXTERNAL_URL` وصل می‌شوند.

5. منتظر بمانید. کانتینر `python -m app.cli init-db --seed` را اجرا می‌کند و
   بعد uvicorn بالا می‌آید.

### ب-۲. ساخت دستی سرویس

* **New → Web Service**، Runtime = **Docker**
* Root directory = `panel`، Dockerfile = `panel/Dockerfile`
* Health check path = `/api/v1/health/live`
* یک **Disk** روی `/app/data` (۱ گیگ) اضافه کنید — SQLite و پوشه‌ی پشتیبان را
  پایدار نگه می‌دارد
* یک **PostgreSQL** بسازید و `DATABASE_URL` را از اتصال **Internal** ست کنید
* بقیه‌ی متغیرها را از `.env.example` کپی کنید

### ب-۳. چک‌لیست اولین اجرا

```bash
PANEL=https://xray-panel.onrender.com

# ۱. سلامت
curl -s $PANEL/api/v1/health/live

# ۲. ورود و گرفتن توکن
TOKEN=$(curl -sX POST $PANEL/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<SUPERADMIN_PASSWORD>"}' | jq -r .access_token)

# ۳. ثبت نود (همان NODE_TOKEN بخش الف)
curl -sX POST $PANEL/api/v1/nodes -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "name":"eu-1","address":"https://node1.example.com",
    "public_host":"node1.example.com","region":"EU",
    "tags":["eu","default"],"api_token":"<NODE_TOKEN>","max_services":500
  }'

# ۴. ساخت اینباند
curl -sX POST $PANEL/api/v1/inbounds -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "node_id":1,"tag":"vless-reality","remark":"VLESS Reality",
    "protocol":"vless","port":443,"transport":"tcp","security":"reality",
    "sni":"www.cloudflare.com","flow":"xtls-rprx-vision",
    "reality_dest":"www.cloudflare.com:443",
    "reality_public_key":"<PUBLIC>","reality_private_key":"<PRIVATE>",
    "reality_short_ids":["6ba85179e30d4fc2"],"is_default":true
  }'

# ۵. ارسال وضعیت مطلوب به نود
curl -sX POST $PANEL/api/v1/nodes/1/sync -H "Authorization: Bearer $TOKEN"

# ۶. وبهوک تلگرام (معمولاً خودکار)
curl -sX POST $PANEL/api/v1/telegram/setup -H "Authorization: Bearer $TOKEN"
```

بعد در رابط کاربری: **Plans → New plan**، **Configs → + New config**، QR را باز
کنید و با v2rayNG / Clash / Streisand اسکن کنید.

### ب-۴. سخت‌سازی اختیاری

* **Custom Domain** بگیرید و `PANEL_BASE_URL` را روی آن بگذارید تا لینک‌های
  اشتراک تمیز باشند.
* `TRUSTED_HOSTS=your-domain` و `CORS_ORIGINS=https://your-domain` را محدود کنید.
* روی پلن پولی `WEB_CONCURRENCY=2` بگذارید؛ قفل advisory خودش رهبر را انتخاب می‌کند.
* **PITR** پستگرس Render (پلن‌های پولی) را فعال کنید؛ پشتیبان‌گیری داخلی خط دوم
  دفاع است.
* `ALERT_WEBHOOK_URL` را به Slack/Discord وصل کنید.

---

## بخش ج — توسعه‌ی محلی

```bash
cp .env.example .env
docker compose up -d --build  # پنل :8000، ایجنت :8081، پستگرس :5432
# مرورگر: http://localhost:8000  (admin / admin)
```

یا فقط پنل با SQLite:

```bash
python -m venv .venv && . .venv/bin/activate     # ویندوز: .venv\Scripts\activate
pip install -r panel/requirements-dev.txt
cd panel && python -m app.cli init-db --seed --seed-demo
uvicorn app.main:app --reload --port 8000
```

---

## عیب‌یابی

| نشانه | علت / راه‌حل |
|---|---|
| دیپلوی روی `init-db` می‌ماند | `DATABASE_URL` در دسترس نیست — آدرس **Internal** را بدهید، نه External |
| Healthcheck رد می‌شود | `/api/v1/health/live` باید تا ۲۵ ثانیه ۲۰۰ بدهد؛ دیتابیس کند کل استارت را قفل می‌کند |
| نود `offline` است | ایجنت در دسترس نیست یا توکن فرق دارد؛ `curl https://node1.example.com/health` |
| نود `degraded` است | یک تا دو شکست متوالی؛ `last_error` کارت نود را ببینید |
| خطای `502` هنگام ساخت کانفیگ | ایجنت درخواست را رد کرده — `last_error` را بخوانید و بعد `/nodes/{id}/sync` بزنید |
| کلاینت وصل می‌شود ولی ترافیک ندارد | پورت در فایروال VPS یا security group ابری بسته است |
| وبهوک `403` می‌دهد | `TELEGRAM_WEBHOOK_SECRET` بعد از ثبت وبهوک عوض شده؛ `/telegram/setup` را دوباره صدا بزنید |
| لینک اشتراک `403` می‌دهد | سرویس منقضی یا حجم‌تمام است — تمدید کنید |
| کارهای زمان‌بندی اجرا نمی‌شوند | `ENABLE_SCHEDULER=false` است و سرویس‌های Cron دیپلوی نشده‌اند |
