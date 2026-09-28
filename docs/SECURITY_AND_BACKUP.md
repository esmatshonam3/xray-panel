# امنیت، بهره‌برداری و پشتیبان‌گیری

## ۱. مدل تهدید

| دارایی | تهدید | کاهش‌دهنده |
|---|---|---|
| اعتبارنامه‌ی پنل | پرکردن اعتبار، brute force | bcrypt با cost ۱۲، محدودیت پنجره‌ای ورود، قفل حساب (`failed_login_count` + `locked_until`)، TOTP اختیاری |
| توکن نشست | سرقت، بازپخش | JWT کوتاه‌مدت (۱۲ ساعت)، توکن refresh با نوع مجزا، الزام claim `iss`، `jti` یکتا |
| آدرس اشتراک | اشتراک‌گذاری / شمارش | توکن ۳۲ کاراکتری `secrets.token_urlsafe`، محدودیت نرخ بر اساس IP، ۴۰۴ برای توکن ناشناس، هدرهای کش که مانع کش شدن در پروکسی مشترک می‌شود |
| API نود | سرقت توکن، بازپخش | الزام TLS، امضای HMAC-SHA256 روی `timestamp || body`، پنجره‌ی ۳۰۰ ثانیه، قابلیت چرخش توکن هر نود |
| توکن نود و کلید خصوصی Reality در حالت سکون | dump دیتابیس | رمزنگاری Fernet با `ENCRYPTION_KEY`؛ متن خام هرگز از هیچ API برنمی‌گردد |
| هسته‌ی Xray | کرش، کانفیگ خراب | اعتبارسنجی `xray run -test` پیش از هر reload، جایگزینی اتمیک فایل، راه‌اندازی خودکار هسته‌ی مرده توسط کار health |
| ردگیری عملیات | انکار | `audit_logs` فقط افزودنی؛ عملیات حساس به‌صورت JSON ساخت‌یافته در stdout هم چاپ می‌شوند |
| از دست رفتن داده | خرابی پلتفرم | PITR پستگرس + پشتیبان‌گیری زمان‌بندی‌شده با checksum |

**خارج از دامنه:** این پنل زیرساختی را مدیریت می‌کند که متعلق به خودتان است.
رعایت قوانین محلی، سیاست استفاده‌ی منصفانه‌ی ارائه‌دهنده‌ی میزبانی و شرایط
استفاده‌ی هر شبکه‌ی بالادستی بر عهده‌ی شماست. از این پنل برای سوءاستفاده،
اسپم یا حمله به اشخاص ثالث استفاده نکنید — مسیر مسدودسازی bittorrent و
سهمیه‌های هر سرویس برای اجرای سیاست استفاده‌ی منصفانه در نظر گرفته شده‌اند.

## ۲. چک‌لیست سخت‌سازی

پیش از انتشار عمومی:

- [ ] `SECRET_KEY` حداقل ۶۴ کاراکتر تصادفی است (`python -c "import secrets;print(secrets.token_urlsafe(64))"`)
- [ ] `SUPERADMIN_PASSWORD` از مقدار پیش‌فرض عوض شده و مالک 2FA دارد
- [ ] `TELEGRAM_WEBHOOK_SECRET` تصادفی است (نه `webhook`)
- [ ] `CORS_ORIGINS` و `TRUSTED_HOSTS` به دامنه‌ی خودتان محدود شده‌اند
- [ ] `DEBUG=false` و `ENVIRONMENT=production` (این `/api/docs` را غیرفعال می‌کند)
- [ ] `NODE_VERIFY_TLS=true` و ایجنت پشت گواهی معتبر
- [ ] دیتابیس PostgreSQL مدیریت‌شده است، نه SQLite
- [ ] `ALERT_WEBHOOK_URL` تنظیم شده تا خرابی به دست انسان برسد
- [ ] کار پشتیبان‌گیری با بازیابی واقعی در یک دیتابیس آزمایشی صحه‌گذاری شده
- [ ] SSH نود فقط با کلید؛ فایروال جز پورت‌های اینباند همه را می‌بندد

`python -m app.cli check` را اجرا کنید — روی رمز پیش‌فرض، CORS باز و
SQLite در محیط تولید با خطا متوقف می‌شود.

## ۳. مدیریت اسرار

| سرّ | محل | چرخش |
|---|---|---|
| `SECRET_KEY` | متغیر محیطی | چرخش، همه‌ی JWTها را باطل می‌کند و اگر `ENCRYPTION_KEY` ست نشده باشد کلید رمزنگاری را هم عوض می‌کند — **هر دو را با هم بچرخانید** |
| `ENCRYPTION_KEY` | متغیر محیطی | نیازمند رمزنگاری مجدد `nodes.api_token_enc` و `inbounds.reality_private_key_enc`؛ در پنجره‌ی نگه‌داری انجام دهید |
| توکن نود | `POST /nodes/{id}/rotate-token` | `NODE_TOKEN` روی ایجنت را عوض و کانتینر را ری‌استارت کنید |
| توکن تلگرام | BotFather → `/revoke` | متغیر را عوض، دیپلوی مجدد و `/telegram/setup` را صدا بزنید |
| کلید API کاربر | `POST /auth/api-key` | کلید قبلی بلافاصله از کار می‌افتد |

هرگز سرّی را در مخزن نگذارید. `.env` در gitignore است؛ `.env.example` فقط
جای‌نگهدار دارد.

## ۴. پشتیبان‌گیری

### چه اتفاقی می‌افتد

`workers/scheduler.py → job_backup` (پیش‌فرض روزانه، به‌علاوه‌ی سرویس Cron):

1. PostgreSQL → `pg_dump --clean --if-exists | gzip` در `BACKUP_DIR`
2. اگر `pg_dump` نبود (یا دیتابیس SQLite بود) → **dump منطقی JSON** از همه‌ی
   جدول‌ها، gzip شده. قابل انتقال بین موتورهای دیتابیس.
3. ثبت SHA-256 و حجم در `backup_records`
4. نگه‌داری: فایل‌های قدیمی‌تر از `BACKUP_RETENTION_DAYS` (پیش‌فرض ۱۴) پاک می‌شوند

دستی: `POST /api/v1/backups` یا دکمه‌ی **Settings → Backups → Create backup**.

### یک‌بار واقعاً بازیابی را تست کنید

قبل از اینکه لازم شود، حتماً انجام دهید:

```bash
# ۱. فایل را از دیسک پلتفرم (یا نسخه‌ی off-site) بگیرید
railway volume files download /app/data/backups ./backups
# یا روی Render:  render ssh srv-xxxx -- tar czf - -C /app/data/backups . > backups.tgz

# ۲. در یک پستگرس آزمایشی بازیابی کنید
createdb xpanel_restore
gunzip -c panel-auto-20260928-030000.sql.gz | psql postgresql://localhost/xpanel_restore

# ۳. بررسی صحت
psql postgresql://localhost/xpanel_restore -c \
  "select (select count(*) from users) users, (select count(*) from services) services,
          (select count(*) from traffic_daily) traffic_rows;"
```

برای SQLite و dumpهای منطقی، `POST /backups/{filename}/restore` بازیابی درجا
را انجام می‌دهد (در تولید روی Postgres مدیریت‌شده مسدود است — آنجا از PITR
پلتفرم استفاده کنید).

### نسخه‌ی خارج از سایت

`services/backup.py::_upload_remote()` یک hook مستند و بی‌اثر است. با SDK
دلخواه خود S3 / Cloudflare R2 / Backblaze B2 را وصل کنید.
**پشتیبانی که فقط روی همان پلتفرمی باشد که از کار افتاده، پشتیبان نیست.**

### سناریوهای بازیابی

| سناریو | راه‌حل |
|---|---|
| پنل بعد از دیپلوی بد رفتار می‌کند | Rollback به دیپلوی قبلی در پلتفرم |
| مهاجرت خراب / خرابی داده | PITR پستگرس، یا بازیابی آخرین dump در دیتابیس جدید و تغییر `DATABASE_URL` |
| از دست رفتن نود | VPS جدید، اجرای ایجنت با همان `NODE_TOKEN`، تغییر `address` نود به هاست جدید، سپس `POST /nodes/{id}/sync` — پنل همه‌ی اینباندها و کاربران را دوباره می‌فرستد |
| حذف تصادفی کانفیگ | `POST /nodes/{id}/sync` همه‌چیز را از دیتابیس (منبع حقیقت) بازمی‌سازد |
| توکن نود لو رفته | `POST /nodes/{id}/rotate-token` و ری‌استارت ایجنت |

## ۵. رصدپذیری

* **`/api/v1/health/live`** — liveness. پلتفرم در صورت شکست کانتینر را ری‌استارت می‌کند.
* **`/api/v1/health/ready`** — دسترسی به دیتابیس.
* **`/api/v1/health`** — گزارش عمیق: تأخیر دیتابیس، رهبری زمان‌بند، نسبت نودهای
  آنلاین، انبار هشدار، دسترسی به تلگرام. نشان سلامت داشبورد همین را می‌خواند.
* **`/api/v1/metrics`** — قالب Prometheus (`xpanel_services{status=…}`،
  `xpanel_node_cpu_percent{node=…}`، `xpanel_traffic_bytes_total`، …). با پلن
  رایگان Grafana Cloud قابل scrape است.
* **لاگ‌ها** — هر خط یک شیء JSON با `request_id` که در هدر `X-Request-Id` هم
  برمی‌گردد؛ پس خطای دیده‌شده در UI به خط لاگ نگاشت می‌شود. درخواست‌های کند
  (بیش از ۲ ثانیه) با سطح WARNING و مدت‌زمان ثبت می‌شوند.
* **هشدارها** — `node.unreachable`، `node.xray_down`، `node.cpu_high`،
  `node.memory_high`، `node.disk_high`، `service.expiring`. با fingerprint
  یکتا می‌شوند و پنجره‌ی اعلان مجدد ۳۰ دقیقه است؛ به ادمین‌های تلگرام و
  در صورت تنظیم به وبهوک تحویل می‌شوند.

## ۶. محدودیت نرخ

| دامنه | پیش‌فرض | محل |
|---|---|---|
| API پنل | ۲۴۰ درخواست در دقیقه به ازای هر IP+مسیر | `core/ratelimit.py` با `Depends(rate_limit)` |
| ورود | ۸ تلاش در ۱۵ دقیقه به ازای هر IP+نام کاربری، سپس ۴۲۹ | `api/v1/auth.py` |
| وبهوک تلگرام | ۶۰۰ در دقیقه (مازاد بی‌صدا حذف می‌شود تا تلگرام دوباره تلاش نکند) | `api/v1/telegram.py` |
| دریافت اشتراک | همان محدودکننده‌ی پنل | `api/v1/subscription.py` |

پشت چند نمونه، این محدودیت‌ها per-instance هستند. هنگام مقیاس افقی
`SlidingWindowLimiter` را با نسخه‌ی مبتنی بر Redis عوض کنید؛ رابط عمداً یکسان
نگه داشته شده است.

## ۷. راهنمای عملیاتی

**کاربران حجم‌تمام یا منقضی هنوز وصل هستند**
پنل با فراخوانی `DELETE /users/{tag}/{email}` روی ایجنت آن‌ها را از هسته‌ی در
حال اجرا حذف می‌کند. اگر نود در دسترس نباشد، ردیف همچنان `limited`/`expired`
علامت می‌خورد و `sync_error` ست می‌شود؛ به‌محض بازگشت نود،
`POST /nodes/{id}/sync` وضعیت را هم‌سان می‌کند.

**مصرف به‌روز نمی‌شود**
1. `GET /api/v1/nodes/{id}/health` → `xray_running` باید `true` باشد
2. `POST /api/v1/services/{id}/pull-usage` برای خواندن فوری
3. `ENABLE_SCHEDULER` و اجرای کارهای `traffic`/`sweep` را بررسی کنید
   (`GET /api/v1/health` وضعیت رهبری زمان‌بند را گزارش می‌دهد)

**نود مدام قطع و وصل می‌شود**
`consecutive_failures >= 3` آن را `offline` و یک هشدار بحرانی ثبت می‌کند.
`last_error`، فضای دیسک و `docker logs xray-node` را بررسی کنید.

**خاموش کردن اضطراری یک نود**
`PATCH /api/v1/nodes/{id}` با `{"is_active": false}` — دیگر کانفیگ جدید
نمی‌گیرد. کاربران فعلی تا زمانی که سرویس‌هایشان را غیرفعال نکنید کار می‌کنند.
