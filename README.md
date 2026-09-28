# Xray Panel — پنل مدیریت سرویس پروکسی/VPN مبتنی بر Xray

پنل کامل مدیریت سرویس پروکسی با هسته‌ی **Xray**، ربات **تلگرام**، رابط کاربری
مدیریت، سهمیه و انقضا، پرداخت، گزارش مصرف، مانیتورینگ و هشدار — آماده‌ی
استقرار روی **Railway** (و Render / Docker / هر VPS).

> **معماری در یک نگاه:** پنل (کنترل‌پلین) روی Railway اجرا می‌شود و نودهای Xray
> (دیتاپلین) روی یک سرویس جدا یا VPS. دلیلش این است که Railway فقط
> HTTP/HTTPS روی `$PORT` منتشر می‌کند و اینباندهای Xray به TCP/UDP خام نیاز
> دارند. جزئیات در [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## فهرست

- [امکانات](#امکانات)
- [پشته‌ی فناوری](#پشتهی-فناوری)
- [ساختار پروژه](#ساختار-پروژه)
- [شروع سریع](#شروع-سریع)
- [استقرار](#استقرار)
- [متغیرهای محیطی](#متغیرهای-محیطی)
- [ربات تلگرام](#ربات-تلگرام)
- [مدل داده](#مدل-داده)
- [تست](#تست)
- [امنیت](#امنیت)
- [پشتیبان‌گیری](#پشتیبانگیری)
- [توسعه‌ی پروژه](#توسعهی-پروژه)

---

## امکانات

**مدیریت کاربران و دسترسی**
- احراز هویت با JWT + توکن refresh، رمزنگاری bcrypt، ورود دومرحله‌ای (TOTP)
- نقش‌ها: `user` / `support` / `admin` / `owner` با کنترل دسترسی مبتنی بر نقش
- کلید API بلندمدت برای اتوماسیون
- قفل خودکار حساب پس از تلاش‌های ناموفق + محدودیت نرخ درخواست
- کیف پول، کد معرف و پورسانت زیرمجموعه

**مدیریت کانفیگ Xray**
- چند پروتکل: **VLESS** (Reality / TLS / XTLS-Vision)، **VMess** (WS / gRPC / TCP / HTTPUpgrade / XHTTP)، **Trojan**، **Shadowsocks**
- ساخت، ویرایش، تمدید، تعلیق، حذف و همگام‌سازی کانفیگ
- تخصیص خودکار به سالم‌ترین نود بر اساس پلن، گروه نود و ظرفیت
- تولید لینک اتصال، لینک اشتراک (base64 / plain / **Clash Meta**) و **QR Code**
- سهمیه‌ی حجمی و تاریخ انقضا با اعمال خودکار
- حسابداری ترافیک مبتنی بر دلتا (مقاوم در برابر ری‌استارت Xray)

**زیرساخت**
- مدیریت چند نود با health check، ظرفیت، وزن و برچسب منطقه‌ای
- اعمال وضعیت مطلوب (desired-state) روی نود با اعتبارسنجی `xray run -test`
- افزودن/حذف کاربر بدون قطعی (hot apply) و بازگشت خودکار به reload کامل در صورت خطا

**پرداخت و فروش**
- پلن‌های قابل تنظیم (مدت، حجم، تعداد دستگاه، پروتکل‌های مجاز)
- سفارش خرید و تمدید، پرداخت دستی با رسید، کریپتو، Telegram Stars، درگاه، کیف پول
- صف بررسی ادمین، تأیید/رد/بازگشت وجه، پرداخت خودکار پس از تأیید

**گزارش و مانیتورینگ**
- داشبورد KPI، نمودار ترافیک ۳۰ روزه، پرمصرف‌ترین‌ها، درآمد
- خروجی CSV و endpoint سازگار با Prometheus
- health check سه‌سطحی (liveness / readiness / deep)
- سیستم هشدار با تشخیص تکراری‌بودن، شمارش تکرار و تحویل به تلگرام/وبهوک
- لاگ حسابرسی (audit) تغییرناپذیر برای همه‌ی عملیات حساس

**ربات تلگرام**
- **اتصال کامل از داخل پنل**: توکن و شناسه‌ی ادمین را در UI وارد کنید — بدون متغیر محیطی، بدون دیپلوی مجدد
- توکن با Fernet رمزنگاری می‌شود و هرگز در پاسخ API برنمی‌گردد
- دکمه‌های «بررسی توکن»، «پیام آزمایشی»، «ثبت وبهوک» و «حذف تنظیمات پنل» برای عیب‌یابی سریع
- دستور `/id` برای پیدا کردن آیدی عددی ادمین
- ثبت‌نام خودکار، مشاهده وضعیت، خرید و تمدید، دریافت کانفیگ و QR
- مدیریت کانفیگ‌های کاربر (تغییر نام، حذف، تمدید)
- پرداخت از کیف پول (فعال‌سازی فوری) یا کارت‌به‌کارت/کریپتو با ارسال رسید
- **پنل مدیریتی مجزا** برای ادمین: آمار، کاربران، جستجو، پرداخت‌ها، نودها، پلن‌ها، هشدار، پشتیبان‌گیری، اعلان همگانی هدفمند
- دوزبانه (فارسی/انگلیسی) با منوی دستورات جدا برای هر زبان
- تنظیمات کاربر: زبان و اعلان‌ها (انقضا، حجم، اخبار)
- محدودیت نرخ ضد اسپم + پاسخ همیشگی به callback تلگرام

---

## رابط کاربری

رابط کاربری یک SPA مستقل است — بدون مرحله‌ی build، بدون CDN اجباری.

| ویژگی | توضیح |
|---|---|
| **دوزبانه** | فارسی و انگلیسی با دکمه‌ی تغییر زبان؛ چیدمان به‌صورت خودکار RTL/LTR می‌شود |
| **دو پوسته** | حالت تیره و روشن با تشخیص تنظیمات سیستم؛ انتخاب کاربر ذخیره می‌شود |
| **آیکون‌های برداری** | ۶۶ آیکون SVG دست‌ساز روی شبکه‌ی ۲۴×۲۴ — بدون فونت آیکون و بدون درخواست شبکه |
| **انیمیشن** | گذار صفحه، ورود پله‌ای کارت‌ها، شمارنده‌ی متحرک اعداد، نمودار با انیمیشن رسم، skeleton loading، shimmer |
| **جستجوی سریع** | `Ctrl+K` برای پرش بین صفحه‌ها و اجرای دستورها |
| **واکنش‌گرا** | منوی کشویی موبایل، جدول‌های اسکرول‌پذیر، شبکه‌ی تطبیقی |
| **دسترس‌پذیری** | احترام به `prefers-reduced-motion`، فوکوس‌رینگ، برچسب‌های ARIA، ناوبری با صفحه‌کلید |
| **بدون خطای اول‌بار** | پوسته و زبان پیش از رندر در `<head>` اعمال می‌شوند تا پرش رنگ رخ ندهد |

مسیرهای عمومی مهم:

| مسیر | کاربرد |
|---|---|
| `/` | پنل مدیریتی |
| `/sub/<token>` | لینک اشتراک کوتاه (همان چیزی که در کانفیگ مشتری می‌نشیند) |
| `/sub/<token>?format=clash` | پروفایل Clash |
| `/sub/<token>/qr.png` | تصویر QR |
| `/api/docs` | مستندات تعاملی (فقط محیط غیرتولیدی) |

---

## پشته‌ی فناوری

| لایه | انتخاب | چرا |
|---|---|---|
| زبان | Python 3.12 | اکوسیستم بالغ، تایپ‌گذاری مدرن |
| فریم‌ورک | FastAPI + Pydantic v2 | اعتبارسنجی خودکار، مستندات OpenAPI، async |
| دیتابیس | PostgreSQL (تولید) / SQLite (توسعه) | SQLAlchemy 2.0 + Alembic |
| احراز هویت | PyJWT + bcrypt + pyotp | بدون وابستگی سنگین |
| رمزنگاری | Fernet (cryptography) | رمزنگاری توکن نود و کلید Reality در دیتابیس |
| HTTP | httpx | کلاینت نود و Telegram API |
| زمان‌بند | APScheduler | کارها با قفل advisory در Postgres |
| رابط کاربری | HTML/CSS/JS خالص | بدون مرحله‌ی build، بدون CDN، تم تیره |
| ربات | Bot API خام در حالت webhook | بدون فریم‌ورک، بدون پروسه‌ی اضافه |
| کانتینر | Docker (multi-stage، کاربر non-root) | Railway / Render / VPS |

---

## ساختار پروژه

```
xray-panel/
├── panel/                      ← کنترل‌پلین (روی Railway/Render)
│   ├── Dockerfile              تصویر تولیدی، کاربر non-root، healthcheck
│   ├── railway.toml            تنظیمات سرویس Railway
│   ├── entrypoint.sh           init-db → uvicorn
│   ├── requirements*.txt
│   └── app/
│       ├── main.py             ساخت اپ، میدل‌ورها، مانت SPA
│       ├── cli.py              init-db / seed-demo / create-admin / sync-nodes / check
│       ├── core/               config, security, logging, ratelimit
│       ├── db/                 base, session, models
│       ├── schemas.py          قراردادهای Pydantic
│       ├── api/v1/             ۱۳ روتر REST
│       ├── services/           منطق کسب‌وکار (provisioning, billing, alerts, backup, …)
│       ├── bot/                کلاینت تلگرام + روتر کاربر/ادمین
│       ├── workers/            زمان‌بند و اجرای تک‌شات (برای Cron)
│       ├── static/             رابط کاربری (index.html, app.js, styles.css)
│       └── tests/              ۴۷ تست pytest
├── node-agent/                 ← دیتاپلین (روی Railway TCP Proxy یا VPS)
│   ├── Dockerfile              Alpine + Xray core
│   ├── railway.toml
│   └── agent/                  main, xray_manager, xray_api, system, security
├── docs/
│   ├── ARCHITECTURE.md         معماری کامل
│   ├── API.md                  مرجع API
│   ├── DEPLOY_RAILWAY.md       راهنمای استقرار روی Railway
│   ├── DEPLOY_RENDER.md        راهنمای استقرار روی Render
│   ├── SECURITY_AND_BACKUP.md  امنیت، پشتیبان‌گیری، runbook
│   └── TELEGRAM_BOT.md         مستندات ربات
├── .railway/                   Infrastructure as Code ریلوی (اختیاری، آینده‌نگر)
├── docker-compose.yml          پشته‌ی کامل محلی
├── render.yaml                 Blueprint رندر
└── .env.example
```

---

## شروع سریع

### با Docker Compose (کامل، شامل Postgres و یک نود)

```bash
cp .env.example .env
docker compose up -d --build
```

- پنل: <http://localhost:8000> — ورود با `admin` / `admin12345`
- ایجنت نود: <http://localhost:8081/health>
- Postgres: `localhost:5432`

### فقط پنل، با SQLite

```bash
python -m venv .venv
. .venv/bin/activate            # ویندوز: .venv\Scripts\activate
pip install -r panel/requirements-dev.txt

cd panel
python -m app.cli init-db --seed --seed-demo
uvicorn app.main:app --reload --port 8000
```

`--seed-demo` یک نود نمونه، سه اینباند (VLESS Reality، VMess WS، Trojan TLS)،
چهار پلن و یک کاربر آزمایشی (`demo` / `demo12345`) می‌سازد.

### تست کامل روی لپ‌تاپ (بدون Xray و بدون IP عمومی)

اگر ویندوز/مک دارید یا نمی‌خواهید Xray واقعی نصب کنید، از دو ابزار کمکی در
پوشه‌ی `tools/` استفاده کنید:

```bash
# ترمینال ۱ — پنل
cd panel && uvicorn app.main:app --reload --port 8000

# ترمینال ۲ — ایجنت نود تقلبی (همان پروتکل، ولی بدون xray-core)
python tools/dev_stub_agent.py --port 8081

# ترمینال ۳ — پر کردن پنل با داده‌ی نمونه
python tools/dev_seed_demo.py --base http://127.0.0.1:8000
```

`dev_stub_agent.py` نود را «آنلاین» نشان می‌دهد و مصرف را شبیه‌سازی می‌کند تا
بتوانید ساخت کانفیگ، همگام‌سازی، لینک/QR و گزارش مصرف را کامل تست کنید.
**این فایل فقط برای توسعه است و هیچ احراز هویتی ندارد — هرگز آن را منتشر نکنید.**

### بررسی سلامت تنظیمات

```bash
python -m app.cli check
```

روی `SECRET_KEY` پیش‌فرض، رمز ادمین پیش‌فرض، CORS باز و SQLite در محیط تولید
هشدار می‌دهد.

---

## استقرار

| پلتفرم | سند |
|---|---|
| **Railway** (پیشنهادی) | [`docs/DEPLOY_RAILWAY.md`](docs/DEPLOY_RAILWAY.md) |
| Render | [`docs/DEPLOY_RENDER.md`](docs/DEPLOY_RENDER.md) |
| VPS / Docker | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) + `docker-compose.yml` |

> ### ⚠️ Railway: اول Root Directory را تنظیم کنید
>
> این مخزن یک **monorepo** است و اپ در ریشه نیست. اگر Root Directory سرویس را
> تنظیم نکنید، Railway با **Railpack** از ریشه بیلد می‌کند و شکست می‌خورد:
>
> ```
> ⚠ Script start.sh not found
> ✖ Railpack could not determine how to build the app.
> ```
>
> **راه‌حل:** `Settings → Source → Root Directory`
>
> | سرویس | Root Directory |
> |---|---|
> | پنل | `panel` |
> | نود | `node-agent` |
>
> بعد از تغییر، **Redeploy** بزنید.

خلاصه‌ی Railway:

1. **Postgres** را به‌عنوان پلاگین اضافه کنید.
2. سرویس **panel** را از مخزن بسازید و **Root Directory = `panel`** بگذارید.
   `DATABASE_URL=${{Postgres.DATABASE_URL}}` و بقیه‌ی متغیرها را ست کنید.
3. سرویس **node-agent** را بسازید و **Root Directory = `node-agent`** بگذارید،
   یک **TCP Proxy** روی پورت اینباند (مثلاً 443) بسازید و یک Volume روی `/etc/xray`.
4. در پنل، نود را با آدرس `http://<node>.railway.internal:8081` و همان
   `NODE_TOKEN` ثبت کنید.
5. اینباند بسازید و `POST /api/v1/nodes/{id}/sync` را بزنید.

---

## متغیرهای محیطی

فایل کامل در [`.env.example`](.env.example) است. مهم‌ترین‌ها:

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/panel.db` | در تولید حتماً Postgres |
| `SECRET_KEY` | — | امضای JWT؛ حداقل ۶۴ کاراکتر تصادفی |
| `ENCRYPTION_KEY` | مشتق از `SECRET_KEY` | رمزنگاری توکن نود و کلید Reality |
| `PANEL_BASE_URL` | `http://localhost:8000` | پایه‌ی لینک اشتراک و وبهوک تلگرام |
| `SUPERADMIN_USERNAME` / `SUPERADMIN_PASSWORD` | `admin` | حساب مالک که در اولین بوت ساخته می‌شود |
| `TELEGRAM_ENABLED` | `true` | فعال‌سازی ربات |
| `TELEGRAM_BOT_TOKEN` | — | از BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | — | اعتبارسنجی وبهوک |
| `TELEGRAM_ADMIN_IDS` | — | آیدی عددی ادمین‌ها، با کاما |
| `ENABLE_SCHEDULER` | `true` | اجرای کارهای زمان‌بندی‌شده داخل وب‌سرویس |
| `TRAFFIC_SYNC_INTERVAL_SECONDS` | `120` | بازه‌ی خواندن مصرف از نودها |
| `QUOTA_ENFORCEMENT_ENABLED` | `true` | قطع سرویس پس از اتمام حجم |
| `GRACE_PERIOD_HOURS` | `24` | مهلت پس از انقضا |
| `BACKUP_ENABLED` / `BACKUP_RETENTION_DAYS` | `true` / `14` | پشتیبان‌گیری خودکار |
| `ALERT_WEBHOOK_URL` | — | ارسال هشدار به Slack/Discord |
| `WEB_CONCURRENCY` | `1` | تعداد ورکر uvicorn |

---

## ربات تلگرام

ربات در حالت **webhook** داخل همین سرویس اجرا می‌شود؛ نه پروسه‌ی جدا لازم دارد
نه polling. مسیر دریافت:
`POST /api/v1/telegram/webhook/{TELEGRAM_WEBHOOK_SECRET}`

جریان کاربر: `/start` → منوی اصلی → حساب من / کانفیگ‌های من / خرید / تمدید /
کیف پول / زیرمجموعه / پشتیبانی.

جریان خرید: انتخاب پلن → ساخت سفارش → نمایش شماره کارت → ارسال عکس رسید →
دکمه‌های تأیید/رد برای ادمین → تأیید → ساخت خودکار کانفیگ + پرداخت پورسانت
زیرمجموعه → اطلاع به کاربر.

جریان ادمین: `/admin` → آمار، کاربران، جستجوی کاربر، پرداخت‌های در انتظار،
نودها، گزارش، هشدارها، اعلان همگانی.

جزئیات کامل در [`docs/TELEGRAM_BOT.md`](docs/TELEGRAM_BOT.md).

---

## مدل داده

```
User ──┬── Service ──┬── Inbound ── Node
       │             │
       │             └── Plan
       ├── Payment
       ├── TrafficDaily
       └── BotUser            (نگاشت هویت تلگرام + وضعیت گفتگو)

Node ──── NodeMetric
Alert · AuditLog · Setting · BackupRecord
```

- `services` = یک کانفیگ تحویل‌شده با سهمیه و انقضا؛ `email_tag` هویت آماری آن در Xray است.
- `traffic_daily` مبنای همه‌ی گزارش‌های مصرف است.
- `audit_logs` فقط افزودنی است و همه‌ی عملیات حساس را ثبت می‌کند.

---

## تست

```bash
cd panel
pytest                       # ۸۸ تست
pytest --cov=app -q          # با پوشش کد
```

| فایل | تعداد | پوشش |
|---|---|---|
| `test_auth.py` | ۱۴ | ورود، refresh، RBAC، محدودیت نرخ، کلید API، رمز ضعیف |
| `test_bot.py` | ۲۶ | دوزبانگی ربات، ثبت‌نام، مجوزدهی، جریان خرید، تأیید پرداخت، مالکیت کانفیگ، ضد اسپم |
| `test_telegram_config.py` | ۳۷ | اتصال ربات از پنل: اولویت دیتابیس بر env، رمزنگاری در حالت سکون، ماسک شدن توکن، اعتبارسنجی، وبهوک و چرخش کلید |
| `test_ui_contract.py` | ۱۵ | تطبیق شناسه‌های HTML با JS، کامل بودن آیکون‌ها و کلیدهای ترجمه، توکن‌های هر دو پوسته، مسیر `/sub` |
| `test_services.py` | ۱۴ | چرخه‌ی عمر سرویس، سهمیه، انقضا، تمدید، حسابداری دلتا، عملیات گروهی |
| `test_subscription.py` | ۱۰ | هر چهار فرمت اشتراک، محافظ انقضا/سهمیه، توکن نامعتبر |
| `test_links.py` | ۹ | تولید لینک VLESS/VMess/Trojan/SS، Clash YAML، QR |

تماس‌های شبکه‌ای (ایجنت نود و Telegram API) در تست‌ها mock می‌شوند، پس مجموعه
بدون شبکه و بدون سرور واقعی اجرا می‌شود.

---

## امنیت

- رمز عبور با bcrypt (cost 12)؛ توکن JWT کوتاه‌مدت + refresh مجزا
- محدودیت نرخ در ورود و کل API؛ قفل حساب پس از تلاش‌های ناموفق
- توکن نود و کلید خصوصی Reality در دیتابیس با Fernet رمزنگاری می‌شوند
- ایجنت نود با Bearer + امضای HMAC و پنجره‌ی زمانی ۳۰۰ ثانیه (ضد بازپخش)
- هدرهای امنیتی، اعتبارسنجی Host، CORS محدود، غیرفعال‌سازی Swagger در تولید
- لاگ حسابرسی برای همه‌ی عملیات حساس

جزئیات و چک‌لیست پیش از انتشار در
[`docs/SECURITY_AND_BACKUP.md`](docs/SECURITY_AND_BACKUP.md).

---

## پشتیبان‌گیری

- کار روزانه: `pg_dump` (یا dump منطقی JSON برای SQLite) + gzip + SHA-256
- نگه‌داری با `BACKUP_RETENTION_DAYS`
- بازیابی دستی: `POST /api/v1/backups/{filename}/restore`
- قالب‌های آماده در [`docs/SECURITY_AND_BACKUP.md`](docs/SECURITY_AND_BACKUP.md)
  شامل drill بازیابی و runbook عملیاتی

---

## توسعه‌ی پروژه

| کار | محل |
|---|---|
| افزودن پروتکل جدید | `services/xray_links.py` + `node-agent/agent/xray_manager.py` + enum در `db/models.py` |
| افزودن درگاه پرداخت | `services/billing.py` (فراخوانی `fulfil_payment`) + روتر در `api/v1/` |
| افزودن کانال اطلاع‌رسانی | `services/alerts.py::dispatch_alert` |
| افزودن صفحه‌ی جدید به UI | `<section class="page">` در `static/index.html` + `Pages.<name>` در `static/app.js` |
| افزودن کار زمان‌بندی‌شده | دیکشنری `JOBS` در `workers/scheduler.py` |

### مجوز و شرایط استفاده

این پروژه ابزار مدیریت زیرساخت است. مسئولیت رعایت قوانین محلی، قوانین
ارائه‌دهنده‌ی میزبانی، شرایط استفاده‌ی سرویس‌های بالادستی و حقوق کاربران بر
عهده‌ی شماست. از این پنل برای سوءاستفاده، اسپم یا حمله به اشخاص ثالث استفاده
نکنید. مسیر مسدودسازی bittorrent و سهمیه‌های هر سرویس برای اجرای سیاست
استفاده‌ی منصفانه در نظر گرفته شده‌اند.
