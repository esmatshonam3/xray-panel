# استقرار روی Railway

این سند گام‌به‌گام استقرار کامل روی [Railway](https://railway.com) را توضیح می‌دهد.
معماری دو بخشی است و باید **دو سرویس جداگانه** بسازید:

| سرویس | چه چیزی | Root Directory |
|---|---|---|
| **panel** | API، رابط کاربری، ربات تلگرام، زمان‌بند، دیتابیس | `panel` |
| **node-agent** | پروسه‌ی `xray-core` و ایجنت مدیریت آن | `node-agent` |

پنل روی Railway می‌ماند و نودها روی Railway (با TCP Proxy) یا هر VPS دیگری اجرا می‌شوند.

---

## بخش ۱ — آماده‌سازی مخزن

پروژه را در GitHub یا GitLab پوش کنید. دو فایل `railway.toml` از قبل در
`panel/` و `node-agent/` قرار دارند و Railway آن‌ها را به‌صورت خودکار می‌خواند.

تولید مقادیر امن (روی سیستم خودتان اجرا کنید):

```bash
python -c "import secrets; print('SECRET_KEY=', secrets.token_urlsafe(64))"
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=', Fernet.generate_key().decode())"
python -c "import secrets; print('WEBHOOK_SECRET=', secrets.token_hex(32))"
```

---

## بخش ۲ — دیتابیس

1. در پروژه‌ی Railway روی **New → Database → Add PostgreSQL** بزنید.
2. صبر کنید تا سرویس `Postgres` سبز شود.
3. روی سرویس Postgres → تب **Variables** → گزینه‌ی `DATABASE_URL` را کپی کنید
   (یا از `Connect` → `Postgres Connection URL` بگیرید).

> پنل هر دو فرمت `postgres://` و `postgresql://` را خودش به
> `postgresql+psycopg://` تبدیل می‌کند؛ نیازی به دست‌کاری دستی نیست.

---

## بخش ۳ — سرویس پنل

> ### ⚠️ قبل از هر چیز: Root Directory را تنظیم کنید
>
> این مخزن یک **monorepo** است و اپ در ریشه نیست. اگر Root Directory را تنظیم
> نکنید، Railway با **Railpack** تلاش می‌کند از ریشه بیلد کند و با این خطا
> شکست می‌خورد:
>
> ```
> ⚠ Script start.sh not found
> ✖ Railpack could not determine how to build the app.
> ```
>
> این خطا به‌معنای «کد خراب است» نیست؛ فقط یعنی Railpack در ریشه هیچ
> `requirements.txt` یا `package.json` پیدا نکرده. **راه‌حل در قدم ۲ آمده است.**

### ۳.۱ ساخت سرویس

1. **New → GitHub Repo** → مخزن خود را انتخاب کنید.
2. سرویس ساخته‌شده را باز کنید → **Settings → Source**:
   * **Root Directory** را روی `panel` بگذارید. **این قدم اجباری است.**
     بدون آن، بیلد با خطای Railpack شکست می‌خورد (بالا را ببینید).
     با تنظیم آن، بیلد از `panel/Dockerfile` استفاده می‌کند و Railpack کاملاً
     دور زده می‌شود.
   * اگر Railway از قبل یک دیپلوی ناموفق ساخته، بعد از تغییر Root Directory
     روی **Redeploy** بزنید.
3. **Settings → Networking → Public Networking → Generate Domain** را بزنید.
4. Railway فایل `panel/railway.toml` را می‌خواند (بیلد با Dockerfile،
   healthcheck روی `/api/v1/health/live`، دستور اجرا `bash entrypoint.sh`).

### ۳.۲ متغیرهای محیطی

در تب **Variables** سرویس پنل این‌ها را اضافه کنید:

| نام | مقدار |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `PANEL_BASE_URL` | `https://${{RAILWAY_PUBLIC_DOMAIN}}` |
| `CORS_ORIGINS` | `https://${{RAILWAY_PUBLIC_DOMAIN}}` |
| `SECRET_KEY` | خروجی دستور بالا |
| `ENCRYPTION_KEY` | خروجی دستور بالا |
| `SUPERADMIN_USERNAME` | `admin` |
| `SUPERADMIN_PASSWORD` | یک رمز قوی |
| `TELEGRAM_BOT_TOKEN` | از BotFather |
| `TELEGRAM_BOT_USERNAME` | نام ربات بدون `@` |
| `TELEGRAM_WEBHOOK_SECRET` | خروجی دستور بالا |
| `TELEGRAM_ADMIN_IDS` | آیدی عددی ادمین‌ها، جدا شده با کاما |
| `TELEGRAM_AUTO_SET_WEBHOOK` | `true` |
| `ENABLE_SCHEDULER` | `true` |
| `WEB_CONCURRENCY` | `1` |
| `ENVIRONMENT` | `production` |
| `DEBUG` | `false` |

نکته: در Railway می‌توانید به متغیرهای سرویس‌های دیگر با
`${{ServiceName.VARIABLE}}` ارجاع بدهید. اگر نام سرویس Postgres شما چیز دیگری
است، همان را جایگزین کنید.

### ۳.۳ ولوم برای پشتیبان‌گیری

**Settings → Volumes → New Volume** بسازید و **Mount Path** را `/app/data`
بگذارید. این مسیر شامل `panel.db` (اگر از SQLite استفاده کنید) و پوشه‌ی
`backups/` است.

> ولوم Railway به سرویس قفل می‌شود و امکان `Replica` روی همان سرویس را از بین
> می‌برد. اگر می‌خواهید چند رپلیکا داشته باشید، از Postgres استفاده کنید و
> پشتیبان‌گیری را به سرویس Cron بسپارید.

### ۳.۴ سرویس‌های زمان‌بند (اختیاری)

اگر نمی‌خواهید زمان‌بند داخل خود وب‌سرویس اجرا شود:

1. یک سرویس دیگر از همان مخزن بسازید، Root Directory = `panel`.
2. در `railway.toml` آن سرویس (یا در تنظیمات سرویس) مقدار `cronSchedule` را
   تنظیم کنید. ساده‌ترین راه: در **Settings → Deploy → Cron Schedule**:

   | سرویس | Cron | دستور سفارشی |
   |---|---|---|
   | `panel-backup` | `0 3 * * *` | `python -m app.cli init-db && python -m app.workers.scheduler --once backup` |
   | `panel-sweep` | `*/15 * * * *` | `python -m app.cli init-db && python -m app.workers.scheduler --once sweep` |

3. در هر دو سرویس `ENABLE_SCHEDULER=false` بگذارید و `DATABASE_URL` را ست کنید.

اگر فقط یک سرویس پنل دارید، همین `ENABLE_SCHEDULER=true` کافی است؛ قفل
`pg_try_advisory_lock` تضمین می‌کند حتی با چند رپلیکا فقط یک نمونه کارها را
اجرا کند.

### ۳.۵ بررسی موفقیت دیپلوی

```bash
PANEL=https://<your-app>.up.railway.app

curl -s $PANEL/api/v1/health/live
# {"status":"ok","uptime_seconds":12}
```

اگر این جواب آمد، `https://<your-app>.up.railway.app` را باز کنید و با
`SUPERADMIN_USERNAME` / `SUPERADMIN_PASSWORD` وارد شوید.

---

## بخش ۴ — سرویس نود

### ۴.۱ ساخت سرویس

1. **New → GitHub Repo** (همان مخزن) → **Settings → Root Directory** = `node-agent`.
2. **Networking → TCP Proxy** → پورتی که اینباند Xray روی آن گوش می‌دهد را
   وارد کنید (مثلاً `443`). Railway یک آدرس `domain:port` عمومی می‌دهد؛
   آن را یادداشت کنید.
3. **Volumes → New Volume** با Mount Path = `/etc/xray` (نگه‌داشتن
   `config.json` و وضعیت بین ری‌استارت‌ها).

### ۴.۲ متغیرهای محیطی نود

| نام | مقدار |
|---|---|
| `NODE_TOKEN` | یک رشته‌ی تصادفی ۶۴ کاراکتری (`openssl rand -hex 32`) |
| `NODE_NAME` | `railway-node-1` |
| `PUBLIC_HOST` | دامنه‌ی TCP Proxy که Railway داد |
| `AGENT_PORT` | `8081` |
| `XRAY_API_PORT` | `10085` |
| `XRAY_LOG_LEVEL` | `warning` |

### ۴.۳ دسترسی پنل به ایجنت

دو راه دارید:

**الف) شبکه‌ی خصوصی Railway (توصیه‌شده).** پورت `8081` را عمومی نکنید.
در پنل، آدرس نود را این بگذارید:

```
http://<node-service-name>.railway.internal:8081
```

سرویس‌های یک پروژه در Railway از طریق `*.railway.internal` به هم دسترسی دارند
و ترافیک از اینترنت عبور نمی‌کند.

**ب) دامنه‌ی عمومی با TLS.** اگر ایجنت باید از بیرون هم در دسترس باشد، یک
دامنه‌ی HTTPS بسازید، `NODE_VERIFY_TLS=true` را در پنل نگه دارید و پورت ۸۰۸۱
را فقط با Basic-Auth/فایروال محافظت کنید. توجه کنید که TCP Proxy فقط یک پورت
را expose می‌کند؛ برای هم ایجنت و هم Xray باید دو TCP Proxy جدا بسازید.

### ۴.۳ اجرای Xray بدون VPS، روی Railway

برای اجرای کامل پروژه فقط با Railway، یک سرویس دوم از همین مخزن بسازید؛ نیازی
به سرور مجازی جدا نیست، اما دیتاپلین Xray باید جدا از وب‌سرویس پنل اجرا شود:

1. در همان Railway project از **New → GitHub Repo** همین مخزن را اضافه کنید.
   **Root Directory** را `node-agent` بگذارید و نام سرویس را دقیقاً
   `node-agent` انتخاب کنید.
2. یک متغیر مشترک و تصادفی با نام `NODE_TOKEN` بسازید؛ مقدار یکسان را روی هر
   دو سرویس `node-agent` و `xray-panel` قرار دهید. در سرویس Agent مقدارهای
   `NODE_NAME=node-agent` و `PUBLIC_HOST` را می‌توان حذف کرد.
3. در تنظیمات پنل این متغیرها را اضافه کنید:
   * `RAILWAY_NODE_TOKEN` = همان `NODE_TOKEN`
   * `RAILWAY_TCP_PROXY_DOMAIN` = مقدار
     `${{node-agent.RAILWAY_TCP_PROXY_DOMAIN}}`
   * `RAILWAY_TCP_PROXY_PORT` = مقدار `${{node-agent.RAILWAY_TCP_PROXY_PORT}}`
4. روی سرویس `node-agent` از **Settings → Networking → TCP Proxy** پورت داخلی
   `443` را منتشر کنید. متغیر Railway `RAILWAY_TCP_PROXY_PORT` پورت عمومی TCP Proxy است و نباید به‌عنوان پورت داخلی Xray ذخیره شود؛ Xray روی همان `443` گوش می‌دهد. برای UDP proxy یا
   Reality روی transport TCP بمانید.
5. یک Volume به سرویس `node-agent` با مسیر `/etc/xray` وصل کنید تا وضعیت Xray
   بعد از redeploy حفظ شود. دو سرویس را deploy کنید.
6. در پنل، **نودها → اتصال Xray روی Railway → ثبت Agent در پنل** را بزنید؛ سپس
   در صفحه Inbounds یک inbound روی پورت داخلی `443` بسازید. دامنه و پورت عمومی
   TCP Proxy به صورت خودکار به inbound داده می‌شود.

Railway برای سرویس‌ها شبکه‌ی خصوصی داخلی و برای TCP Proxy دامنه و پورت عمومی
می‌دهد. این بخش همچنان یک Agent لازم دارد، اما Agent در همان Railway اجرا
می‌شود و VPS لازم نیست. [راهنمای شبکه خصوصی Railway](https://docs.railway.com/networking/private-networking)
و [راهنمای TCP Proxy](https://docs.railway.com/networking/tcp-proxy) را ببینید.

### ۴.۴ افزودن نود در پنل (تنظیم دستی)

```bash
TOKEN=$(curl -sX POST $PANEL/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<SUPERADMIN_PASSWORD>"}' | jq -r .access_token)

curl -sX POST $PANEL/api/v1/nodes -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "name":"railway-1",
    "address":"http://railway-node-1.railway.internal:8081",
    "public_host":"<railway-tcp-domain>",
    "region":"EU","tags":["eu","default"],
    "api_token":"<NODE_TOKEN>","max_services":200
  }'
```

سپس یک اینباند بسازید و تنظیمات عمومی را پر کنید:

```bash
curl -sX POST $PANEL/api/v1/inbounds -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "node_id":1,"tag":"vless-reality","remark":"VLESS Reality",
    "protocol":"vless","port":443,"transport":"tcp","security":"reality",
    "sni":"www.cloudflare.com","flow":"xtls-rprx-vision",
    "reality_dest":"www.cloudflare.com:443",
    "reality_public_key":"<PUBLIC>","reality_private_key":"<PRIVATE>",
    "reality_short_ids":["6ba85179e30d4fc2"],
    "public_host":"<railway-tcp-domain>","public_port":<railway-tcp-port>,
    "is_default":true
  }'
```

> `public_host` و `public_port` همان چیزی است که در لینک کانفیگ مشتری می‌نشیند.
> چون TCP Proxy روی یک پورت غیراستاندارد گوش می‌دهد، این دو فیلد حیاتی‌اند.

در آخر وضعیت مطلوب را به نود بفرستید:

```bash
curl -sX POST $PANEL/api/v1/nodes/1/sync -H "Authorization: Bearer $TOKEN"
```

---

## بخش ۵ — محدودیت‌های Railway که باید بدانید

| موضوع | واقعیت |
|---|---|
| **UDP** | Railway از TCP Proxy فقط TCP عبور می‌دهد. QUIC/KCP و پروتکل‌های مبتنی بر UDP کار نمی‌کنند. روی TCP بمانید: VLESS+Reality، VMess+WS، Trojan، XHTTP. |
| **پورت خام** | برای هر پورت باید یک TCP Proxy جدا بسازید. Xray می‌تواند روی یک پورت واحد چند سرویس بدهد؛ همین را توصیه می‌کنیم. |
| **ترافیک** | سهمیه‌ی ماهانه‌ی پلن Railway مصرف می‌شود. برای حجم بالا VPS اختصاصی ارزان‌تر است. |
| **IP** | IP خروجی مشترک است؛ ممکن است در لیست‌های بلاک باشد. با یک پروکسی خروجی یا نود VPS ترکیب کنید. |
| **فایل پایدار** | فقط مسیرهای دارای Volume پایدارند. `config.json` نود و پوشه‌ی `backups` پنل باید روی Volume باشند. |
| **شرایط استفاده** | استفاده از Railway تابع قوانین خودش است. مسئولیت رعایت قوانین محلی، قوانین ارائه‌دهنده و شرایط استفاده‌ی سرویس بر عهده‌ی شماست. |

---

## بخش ۶ — اجرای محلی

```bash
cp .env.example .env      # سپس ویرایش کنید
docker compose up -d --build
# پنل: http://localhost:8000   (admin / admin)
# ایجنت: http://localhost:8081
```

یا فقط پنل با SQLite:

```bash
python -m venv .venv && . .venv/bin/activate     # ویندوز: .venv\Scripts\activate
pip install -r panel/requirements-dev.txt
cd panel
python -m app.cli init-db --seed --seed-demo
uvicorn app.main:app --reload --port 8000
```

---

## عیب‌یابی

| نشانه | علت / راه‌حل |
|---|---|
| دیپلوی روی `init-db` می‌ماند | `DATABASE_URL` درست ست نشده یا Postgres آماده نیست |
| Healthcheck رد می‌شود | باید `/api/v1/health/live` در ۱۲۰ ثانیه ۲۰۰ بدهد؛ کندی دیتابیس باعث رد شدن می‌شود |
| `Railpack could not determine how to build the app` | **Root Directory ست نشده.** Settings → Source → Root Directory = `panel` (پنل) یا `node-agent` (نود). سپس Redeploy. |
| `Script start.sh not found` | همان مورد بالا؛ Railpack به‌جای Dockerfile تلاش کرده از ریشه بیلد کند. |
| بیلد با `pip install` روی ریشه شروع می‌شود | Root Directory روی `/` مانده. آن را به `panel` تغییر دهید. |
| خطای `COPY failed: requirements.txt not found` | Root Directory روی `/` است ولی `dockerfilePath` به `panel/Dockerfile` اشاره می‌کند؛ بافت بیلد با مسیرهای داخل Dockerfile نمی‌خواند. Root Directory را `panel` بگذارید. |
| نود `offline` است | آدرس ایجنت در دسترس نیست یا توکن فرق دارد؛ `curl http://<node>.railway.internal:8081/health` |
| نود `degraded` است | یک یا دو شکست متوالی؛ فیلد `last_error` کارت نود را ببینید |
| هنگام ساخت کانفیگ خطای `502` | ایجنت درخواست را رد کرده؛ `last_error` را بخوانید و بعد `/nodes/{id}/sync` بزنید |
| کلاینت وصل می‌شود ولی ترافیک ندارد | `public_host`/`public_port` اینباند با TCP Proxy هم‌خوان نیست |
| وبهوک تلگرام `403` می‌دهد | `TELEGRAM_WEBHOOK_SECRET` بعد از ثبت وبهوک عوض شده؛ دوباره `/api/v1/telegram/setup` را صدا بزنید |
| لینک اشتراک `403` می‌دهد | سرویس منقضی یا حجم‌تمام است؛ تمدید کنید |
