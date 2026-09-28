# معماری

## ۱. چرا معماری دو بخشی (کنترل‌پلین / دیتاپلین)

Railway و Render ترافیک را در لبه‌ی خود خاتمه می‌دهند و **فقط HTTP(S) روی
`$PORT`** را منتشر می‌کنند. اینباندهای Xray اما به سوکت خام **TCP/UDP** نیاز
دارند — VLESS+Reality، VMess+WS، Trojan، Shadowsocks، XTLS. هیچ PaaS‌ای این
سوکت‌ها را فوروارد نمی‌کند (به‌جز TCP Proxy در Railway که فقط TCP است).

پس سیستم به دو نیم تقسیم می‌شود:

| بخش | چیست | کجا اجرا می‌شود |
|---|---|---|
| **کنترل‌پلین** | API، رابط کاربری، ربات تلگرام، زمان‌بند، دیتابیس | Railway / Render (Docker + Postgres) |
| **دیتاپلین** | پروسه‌ی `xray-core` + ایجنت نود | Railway با TCP Proxy، یا هر VPS با IP عمومی |

کنترل‌پلین **تنها منبع حقیقت** است. دیتاپلین جز فایل `config.json` و وضعیت
کاربرانش هیچ حالت مستقلی ندارد، بنابراین هر لحظه می‌توان آن را بازسازی یا
افقی مقیاس‌پذیر کرد.

```
                    ┌──────────────────────────────────────────────┐
  تلگرام ──webhook─▶│  سرویس وب پنل  (panel/Dockerfile)            │
                    │                                              │
  مرورگر ──HTTPS───▶│  FastAPI  ── /api/v1/*                       │
                    │    ├── SPA مدیریتی (static/)                 │
                    │    ├── /sub/<token>  اشتراک عمومی            │
                    │    ├── وبهوک تلگرام + کنسول ادمین ربات       │
                    │    └── کارهای APScheduler                    │
                    └──────┬────────────────────────┬──────────────┘
                           │                        │
                  SQLAlchemy│              HTTPS + HMAC│
                           ▼                        ▼
                   ┌───────────────┐      ┌──────────────────────────┐
                   │  PostgreSQL    │      │  ایجنت نود (VPS/Railway) │
                   │  (پایدار)      │      │  node-agent/Dockerfile   │
                   └───────────────┘      │   ├── FastAPI (پورت 8081)│
                                          │   ├── state.json         │
                                          │   ├── config.json        │
                                          │   └── xray-core          │
                                          └───────────┬──────────────┘
                                                      │ TCP خام
                                              کلاینت‌ها (v2rayNG،
                                              Clash، Streisand، …)
```

## ۲. ساختار داخلی پنل

```
app/
├── main.py              ساخت اپ: میدل‌ورها، روترها، SPA، چرخه‌ی عمر
├── cli.py               init-db / seed-demo / create-admin / sync-nodes / check
├── core/
│   ├── config.py        pydantic-settings، نرمال‌سازی متغیرها
│   ├── security.py      bcrypt، JWT، HMAC، Fernet، TOTP
│   ├── logging.py       لاگ JSON + contextvar برای request-id
│   └── ratelimit.py     پنجره‌ی لغزان + سطل توکن
├── db/
│   ├── base.py          Base، TimestampMixin، utcnow()، as_utc()
│   ├── session.py       engine، SessionLocal، session_scope()، pragma های SQLite
│   └── models.py        مدل دامنه (بخش ۴)
├── schemas.py           قراردادهای ورودی/خروجی با Pydantic v2
├── api/
│   ├── deps.py          احراز هویت، نگهبان‌های RBAC، صفحه‌بندی، محدودیت نرخ
│   └── v1/              auth، users، plans، services، nodes، subscription،
│                        payments، reports، monitoring، settings، telegram
├── services/            منطق کسب‌وکار (بی‌خبر از HTTP)
│   ├── provisioning.py  ساخت/تمدید/تعلیق/همگام‌سازی + سهمیه + انقضا
│   ├── node_client.py   کلاینت HTTP امضاشده برای ایجنت نود
│   ├── xray_links.py    ساخت URI، اشتراک، Clash YAML، QR
│   ├── billing.py       سفارش، تأیید، کیف پول، زیرمجموعه
│   ├── alerts.py        تشخیص تکراری، تحویل تلگرام/وبهوک
│   ├── backup.py        pg_dump / dump منطقی، بازیابی، نگه‌داری
│   ├── audit.py         ردگیری تغییرناپذیر
│   └── serializers.py   تبدیل ردیف دیتابیس به خروجی API/ربات
├── bot/
│   ├── telegram.py      کلاینت خام Bot API + سازنده‌های کیبورد
│   └── router.py        جریان‌های کاربر + کنسول مجزای ادمین
├── workers/
│   └── scheduler.py     کارهای APScheduler + حالت تک‌شات برای Cron
└── static/              SPA دوزبانه با دو پوسته (بدون build، بدون CDN)
    ├── index.html       پوسته‌ی HTML + بوت‌استرپ تم/زبان پیش از رندر
    ├── styles.css       سیستم طراحی: توکن‌های تم، RTL با ویژگی‌های منطقی، انیمیشن‌ها
    ├── icons.js         ۶۶ آیکون SVG روی شبکه‌ی ۲۴×۲۴ (تابع icon + hydrateIcons)
    ├── i18n.js          دیکشنری fa/en، درون‌یابی، قالب‌بندی عدد/تاریخ/حجم
    └── app.js           روتر هش، صفحه‌ها، نمودارها، پالت فرمان (Ctrl+K)
```

### ترتیب ثبت مسیرها (حساس)

`main.create_app()` مسیرها را به این ترتیب ثبت می‌کند و **ترتیب مهم است**:

1. `/api/v1/*` — روترهای API
2. `/sub/*` — لینک اشتراک کوتاه
3. `/assets/*` — فایل‌های استاتیک
4. `/{full_path:path}` — fallback برای SPA

اگر fallback زودتر ثبت شود، درخواست `/sub/<token>` به‌جای کانفیگ، HTML پنل
برمی‌گرداند و کلاینت‌ها خطای parse می‌دهند. مسیرهای زیر `api/` و `sub/` در
fallback صریحاً ۴۰۴ JSON برمی‌گردانند تا این اشتباه هرگز بی‌صدا نماند.

### رابط کاربری

SPA بدون مرحله‌ی build. `i18n.js` جهت صفحه را با `dir` عوض می‌کند و
`styles.css` همه‌جا از ویژگی‌های منطقی (`inset-inline-start`،
`margin-inline-start`) استفاده می‌کند، پس RTL بدون هیچ قاعده‌ی تکراری کار
می‌کند. `index.html` یک اسکریپت کوچک در `<head>` دارد که تم و زبان را پیش از
اولین رندر اعمال می‌کند تا پرش رنگ رخ ندهد.

`tests/test_ui_contract.py` این قراردادها را قفل می‌کند: هر شناسه‌ای که
`app.js` می‌خواند باید در HTML باشد، هر آیکون و کلید ترجمه‌ای که استفاده
می‌شود باید تعریف شده باشد، و توکن‌های هر دو پوسته باید موجود باشند.

### چرخه‌ی یک درخواست

1. `main.py` یک شناسه‌ی درخواست می‌سازد، زمان‌سنجی می‌کند و هدرهای امنیتی می‌چسباند.
2. `api/deps.py` هویت را از JWT یا `X-API-Key` استخراج می‌کند، RBAC و
   محدودیت نرخ را اعمال می‌کند.
3. روترها با Pydantic اعتبارسنجی می‌کنند و کار را به `services/*` می‌سپارند.
4. `services/provisioning.py` **تنها** ماژولی است که دیتاپلین را تغییر می‌دهد؛
   هر تغییر یک ردیف `AuditLog` می‌نویسد.

### زمان‌بندی

`workers/scheduler.py` شش کار دارد: `traffic` (خواندن مصرف)، `sweep` (سهمیه و
انقضا)، `reminders`، `health`، `backup`، `maintenance`.

دو مکانیزم محافظتی:

* **انتخاب رهبر** — قفل advisory در Postgres (`pg_try_advisory_lock`) تضمین
  می‌کند حتی با چند نمونه فقط یکی کارها را اجرا کند.
* **جایگزین Cron** — اگر نمی‌خواهید زمان‌بند داخل وب‌سرویس باشد،
  `ENABLE_SCHEDULER=false` بگذارید و همان کد را با
  `python -m app.workers.scheduler --once <job>` از سرویس Cron صدا بزنید.

## ۳. پروتکل ارتباط پنل و ایجنت نود

همه‌ی درخواست‌ها JSON روی HTTPS با این هدرها هستند:

```
Authorization: Bearer <node-token>
X-Node-Timestamp: <ثانیه‌ی یونیکس>
X-Node-Signature: HMAC_SHA256(node-token, timestamp || raw-body)
```

ایجنت اختلاف زمانی بیش از `SIGNATURE_TTL_SECONDS` (پیش‌فرض ۳۰۰ ثانیه) را رد
می‌کند، پس یک درخواست شنود‌شده قابل بازپخش نیست.

| endpoint | کار |
|---|---|
| `GET /health` | وضعیت پروسه و Xray (عمومی، برای probe) |
| `GET /health/deep` | + CPU/RAM/دیسک/شبکه و راه‌اندازی خودکار هسته‌ی مرده |
| `POST /inbounds/apply` | جایگزینی کل اینباندها؛ ایجنت `config.json` را می‌سازد و اعتبارسنجی می‌کند |
| `POST /users/apply` | جایگزینی کاربران یک اینباند |
| `POST /users` و `DELETE /users/{tag}/{email}` | افزودن/حذف تکی |
| `GET /stats?reset=` | شمارنده‌ی آپلینک/داونلینک هر کاربر |
| `POST /restart` | ری‌لود هسته |
| `POST /reality-keys` | تولید جفت‌کلید X25519 برای Reality |

**راهبرد اعمال تغییر.** ایجنت ابتدا از API زمان‌اجرای Xray
(`xray api adu` / `rmu`) استفاده می‌کند تا تغییرات کاربران بدون قطعی اعمال
شود. اگر هر عملیات hot شکست بخورد — معمولاً به‌خاطر تفاوت امضای CLI در نسخه‌های
مختلف — به مسیر پشتیبان برمی‌گردد: نوشتن `config.json`، اعتبارسنجی با
`xray run -test` و ری‌استارت پروسه. مسیر پشتیبان همیشه درست است، فقط کوتاه
مدت قطعی دارد؛ بنابراین یک دیپلوی خراب هرگز نود را در وضعیت ناسازگار رها
نمی‌کند.

## ۴. مدل داده

```
User ──┬── Service ──┬── Inbound ── Node
       │             │
       │             └── Plan
       ├── Payment
       ├── TrafficDaily
       └── BotUser            (نگاشت ۱:۱ هویت تلگرام + وضعیت گفتگو)

Node ──── NodeMetric
Alert · AuditLog · Setting · BackupRecord  (مستقل)
```

جدول‌های کلیدی:

| جدول | نکته |
|---|---|
| `users` | نقش (`user/support/admin/owner`)، وضعیت، موجودی کیف پول، گراف معرف، TOTP، هش کلید API، قفل ورود |
| `plans` | قیمت، مدت، حجم گیگابایت (۰ = نامحدود)، پروتکل‌های مجاز، گروه نود، تعداد کانفیگ |
| `nodes` | آدرس ایجنت، هاست عمومی، توکن **رمزنگاری‌شده با Fernet**، وضعیت سلامت، سقف ظرفیت |
| `inbounds` | پروتکل، پورت، ترنسپورت، امنیت، مواد TLS/Reality (کلید خصوصی رمزنگاری‌شده)، بازنویسی endpoint عمومی |
| `services` | کانفیگ تحویل‌شده: uuid/رمز، `email_tag` (هویت آماری در Xray)، سهمیه، شمارنده‌های `last_raw_*`، توکن اشتراک |
| `traffic_daily` | مصرف روزانه‌ی هر سرویس؛ مبنای همه‌ی گزارش‌ها |
| `payments` | کد پیگیری، نوع، روش، وضعیت، رسید، ردگیری بررسی |
| `alerts` | تشخیص تکراری با fingerprint، شمارنده‌ی تکرار، جریان تأیید |
| `audit_logs` | فقط افزودنی؛ عملیات حساس علاوه بر دیتابیس در stdout هم چاپ می‌شوند |

**حسابداری ترافیک** از شمارنده‌های تجمعی نود استفاده می‌کند و آخرین مقدار خام
را ذخیره می‌کند، پس فقط دلتا اضافه می‌شود. اگر شمارنده صفر شود (ری‌استارت
Xray)، افت شمارنده تشخیص داده می‌شود و مصرف کاربر باد نمی‌کند.

## ۵. مقیاس‌پذیری

| محور | راهکار |
|---|---|
| پنل | بی‌حالت — هر تعداد نمونه؛ قفل advisory یک رهبر انتخاب می‌کند |
| دیتابیس | PostgreSQL؛ تنظیم استخر با `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` |
| نودها | بی‌نهایت؛ `Plan.node_group` و `Node.tags` مسیریابی منطقه‌ای را انجام می‌دهند |
| محدودیت نرخ | برای بیش از دو نمونه، `core/ratelimit.py` را با Redis جایگزین کنید |
| ربات | حالت webhook با وب‌سرویس مقیاس می‌گیرد؛ ورکر polling لازم نیست |

## ۶. نقاط توسعه

* **پروتکل جدید** — یک سازنده در `services/xray_links.py`، یک رندرر در
  `node-agent/agent/xray_manager.py` و یک عضو enum در `db/models.py`.
* **درگاه پرداخت جدید** — یک verifier در `services/billing.py` بنویسید و
  `fulfil_payment()` را صدا بزنید؛ روتر را در `api/v1/` اضافه کنید.
* **کانال اطلاع‌رسانی جدید** — `alerts.dispatch_alert()` را گسترش دهید.
* **صفحه‌ی جدید پنل** — یک `<section class="page">` در `static/index.html` و
  یک تابع `Pages.<name>` در `static/app.js`.
